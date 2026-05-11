"""TCP probe.

Performs one TCP connect + immediate close per service per attempt. No
application bytes are exchanged — the goal is to confirm the service
accepts connections, not to speak any protocol.
"""

from __future__ import annotations

import asyncio
import errno
import socket
from typing import TYPE_CHECKING, Any

from .host_resolver import ProbeHost, resolve_probe_hosts
from .probe import HealthCheckProbe
from .types import ProbeAttemptResult, ProbeErrorCategory

if TYPE_CHECKING:
    from .types import HealthCheckContext


# Handshake-complete → close budget.
_POST_HANDSHAKE_CLOSE_BUDGET_SECONDS = 1


class TCPProbe(HealthCheckProbe):
    def __init__(self, *, manual: bool = False) -> None:
        self._manual = manual

    async def attempt(self, ctx: "HealthCheckContext") -> ProbeAttemptResult:
        profile = ctx.executor_config.health_check
        tcp_cfg = profile.tcp
        if tcp_cfg is None:
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error="health_check.tcp sub-object is missing at runtime",
            )

        if self._manual:
            if not tcp_cfg.host:
                return ProbeAttemptResult(
                    healthy=False,
                    error_category="host_unresolvable",
                    last_error="health_check.tcp.host is required for manual TCP probes",
                )
            hosts = [ProbeHost(service=None, host=tcp_cfg.host, port=tcp_cfg.port)]
        else:
            try:
                hosts = await resolve_probe_hosts(
                    ctx.adapter,
                    ctx.executor_config.target_ref,
                    services=list(profile.services) if profile.services else None,
                    default_port=tcp_cfg.port,
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
        aggregate_detail: dict[str, Any] = {"tcp": []}
        overall_healthy = True
        aggregate_last_error: str | None = None
        aggregate_category: ProbeErrorCategory = "ok"
        per_attempt_timeout = max(1, int(profile.attempt_timeout_seconds))

        for host in hosts:
            svc_result = await self._probe_single(
                host=host,
                default_port=tcp_cfg.port,
                timeout_seconds=per_attempt_timeout,
            )
            if host.service is not None:
                per_service[host.service] = svc_result
            aggregate_detail["tcp"].append(
                {"service": host.service, **svc_result.detail}
            )
            if not svc_result.healthy:
                overall_healthy = False
                if aggregate_last_error is None:
                    prefix = f"{host.service}: " if host.service else ""
                    aggregate_last_error = f"{prefix}{svc_result.last_error or 'unhealthy'}"
                if aggregate_category == "ok":
                    aggregate_category = svc_result.error_category

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
        default_port: int | None,
        timeout_seconds: int,
    ) -> ProbeAttemptResult:
        port = host.port if host.port is not None else default_port
        if port is None:
            return ProbeAttemptResult(
                healthy=False,
                error_category="host_unresolvable",
                detail={"host": host.host},
                last_error="no port resolved for TCP probe",
            )

        writer: asyncio.StreamWriter | None = None
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host.host, port),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            return ProbeAttemptResult(
                healthy=False,
                error_category="timeout",
                detail={"host": host.host, "port": port},
                last_error=f"connect exceeded {timeout_seconds}s timeout",
            )
        except ConnectionRefusedError:
            return ProbeAttemptResult(
                healthy=False,
                error_category="connection_refused",
                detail={"host": host.host, "port": port},
                last_error="connection refused",
            )
        except socket.gaierror as exc:
            return ProbeAttemptResult(
                healthy=False,
                error_category="dns_failure",
                detail={"host": host.host, "port": port},
                last_error=f"dns failure: {exc}",
            )
        except OSError as exc:
            category: ProbeErrorCategory = "network_unreachable"
            if exc.errno == errno.ECONNRESET:
                category = "connection_refused"
            return ProbeAttemptResult(
                healthy=False,
                error_category=category,
                detail={"host": host.host, "port": port, "errno": exc.errno},
                last_error=f"{exc.__class__.__name__}: {exc}",
            )
        except Exception as exc:
            return ProbeAttemptResult(
                healthy=False,
                error_category="other",
                detail={"host": host.host, "port": port},
                last_error=f"unexpected tcp error: {exc}",
            )

        try:
            writer.close()
            await asyncio.wait_for(
                writer.wait_closed(), timeout=_POST_HANDSHAKE_CLOSE_BUDGET_SECONDS
            )
        except Exception:
            # Close failures do not impact the health verdict — the
            # handshake already succeeded.
            pass

        return ProbeAttemptResult(
            healthy=True,
            detail={"host": host.host, "port": port},
        )


__all__ = ["TCPProbe"]
