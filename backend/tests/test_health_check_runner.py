"""HealthCheckRunner lifecycle tests (Req 7.5-7.11, 13.1-13.4)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import pytest

from releasetracker.config import ExecutorConfig, HealthCheckProfile, RuntimeConnectionConfig
from releasetracker.executors.health_check.probe import HealthCheckProbe
from releasetracker.executors.health_check.runner import HealthCheckRunner
from releasetracker.executors.health_check.types import HealthCheckContext, ProbeAttemptResult


@dataclass
class _ScriptedProbe(HealthCheckProbe):
    """Yields canned ``ProbeAttemptResult``s in order, optionally sleeping.

    Each entry is ``(result, sleep_before_return)``; the sleep is awaited
    before returning so tests can verify per-attempt timeout handling.
    """

    script: list[tuple[ProbeAttemptResult, float]] = field(default_factory=list)
    calls: int = 0

    async def attempt(self, ctx: HealthCheckContext) -> ProbeAttemptResult:
        if self.calls >= len(self.script):
            raise AssertionError("probe called more often than scripted")
        result, delay = self.script[self.calls]
        self.calls += 1
        if delay > 0:
            await asyncio.sleep(delay)
        return result


class _FakeClock:
    """Monotonic clock that advances only when the code under test asks for sleep."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        # Yield to the event loop so scheduled tasks can progress, then
        # advance the virtual clock. This lets ``asyncio.wait_for`` still
        # exercise real cancellation semantics inside probe attempts.
        await asyncio.sleep(0)
        self.now += seconds


def _executor(
    profile: HealthCheckProfile,
    *,
    target_ref: dict | None = None,
    runtime_type: str = "docker",
) -> ExecutorConfig:
    return ExecutorConfig(
        id=42,
        name="runner-executor",
        runtime_type=runtime_type,
        runtime_connection_id=1,
        tracker_name="tracker",
        tracker_source_id=1,
        channel_name="stable",
        enabled=True,
        update_mode="manual",
        target_ref=target_ref
        or {"mode": "container", "container_id": "c1", "container_name": "api"},
        health_check=profile,
    )


def _context(
    profile: HealthCheckProfile,
    *,
    target_ref: dict | None = None,
    runtime_type: str = "docker",
) -> HealthCheckContext:
    runtime_conn = RuntimeConnectionConfig(
        id=1,
        name=f"{runtime_type}-local",
        type=runtime_type,
        enabled=True,
        config=(
            {"socket": "unix:///var/run/docker.sock"}
            if runtime_type in {"docker", "podman"}
            else {"in_cluster": True}
        ),
        secrets={"token": "x"} if runtime_type in {"docker", "podman"} else {},
    )
    return HealthCheckContext(
        executor_config=_executor(profile, target_ref=target_ref, runtime_type=runtime_type),
        adapter=runtime_conn,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 8, 12, 0, 0),
        baseline={},
    )


def _build_runner(probe: HealthCheckProbe, *, clock: _FakeClock) -> HealthCheckRunner:
    return HealthCheckRunner(
        probe,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        now=lambda: datetime(2026, 5, 8, 12, 0, 0),
    )


# ---- Tests ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_strategy_none_returns_skipped_without_invoking_probe():
    clock = _FakeClock()
    probe = _ScriptedProbe(script=[])
    runner = _build_runner(probe, clock=clock)
    profile = HealthCheckProfile()  # strategy=none by default

    result = await runner.run(_context(profile))

    assert result.outcome == "skipped"
    assert probe.calls == 0
    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_first_attempt_healthy_returns_immediately_after_grace():
    clock = _FakeClock()
    probe = _ScriptedProbe(
        script=[(ProbeAttemptResult(healthy=True, detail={"health": "healthy"}), 0)]
    )
    runner = _build_runner(probe, clock=clock)
    profile = HealthCheckProfile(
        strategy="runtime_native",
        grace_period_seconds=3,
        attempt_timeout_seconds=5,
        interval_seconds=5,
        probe_window_seconds=60,
        failure_policy="mark_failed",
    )

    result = await runner.run(_context(profile))

    assert result.outcome == "healthy"
    assert result.attempt_count == 1
    assert probe.calls == 1
    assert clock.sleeps[0] == 3  # grace period awaited
    assert result.probe_diagnostics == {"health": "healthy"}


