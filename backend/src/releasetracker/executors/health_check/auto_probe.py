"""Auto health probe strategy.

Auto mode keeps the operator-facing configuration simple while choosing the
least surprising runtime-specific check:

- Docker / Podman prefer configured runtime HEALTHCHECK status.
- Without a runtime HEALTHCHECK, Docker / Podman probe published host ports
  from the ReleaseTracker backend process.
- If there is no published host-port path, Docker / Podman fall back to the
  runtime-native running/restart check.
- Kubernetes / Portainer do not perform host-port probing; they only use the
  runtime/API-native check exposed by their adapter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .probe import HealthCheckProbe, RuntimeNativeProbe
from .tcp_probe import TCPProbe
from .types import ProbeAttemptResult, ProbeErrorCategory

if TYPE_CHECKING:
    from .host_resolver import ProbeHost
    from .types import HealthCheckContext


_HOST_PORT_RUNTIME_TYPES = {"docker", "podman"}


class AutoProbe(HealthCheckProbe):
    """Runtime-aware health probe for the user-facing ``auto`` strategy."""

    async def attempt(self, ctx: "HealthCheckContext") -> ProbeAttemptResult:
        runtime_type = getattr(ctx.executor_config, "runtime_type", None)
        services = _profile_services(ctx)

        if runtime_type not in _HOST_PORT_RUNTIME_TYPES:
            return await RuntimeNativeProbe().attempt(ctx)

        try:
            has_native_healthcheck = await ctx.adapter.has_runtime_native_healthcheck(
                ctx.executor_config.target_ref,
                services=services,
            )
        except Exception:
            has_native_healthcheck = False

        if has_native_healthcheck:
            result = await RuntimeNativeProbe().attempt(ctx)
            return _with_auto_selection(result, "runtime_native_healthcheck")

        try:
            hosts = await ctx.adapter.resolve_auto_probe_hosts(
                ctx.executor_config.target_ref,
                services=services,
                default_port=None,
            )
        except NotImplementedError:
            result = await RuntimeNativeProbe().attempt(ctx)
            return _with_auto_selection(result, "runtime_native_fallback")
        except ValueError as exc:
            if _should_fallback_to_runtime_native(str(exc)):
                result = await RuntimeNativeProbe().attempt(ctx)
                return _with_auto_selection(
                    result,
                    "runtime_native_fallback",
                    auto_error=str(exc),
                )
            return ProbeAttemptResult(
                healthy=False,
                error_category="host_unresolvable",
                detail={"auto": {"selected": "host_port_tcp", "error": str(exc)}},
                last_error=str(exc),
            )
        except Exception as exc:  # pragma: no cover - defensive
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                detail={"auto": {"selected": "host_port_tcp"}},
                last_error=f"auto host-port resolution raised: {exc}",
            )

        result = await _probe_tcp_hosts(ctx, hosts)
        return _with_auto_selection(result, "host_port_tcp")


async def _probe_tcp_hosts(
    ctx: "HealthCheckContext",
    hosts: list["ProbeHost"],
) -> ProbeAttemptResult:
    profile = ctx.executor_config.health_check
    per_service: dict[str, ProbeAttemptResult] = {}
    aggregate_detail: dict[str, Any] = {"tcp": []}
    overall_healthy = True
    aggregate_last_error: str | None = None
    aggregate_category: ProbeErrorCategory = "ok"
    per_attempt_timeout = max(1, int(profile.attempt_timeout_seconds))
    tcp_probe = TCPProbe()

    for host in hosts:
        svc_result = await tcp_probe._probe_single(
            host=host,
            default_port=host.port,
            timeout_seconds=per_attempt_timeout,
        )
        if host.service is not None:
            per_service[host.service] = svc_result
        aggregate_detail["tcp"].append({"service": host.service, **svc_result.detail})
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


def _profile_services(ctx: "HealthCheckContext") -> list[str] | None:
    profile = ctx.executor_config.health_check
    return list(profile.services) if profile.services else None


def _should_fallback_to_runtime_native(message: str) -> bool:
    lowered = message.lower()
    return "no published host port" in lowered or "no compose services" in lowered


def _with_auto_selection(
    result: ProbeAttemptResult,
    selected: str,
    *,
    auto_error: str | None = None,
) -> ProbeAttemptResult:
    auto_detail: dict[str, Any] = {"selected": selected}
    if auto_error:
        auto_detail["fallback_reason"] = auto_error
    detail = {"auto": auto_detail, **dict(result.detail)}
    return ProbeAttemptResult(
        healthy=result.healthy,
        error_category=result.error_category,
        detail=detail,
        last_error=result.last_error,
        per_service=result.per_service,
        terminate_phase=result.terminate_phase,
    )


__all__ = ["AutoProbe"]
