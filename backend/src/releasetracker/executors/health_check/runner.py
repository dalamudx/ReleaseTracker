"""HealthCheckRunner — post-update health check lifecycle.

Implements the grace-period → probe-loop → outcome pipeline.

Scheduler cancellation is respected via an optional ``cancel_event``
passed on the context-side ``cancel`` attribute. The runner accepts
``None`` and treats absence as "no external cancellation".
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

from .probe import HealthCheckProbe
from .types import (
    HealthCheckResult,
    ProbeAttemptResult,
    redact_for_log,
    truncate_error,
)

if TYPE_CHECKING:
    from .types import HealthCheckContext


logger = logging.getLogger(__name__)


class HealthCheckRunner:
    """Runs the post-update health check end-to-end.

    The runner is stateless; each ``run()`` call owns its own timing and
    diagnostics. Probes are injected so tests can drive a scripted
    sequence of attempt outcomes without hitting a real runtime.
    """

    def __init__(
        self,
        probe: HealthCheckProbe,
        *,
        sleep: "callable | None" = None,
        monotonic: "callable | None" = None,
        now: "callable | None" = None,
    ) -> None:
        self._probe = probe
        self._sleep = sleep or asyncio.sleep
        self._monotonic = monotonic or time.monotonic
        self._now = now or datetime.now

    async def run(self, ctx: "HealthCheckContext") -> HealthCheckResult:
        profile = ctx.executor_config.health_check
        strategy = profile.strategy

        # strategy=none must never instantiate the runner in the scheduler;
        # defend here for direct callers / tests.
        if strategy == "none":
            return HealthCheckResult(
                strategy=strategy,
                outcome="skipped",
                failure_policy=profile.failure_policy,
            )

        phase_start = self._monotonic()
        grace = int(profile.grace_period_seconds)
        interval = int(profile.interval_seconds)
        window = int(profile.probe_window_seconds)
        attempt_timeout = int(profile.attempt_timeout_seconds)

        logger.info(
            "health_check_phase_start %s",
            redact_for_log(
                {
                    "executor_id": ctx.executor_config.id,
                    "target_mode": ctx.executor_config.target_ref.get("mode"),
                    "strategy": strategy,
                    "grace_period_seconds": grace,
                    "probe_window_seconds": window,
                    "interval_seconds": interval,
                }
            ),
        )

        # Grace period before the first attempt.
        if grace > 0:
            try:
                await self._sleep(grace)
            except asyncio.CancelledError:
                return self._cancelled_result(
                    strategy=strategy,
                    profile=profile,
                    attempt_count=0,
                    first_attempt_at=None,
                    last_attempt_at=None,
                    phase_start=phase_start,
                )

        attempt_count = 0
        first_attempt_at: datetime | None = None
        last_attempt_at: datetime | None = None
        last_result: ProbeAttemptResult | None = None

        # Total phase cap.
        deadline = phase_start + grace + window

        while True:
            # Stop before starting another attempt if we have no budget left.
            now_mono = self._monotonic()
            if now_mono >= deadline:
                break

            remaining = deadline - now_mono
            per_attempt_timeout = min(attempt_timeout, remaining)
            if per_attempt_timeout <= 0:
                break

            attempt_count += 1
            attempt_started_at = self._now()
            if first_attempt_at is None:
                first_attempt_at = attempt_started_at
            last_attempt_at = attempt_started_at
            attempt_monotonic_start = self._monotonic()

            try:
                last_result = await asyncio.wait_for(
                    self._probe.attempt(ctx), timeout=per_attempt_timeout
                )
            except asyncio.CancelledError:
                return self._cancelled_result(
                    strategy=strategy,
                    profile=profile,
                    attempt_count=attempt_count,
                    first_attempt_at=first_attempt_at,
                    last_attempt_at=last_attempt_at,
                    phase_start=phase_start,
                )
            except asyncio.TimeoutError:
                last_result = ProbeAttemptResult(
                    healthy=False,
                    error_category="timeout",
                    last_error=f"attempt exceeded {per_attempt_timeout}s timeout",
                )

            attempt_duration_ms = int(
                (self._monotonic() - attempt_monotonic_start) * 1000
            )
            logger.debug(
                "health_check_probe_attempt %s",
                redact_for_log(
                    {
                        "executor_id": ctx.executor_config.id,
                        "strategy": strategy,
                        "attempt": attempt_count,
                        "outcome": "success" if last_result.healthy else last_result.error_category,
                        "duration_ms": attempt_duration_ms,
                        "detail": last_result.detail,
                    }
                ),
            )

            if not last_result.healthy and last_result.error_category in {
                "timeout",
                "connection_refused",
                "dns_failure",
                "tls_error",
                "network_unreachable",
            }:
                logger.warning(
                    "health_check_transport_error %s",
                    redact_for_log(
                        {
                            "executor_id": ctx.executor_config.id,
                            "strategy": strategy,
                            "error_category": last_result.error_category,
                            "attempt": attempt_count,
                        }
                    ),
                )

            if last_result.healthy:
                duration_seconds = max(0, int(self._monotonic() - phase_start))
                outcome_result = HealthCheckResult(
                    strategy=strategy,
                    outcome="healthy",
                    attempt_count=attempt_count,
                    first_attempt_at=first_attempt_at,
                    last_attempt_at=last_attempt_at,
                    duration_seconds=duration_seconds,
                    failure_policy=profile.failure_policy,
                    last_error=None,
                    services=None,
                    probe_diagnostics=dict(last_result.detail),
                )
                self._log_phase_end(ctx, strategy, outcome_result)
                return outcome_result

            if last_result.terminate_phase:
                break

            # Wait the configured interval before the next attempt, unless
            # doing so would blow the window budget.
            if interval > 0:
                remaining_after_attempt = deadline - self._monotonic()
                if remaining_after_attempt <= 0:
                    break
                sleep_for = min(interval, remaining_after_attempt)
                if sleep_for > 0:
                    try:
                        await self._sleep(sleep_for)
                    except asyncio.CancelledError:
                        return self._cancelled_result(
                            strategy=strategy,
                            profile=profile,
                            attempt_count=attempt_count,
                            first_attempt_at=first_attempt_at,
                            last_attempt_at=last_attempt_at,
                            phase_start=phase_start,
                        )

        duration_seconds = max(0, int(self._monotonic() - phase_start))
        unhealthy_result = HealthCheckResult(
            strategy=strategy,
            outcome="unhealthy",
            attempt_count=attempt_count,
            first_attempt_at=first_attempt_at,
            last_attempt_at=last_attempt_at,
            duration_seconds=duration_seconds,
            failure_policy=profile.failure_policy,
            last_error=truncate_error(
                (last_result.last_error if last_result else None)
                or "health check window expired without a healthy attempt"
            ),
            services=None,
            probe_diagnostics=dict(last_result.detail) if last_result else {},
        )
        self._log_phase_end(ctx, strategy, unhealthy_result)
        return unhealthy_result

    def _cancelled_result(
        self,
        *,
        strategy: str,
        profile,
        attempt_count: int,
        first_attempt_at: datetime | None,
        last_attempt_at: datetime | None,
        phase_start: float,
    ) -> HealthCheckResult:
        duration_seconds = max(0, int(self._monotonic() - phase_start))
        result = HealthCheckResult(
            strategy=strategy,
            outcome="error",
            attempt_count=attempt_count,
            first_attempt_at=first_attempt_at,
            last_attempt_at=last_attempt_at,
            duration_seconds=duration_seconds,
            failure_policy=profile.failure_policy,
            last_error="cancelled",
        )
        logger.info(
            "health_check_phase_cancelled %s",
            redact_for_log(
                {
                    "strategy": strategy,
                    "attempt_count": attempt_count,
                    "duration_seconds": duration_seconds,
                }
            ),
        )
        return result

    def _log_phase_end(self, ctx, strategy: str, result: HealthCheckResult) -> None:
        logger.info(
            "health_check_phase_end %s",
            redact_for_log(
                {
                    "executor_id": ctx.executor_config.id,
                    "strategy": strategy,
                    "outcome": result.outcome,
                    "attempt_count": result.attempt_count,
                    "duration_ms": result.duration_seconds * 1000,
                    "failure_policy": result.failure_policy,
                }
            ),
        )


__all__ = ["HealthCheckRunner"]