@pytest.mark.asyncio
async def test_unhealthy_then_healthy_retries_at_interval():
    clock = _FakeClock()
    probe = _ScriptedProbe(
        script=[
            (ProbeAttemptResult(healthy=False, error_category="status_mismatch", last_error="503"), 0),
            (ProbeAttemptResult(healthy=True, detail={"status": "ok"}), 0),
        ]
    )
    runner = _build_runner(probe, clock=clock)
    profile = HealthCheckProfile(
        strategy="runtime_native",
        grace_period_seconds=0,
        attempt_timeout_seconds=5,
        interval_seconds=7,
        probe_window_seconds=60,
        failure_policy="mark_failed",
    )

    result = await runner.run(_context(profile))

    assert result.outcome == "healthy"
    assert probe.calls == 2
    assert 7 in clock.sleeps


@pytest.mark.asyncio
async def test_probe_window_exhausts_returns_unhealthy():
    clock = _FakeClock()
    unhealthy = ProbeAttemptResult(
        healthy=False,
        error_category="status_mismatch",
        last_error="not ready",
    )
    probe = _ScriptedProbe(script=[(unhealthy, 0) for _ in range(10)])
    runner = _build_runner(probe, clock=clock)
    profile = HealthCheckProfile(
        strategy="runtime_native",
        grace_period_seconds=0,
        attempt_timeout_seconds=2,
        interval_seconds=5,
        probe_window_seconds=10,
        failure_policy="mark_failed",
    )

    result = await runner.run(_context(profile))

    assert result.outcome == "unhealthy"
    assert result.last_error == "not ready"
    # Window was 10s with 5s intervals -> at most 3 attempts fit.
    assert probe.calls >= 2
    assert probe.calls <= 4


@pytest.mark.asyncio
async def test_terminate_phase_short_circuits_window():
    clock = _FakeClock()
    probe = _ScriptedProbe(
        script=[
            (
                ProbeAttemptResult(
                    healthy=False,
                    error_category="helm_failed",
                    last_error="helm status failed",
                    terminate_phase=True,
                ),
                0,
            )
        ]
    )
    runner = _build_runner(probe, clock=clock)
    profile = HealthCheckProfile(
        strategy="helm_status",
        grace_period_seconds=0,
        attempt_timeout_seconds=5,
        interval_seconds=5,
        probe_window_seconds=60,
        failure_policy="mark_failed",
    )

    result = await runner.run(
        _context(
            profile,
            runtime_type="kubernetes",
            target_ref={
                "mode": "helm_release",
                "namespace": "prod",
                "release_name": "api",
            },
        )
    )

    assert result.outcome == "unhealthy"
    assert result.attempt_count == 1
    # No interval sleep happened — termination was immediate.
    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_per_attempt_timeout_is_classified_as_timeout():
    # Probe sleeps for longer than the configured per-attempt timeout; the
    # runner must cancel the attempt and classify it as a timeout. Uses
    # real ``time.monotonic`` so the window-exhaustion check advances and
    # the test terminates promptly instead of looping forever.
    import time as _time

    class _SlowProbe(HealthCheckProbe):
        calls = 0

        async def attempt(self, ctx):
            _SlowProbe.calls += 1
            await asyncio.sleep(5)  # real sleep, real cancellation
            return ProbeAttemptResult(healthy=True)

    runner = HealthCheckRunner(
        _SlowProbe(),
        sleep=asyncio.sleep,
        monotonic=_time.monotonic,
        now=lambda: datetime(2026, 5, 8, 12, 0, 0),
    )
    profile = HealthCheckProfile(
        strategy="runtime_native",
        grace_period_seconds=0,
        attempt_timeout_seconds=1,
        interval_seconds=1,
        probe_window_seconds=2,
        failure_policy="mark_failed",
    )

    result = await runner.run(_context(profile))

    assert result.outcome == "unhealthy"
    assert "timeout" in (result.last_error or "").lower()
