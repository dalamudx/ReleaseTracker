"""HTTP probe.

Evaluates one HTTP request per service per attempt. Grouped modes probe
every resolved service and only report healthy when every per-service
attempt independently passes status + body checks.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import httpx

from .host_resolver import ProbeHost, resolve_probe_hosts
from .probe import HealthCheckProbe
from .types import ProbeAttemptResult, ProbeErrorCategory

if TYPE_CHECKING:
    from .types import HealthCheckContext


# Request body cap. Anything beyond this size is truncated in-place; the
# regex still runs against the truncated bytes but the attempt result
# carries ``body_truncated=True`` so operators know.
_BODY_READ_CAP_BYTES = 65_536


class HTTPProbe(HealthCheckProbe):
    async def attempt(self, ctx: "HealthCheckContext") -> ProbeAttemptResult:
        profile = ctx.executor_config.health_check
        http_cfg = profile.http
        if http_cfg is None:
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error="health_check.http sub-object is missing at runtime",
            )

        try:
            hosts = await resolve_probe_hosts(
                ctx.adapter,
                ctx.executor_config.target_ref,
                services=list(profile.services) if profile.services else None,
                default_port=http_cfg.port,
            )
        except NotImplementedError as exc:
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error=f"host resolver not available: {exc}",
            )
        except ValueError as exc:
            return ProbeAttemptResult(
                healthy=False,
                error_category="host_unresolvable",
                last_error=str(exc),
            )
        except Exception as exc:  # pragma: no cover - defensive
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error=f"host resolution raised: {exc}",
            )

        per_service: dict[str, ProbeAttemptResult] = {}
        aggregate_detail: dict[str, Any] = {"http": []}
        overall_healthy = True
        aggregate_last_error: str | None = None
        aggregate_category: ProbeErrorCategory = "ok"
        expected_status_codes = http_cfg.expected_status_codes
        expected_body_regex = (
            re.compile(http_cfg.expected_body_regex)
            if http_cfg.expected_body_regex
            else None
        )

        per_attempt_timeout = max(1, int(profile.attempt_timeout_seconds))

        for host in hosts:
            svc_result = await self._probe_single(
                host=host,
                http_cfg=http_cfg,
                timeout_seconds=per_attempt_timeout,
                expected_status_codes=expected_status_codes,
                expected_body_regex=expected_body_regex,
            )
            if host.service is not None:
                per_service[host.service] = svc_result
            aggregate_detail["http"].append(
                {"service": host.service, **svc_result.detail}
            )
            if not svc_result.healthy:
                overall_healthy = False
                if aggregate_last_error is None:
                    prefix = f"{host.service}: " if host.service else ""
                    aggregate_last_error = f"{prefix}{svc_result.last_error or 'unhealthy'}"
                # First non-ok category wins for the aggregate so the
                # runner's WARN filter sees the right bucket. Per-service
                # rollup below refines when multiple services disagree.
                if aggregate_category == "ok":
                    aggregate_category = svc_result.error_category

        if not overall_healthy and per_service:
            aggregate_category = _pick_aggregate_category(per_service)

        return ProbeAttemptResult(
            healthy=overall_healthy,
            error_category="ok" if overall_healthy else aggregate_category,
            detail=aggregate_detail,
            last_error=None if overall_healthy else aggregate_last_error,
            per_service=per_service or None,
        )

    async def _probe_single(
        self,
        *,
        host: ProbeHost,
        http_cfg,
        timeout_seconds: int,
        expected_status_codes: list[int] | None,
        expected_body_regex: re.Pattern | None,
    ) -> ProbeAttemptResult:
        port = host.port if host.port is not None else http_cfg.port
        if port is None:
            return ProbeAttemptResult(
                healthy=False,
                error_category="host_unresolvable",
                detail={"host": host.host},
                last_error="no port resolved for HTTP probe",
            )

        url = f"{http_cfg.scheme}://{_bracket_ipv6(host.host)}:{port}{http_cfg.path}"
        headers = dict(http_cfg.headers or {})
        verify = not http_cfg.tls_skip_verify

        try:
            async with httpx.AsyncClient(verify=verify, timeout=timeout_seconds) as client:
                response = await client.request(
                    http_cfg.method,
                    url,
                    headers=headers,
                )
                body_bytes, truncated = await _collect_body(response)
        except httpx.TimeoutException:
            return ProbeAttemptResult(
                healthy=False,
                error_category="timeout",
                detail={"host": host.host, "port": port, "url": url},
                last_error=f"attempt exceeded {timeout_seconds}s timeout",
            )
        except httpx.ConnectError as exc:
            category = _classify_connect_error(exc)
            return ProbeAttemptResult(
                healthy=False,
                error_category=category,
                detail={"host": host.host, "port": port, "url": url},
                last_error=f"connect error: {exc}",
            )
        except Exception as exc:
            return ProbeAttemptResult(
                healthy=False,
                error_category="other",
                detail={"host": host.host, "port": port, "url": url},
                last_error=f"request failed: {exc}",
            )

        status_ok = _status_matches(response.status_code, expected_status_codes)
        body_text = body_bytes.decode("utf-8", errors="replace")
        body_ok = True
        if expected_body_regex is not None:
            body_ok = expected_body_regex.search(body_text) is not None

        detail: dict[str, Any] = {
            "host": host.host,
            "port": port,
            "url": url,
            "http_last_status": response.status_code,
            "matched": status_ok and body_ok,
            "body_truncated": truncated,
        }
        if status_ok and body_ok:
            return ProbeAttemptResult(healthy=True, detail=detail)

        if not status_ok:
            return ProbeAttemptResult(
                healthy=False,
                error_category="status_mismatch",
                detail=detail,
                last_error=(
                    f"status {response.status_code} not in expected set "
                    f"{expected_status_codes or 'range 200..399'}"
                ),
            )
        return ProbeAttemptResult(
            healthy=False,
            error_category="body_mismatch",
            detail=detail,
            last_error=f"body did not match {expected_body_regex.pattern!r}",
        )


def _bracket_ipv6(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _status_matches(status_code: int, expected: list[int] | None) -> bool:
    if expected is None:
        return 200 <= status_code < 400
    return status_code in expected


def _classify_connect_error(exc: httpx.ConnectError) -> ProbeErrorCategory:
    """Best-effort classification of ``httpx.ConnectError`` causes.

    The library does not expose a typed error taxonomy so we inspect the
    cause chain. Anything we cannot classify falls through to
    ``network_unreachable`` which is the least specific transport bucket.
    """
    cause = exc.__cause__ or exc.__context__
    message = f"{exc} {cause}" if cause else str(exc)
    lowered = message.lower()
    if "name or service not known" in lowered or "nodename nor servname" in lowered:
        return "dns_failure"
    if "gaierror" in lowered:
        return "dns_failure"
    if "connection refused" in lowered:
        return "connection_refused"
    if "ssl" in lowered or "tls" in lowered or "certificate" in lowered:
        return "tls_error"
    if "network is unreachable" in lowered or "no route to host" in lowered:
        return "network_unreachable"
    return "network_unreachable"


async def _collect_body(response: httpx.Response) -> tuple[bytes, bool]:
    """Read the response body up to the cap; return (body, truncated)."""
    chunks: list[bytes] = []
    total = 0
    truncated = False
    async for chunk in response.aiter_bytes():
        if total + len(chunk) >= _BODY_READ_CAP_BYTES:
            remaining = _BODY_READ_CAP_BYTES - total
            if remaining > 0:
                chunks.append(chunk[:remaining])
                total += remaining
            truncated = True
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), truncated


def _pick_aggregate_category(
    per_service: dict[str, ProbeAttemptResult],
) -> ProbeErrorCategory:
    """Choose a reasonable aggregate error category from per-service results.

    Bubble up the first non-``ok`` category in a stable order. This keeps
    the runner's transport-error WARN log accurate when one service
    hits a timeout while another returns 503.
    """
    priority: list[ProbeErrorCategory] = [
        "timeout",
        "connection_refused",
        "dns_failure",
        "tls_error",
        "network_unreachable",
        "host_unresolvable",
        "status_mismatch",
        "body_mismatch",
        "runtime_api_error",
        "other",
    ]
    observed = {res.error_category for res in per_service.values() if not res.healthy}
    for category in priority:
        if category in observed:
            return category
    return "other"


__all__ = ["HTTPProbe"]
