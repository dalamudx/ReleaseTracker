"""Abstract HealthCheckProbe + RuntimeNativeProbe implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from .types import ProbeAttemptResult

if TYPE_CHECKING:
    from .types import HealthCheckContext


class HealthCheckProbe(ABC):
    """One strategy implementation, evaluated per attempt by the runner."""

    @abstractmethod
    async def attempt(self, ctx: "HealthCheckContext") -> ProbeAttemptResult:
        """Evaluate the probe once and return the outcome."""


class RuntimeNativeProbe(HealthCheckProbe):
    """Delegates to the runtime adapter's ``probe_runtime_native_health``.

    The actual readiness semantics live on each adapter (container /
    kubernetes / portainer) so the probe can stay thin and
    per-runtime-agnostic. Adapters raising ``NotImplementedError`` surface
    as a ``runtime_api_error`` so the runner retries on the next interval.
    """

    async def attempt(self, ctx: "HealthCheckContext") -> ProbeAttemptResult:
        profile = ctx.executor_config.health_check
        services = list(profile.services) if profile.services else None
        try:
            result = await ctx.adapter.probe_runtime_native_health(
                ctx.executor_config.target_ref,
                baseline=ctx.baseline,
                services=services,
            )
        except NotImplementedError as exc:
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error=f"runtime adapter does not support runtime_native probing: {exc}",
            )
        except Exception as exc:
            return ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error=f"runtime_native probe raised: {exc}",
            )

        # Adapters return ProbeAttemptResult by contract but defend against
        # third-party adapters emitting plain dicts or tuples.
        if isinstance(result, ProbeAttemptResult):
            return result
        return ProbeAttemptResult(
            healthy=False,
            error_category="runtime_api_error",
            last_error=f"adapter returned unexpected result: {type(result).__name__}",
        )


__all__ = ["HealthCheckProbe", "RuntimeNativeProbe"]
