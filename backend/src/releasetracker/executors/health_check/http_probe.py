"""HTTP probe.

Evaluates one HTTP request per service per attempt. Grouped modes probe
every resolved service and only report healthy when every per-service
attempt independently passes status + body checks.
"""

from __future__ import annotations

import asyncio
import errno
import json
import sys
from typing import TYPE_CHECKING, Any

from ...services.outbound_http import (
    OutboundConnectError,
    OutboundDNSFailure,
    OutboundHTTPClient,
    OutboundHTTPError,
    OutboundRedirectRejected,
    OutboundTLSFailure,
    OutboundTimeout,
    OutboundTimeouts,
    OutboundURLRejected,
)
from .host_resolver import ProbeHost, resolve_probe_hosts
from .probe import HealthCheckProbe
from .types import ProbeAttemptResult, ProbeErrorCategory

if TYPE_CHECKING:
    from .types import HealthCheckContext


# Request body cap. Anything beyond this size is truncated in-place; the
# regex still runs against the truncated bytes but the attempt result
# carries ``body_truncated=True`` so operators know.
_BODY_READ_CAP_BYTES = 65_536
_REGEX_INPUT_CAP_CHARS = 8_192
_REGEX_TIMEOUT_SECONDS = 0.2
_REGEX_WORKER_CODE = """
import json
import re
import sys

pattern, body = json.loads(sys.stdin.buffer.read())
sys.stdout.buffer.write(b"1" if re.search(pattern, body) is not None else b"0")
"""


class HTTPProbe(HealthCheckProbe):
    def __init__(self, *, manual: bool = False) -> None:
        self._manual = manual

    async def attempt(self, ctx: "HealthCheckContext") -> ProbeAttemptResult:
        profile = ctx.executor_config.health_check
        http_cfg = profile.http
        if http_cfg is None:
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error="health_check.http sub-object is missing at runtime",
            )

        if self._manual:
            if not http_cfg.host:
                return ProbeAttemptResult(
                    healthy=False,
                    error_category="host_unresolvable",
                    last_error="health_check.http.host is required for manual HTTP probes",
                )
            hosts = [ProbeHost(service=None, host=http_cfg.host, port=http_cfg.port)]
        else:
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
        expected_body_regex = http_cfg.expected_body_regex

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
            aggregate_detail["http"].append({"service": host.service, **svc_result.detail})
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
        expected_body_regex: str | None,
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
        client = OutboundHTTPClient(
            timeouts=OutboundTimeouts(
                connect=min(5.0, timeout_seconds),
                read=min(5.0, timeout_seconds),
                write=min(5.0, timeout_seconds),
                total=float(timeout_seconds),
            ),
            max_response_bytes=_BODY_READ_CAP_BYTES,
        )

        try:
            response = await client.request(
                http_cfg.method,
                url,
                headers=headers,
                verify_tls=verify,
            )
        except OutboundTimeout:
            return ProbeAttemptResult(
                healthy=False,
                error_category="timeout",
                detail={"host": host.host, "port": port},
                last_error=f"attempt exceeded {timeout_seconds}s timeout",
            )
        except OutboundDNSFailure:
            return ProbeAttemptResult(
                healthy=False,
                error_category="dns_failure",
                detail={"host": host.host, "port": port},
                last_error="destination DNS resolution failed",
            )
        except OutboundTLSFailure:
            return ProbeAttemptResult(
                healthy=False,
                error_category="tls_error",
                detail={"host": host.host, "port": port},
                last_error="TLS connection failed",
            )
        except OutboundURLRejected as exc:
            return ProbeAttemptResult(
                healthy=False,
                error_category="host_unresolvable",
                detail={"host": host.host, "port": port, "outbound_policy_blocked": True},
                last_error=str(exc),
            )
        except OutboundRedirectRejected as exc:
            return ProbeAttemptResult(
                healthy=False,
                error_category="other",
                detail={"host": host.host, "port": port, "redirect_blocked": True},
                last_error=str(exc),
            )
        except OutboundConnectError as exc:
            category = _classify_connect_error(exc)
            return ProbeAttemptResult(
                healthy=False,
                error_category=category,
                detail={"host": host.host, "port": port},
                last_error="connection to validated destination failed",
            )
        except OutboundHTTPError as exc:
            return ProbeAttemptResult(
                healthy=False,
                error_category="other",
                detail={"host": host.host, "port": port},
                last_error=str(exc),
            )

        status_ok = _status_matches(response.status_code, expected_status_codes)
        body_text = response.body.decode("utf-8", errors="replace")
        regex_input = body_text[:_REGEX_INPUT_CAP_CHARS]
        regex_input_truncated = len(body_text) > _REGEX_INPUT_CAP_CHARS
        body_ok = True
        regex_timed_out = False
        if expected_body_regex is not None:
            try:
                body_ok = await _safe_regex_search(expected_body_regex, regex_input)
            except TimeoutError:
                body_ok = False
                regex_timed_out = True

        detail: dict[str, Any] = {
            "host": host.host,
            "port": port,
            "http_last_status": response.status_code,
            "matched": status_ok and body_ok,
            "body_truncated": response.body_truncated,
            "body_regex_input_truncated": regex_input_truncated,
            "body_regex_timed_out": regex_timed_out,
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
            last_error=(
                "body regex exceeded its execution limit"
                if regex_timed_out
                else f"body did not match {expected_body_regex!r}"
            ),
        )


def _bracket_ipv6(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _status_matches(status_code: int, expected: list[int] | None) -> bool:
    if expected is None:
        return 200 <= status_code < 400
    return status_code in expected


def _classify_connect_error(exc: OutboundConnectError) -> ProbeErrorCategory:
    cause = exc.__cause__
    while cause is not None:
        if isinstance(cause, ConnectionRefusedError):
            return "connection_refused"
        if isinstance(cause, OSError) and cause.errno == errno.ECONNREFUSED:
            return "connection_refused"
        cause = cause.__cause__ or cause.__context__
    return "network_unreachable"


async def _safe_regex_search(pattern: str, body: str) -> bool:
    """Run matching in a disposable subprocess that can be killed on timeout."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-c",
        _REGEX_WORKER_CODE,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    payload = json.dumps([pattern, body], ensure_ascii=False).encode("utf-8")
    try:
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(payload),
            timeout=_REGEX_TIMEOUT_SECONDS,
        )
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    return process.returncode == 0 and stdout == b"1"


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
