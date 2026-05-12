"""Scheduler-level integration tests for post-update health checks."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import pytest

from helpers.executor_runtime import save_docker_tracker_config, seed_docker_release
from releasetracker.config import (
    Channel,
    ExecutorConfig,
    HealthCheckProfile,
    RuntimeConnectionConfig,
)
from releasetracker.executor_scheduler import ExecutorScheduler
from releasetracker.executors.base import BaseRuntimeAdapter, RuntimeUpdateResult
from releasetracker.executors.health_check.types import ProbeAttemptResult
from releasetracker.models import Release


async def _create_runtime_connection(storage, *, name: str) -> int:
    return await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name=name,
            type="docker",
            enabled=True,
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={"token": "x"},
        )
    )


async def _create_tracker_source(storage, *, name: str) -> int:
    await save_docker_tracker_config(
        storage,
        name=name,
        image=name,
        channels=[Channel(name="stable", enabled=True, type="release")],
    )
    agg = await storage.get_aggregate_tracker(name)
    return agg.sources[0].id


async def _seed_release(storage, *, tracker_name: str, version: str) -> None:
    from datetime import timezone as _tz

    release_published_at = datetime(2026, 5, 8, 10, 0, 0, tzinfo=_tz.utc)
    await seed_docker_release(
        storage,
        tracker_name=tracker_name,
        version=version,
        prerelease=False,
        published_at=release_published_at,
    )
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    runtime_source = storage._select_runtime_source(aggregate_tracker)
    await storage.save_source_observations(
        aggregate_tracker.id,
        runtime_source,
        [
            Release(
                tracker_name=tracker_name,
                tracker_type="container",
                name=version,
                tag_name=version,
                version=version,
                published_at=release_published_at,
                url=f"https://example.com/{tracker_name}/{version}",
                channel_name="stable",
            )
        ],
    )


class _FakeAdapter(BaseRuntimeAdapter):
    """Adapter fake that lets tests script discovery, capture, update, and
    per-attempt health probe outcomes."""

    def __init__(
        self,
        runtime_connection,
        *,
        current_image: str,
        update_result: RuntimeUpdateResult,
        health_results: list[ProbeAttemptResult],
    ) -> None:
        super().__init__(runtime_connection)
        self._current_image = current_image
        self._update_result = update_result
        self._health_results = list(health_results)
        self.captured_snapshots: list[dict[str, Any]] = []

    async def discover_targets(self):
        return []

    async def validate_target_ref(self, target_ref):
        return None

    async def get_current_image(self, target_ref):
        return self._current_image

    async def capture_snapshot(self, target_ref, current_image):
        return {
            "runtime_type": self.runtime_connection.type,
            "container_id": target_ref.get("container_id", "c1"),
            "image": current_image,
        }

    async def validate_snapshot(self, target_ref, snapshot):
        return None

    async def update_image(self, target_ref, new_image):
        self.captured_snapshots.append({"to": new_image})
        return self._update_result

    async def probe_runtime_native_health(self, target_ref, *, baseline, services=None):
        if not self._health_results:
            raise AssertionError("adapter asked to probe more often than scripted")
        return self._health_results.pop(0)


@pytest.fixture
def scheduler(storage):
    return ExecutorScheduler(storage, now_provider=lambda: datetime(2026, 5, 8, 12, 0, 0))


async def _build_executor(
    storage,
    *,
    tracker_name: str,
    profile: HealthCheckProfile,
    container_id: str = "c1",
) -> ExecutorConfig:
    runtime_id = await _create_runtime_connection(storage, name=f"{tracker_name}-runtime")
    tracker_source_id = await _create_tracker_source(storage, name=tracker_name)
    await _seed_release(storage, tracker_name=tracker_name, version="2.0.0")
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name=f"{tracker_name}-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=tracker_source_id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": container_id},
            health_check=profile,
        )
    )
    return await storage.get_executor_config(executor_id)


# ---- Tests ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_strategy_none_preserves_pre_feature_success_semantics(storage, scheduler):
    """strategy=none must not change run status or add any
    ``health_check`` diagnostics."""
    executor = await _build_executor(
        storage,
        tracker_name="hc-none",
        profile=HealthCheckProfile(),
    )
    adapter = _FakeAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id),
        current_image="hc-none:1.0.0",
        update_result=RuntimeUpdateResult(
            updated=True, old_image="hc-none:1.0.0", new_image="hc-none:2.0.0"
        ),
        health_results=[],
    )
    scheduler._adapters[executor.id] = adapter

    outcome = await scheduler.run_executor_now(executor.id)

    assert outcome.status == "success"
    run = await scheduler.storage.get_latest_executor_run(executor.id)
    assert run.diagnostics is None or "health_check" not in run.diagnostics


@pytest.mark.asyncio
async def test_runtime_native_healthy_finalizes_success_with_diagnostics(storage, scheduler):
    """healthy probe → status=success + health_check diagnostics."""
    executor = await _build_executor(
        storage,
        tracker_name="hc-healthy",
        profile=HealthCheckProfile(
            strategy="runtime_native",
            grace_period_seconds=0,
            attempt_timeout_seconds=5,
            interval_seconds=1,
            probe_window_seconds=60,
            failure_policy="mark_failed",
        ),
    )
    adapter = _FakeAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id),
        current_image="hc-healthy:1.0.0",
        update_result=RuntimeUpdateResult(
            updated=True, old_image="hc-healthy:1.0.0", new_image="hc-healthy:2.0.0"
        ),
        health_results=[ProbeAttemptResult(healthy=True, detail={"health": "healthy"})],
    )
    scheduler._adapters[executor.id] = adapter

    outcome = await scheduler.run_executor_now(executor.id)

    assert outcome.status == "success"
    run = await scheduler.storage.get_latest_executor_run(executor.id)
    assert run.diagnostics is not None
    assert run.diagnostics["health_check"]["outcome"] == "healthy"
    assert run.diagnostics["health_check"]["strategy"] == "runtime_native"
    assert run.diagnostics["health_check"]["attempt_count"] == 1


@pytest.mark.asyncio
async def test_runtime_native_unhealthy_mark_failed_finalizes_failed(storage, scheduler):
    """unhealthy + mark_failed → status=failed + diagnostics."""
    executor = await _build_executor(
        storage,
        tracker_name="hc-unhealthy",
        profile=HealthCheckProfile(
            strategy="runtime_native",
            grace_period_seconds=0,
            attempt_timeout_seconds=1,
            interval_seconds=1,
            probe_window_seconds=2,
            failure_policy="mark_failed",
        ),
    )
    adapter = _FakeAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id),
        current_image="hc-unhealthy:1.0.0",
        update_result=RuntimeUpdateResult(
            updated=True, old_image="hc-unhealthy:1.0.0", new_image="hc-unhealthy:2.0.0"
        ),
        health_results=[
            ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error="container status is 'exited'",
            ),
            ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error="container status is 'exited'",
            ),
            ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error="container status is 'exited'",
            ),
        ],
    )
    scheduler._adapters[executor.id] = adapter

    outcome = await scheduler.run_executor_now(executor.id)

    assert outcome.status == "failed"
    assert outcome.message and outcome.message.startswith("health_check_failed:")
    run = await scheduler.storage.get_latest_executor_run(executor.id)
    assert run.diagnostics["health_check"]["outcome"] == "unhealthy"


@pytest.mark.asyncio
async def test_runtime_native_unhealthy_mark_degraded_uses_degraded_prefix(storage, scheduler):
    """mark_degraded → status=failed with ``degraded:`` prefix."""
    executor = await _build_executor(
        storage,
        tracker_name="hc-degraded",
        profile=HealthCheckProfile(
            strategy="runtime_native",
            grace_period_seconds=0,
            attempt_timeout_seconds=1,
            interval_seconds=1,
            probe_window_seconds=2,
            failure_policy="mark_degraded",
        ),
    )
    adapter = _FakeAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id),
        current_image="hc-degraded:1.0.0",
        update_result=RuntimeUpdateResult(
            updated=True, old_image="hc-degraded:1.0.0", new_image="hc-degraded:2.0.0"
        ),
        health_results=[
            ProbeAttemptResult(
                healthy=False,
                error_category="runtime_api_error",
                last_error="container not ready",
            )
        ]
        * 5,
    )
    scheduler._adapters[executor.id] = adapter

    outcome = await scheduler.run_executor_now(executor.id)

    assert outcome.status == "failed"
    assert outcome.message.startswith("degraded:")
    run = await scheduler.storage.get_latest_executor_run(executor.id)
    assert run.diagnostics["health_check"]["failure_policy"] == "mark_degraded"


@pytest.mark.asyncio
async def test_notification_payload_includes_health_check_object(storage, scheduler, monkeypatch):
    """Webhook payloads include health check details without changing existing fields."""
    executor = await _build_executor(
        storage,
        tracker_name="hc-notify",
        profile=HealthCheckProfile(
            strategy="runtime_native",
            grace_period_seconds=0,
            attempt_timeout_seconds=5,
            interval_seconds=1,
            probe_window_seconds=60,
            failure_policy="mark_failed",
        ),
    )
    adapter = _FakeAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id),
        current_image="hc-notify:1.0.0",
        update_result=RuntimeUpdateResult(
            updated=True, old_image="hc-notify:1.0.0", new_image="hc-notify:2.0.0"
        ),
        health_results=[ProbeAttemptResult(healthy=True, detail={"health": "healthy"})],
    )
    scheduler._adapters[executor.id] = adapter

    await storage.create_notifier(
        {
            "name": "hc-webhook",
            "type": "webhook",
            "url": "https://example.com/hook",
            "events": ["executor_run_success"],
            "enabled": True,
            "language": "en",
        }
    )

    captured: list[tuple[str, dict]] = []

    async def _fake_notify(self, event, payload):
        captured.append((event, payload))

    monkeypatch.setattr("releasetracker.notifiers.webhook.WebhookNotifier.notify", _fake_notify)

    outcome = await scheduler.run_executor_now(executor.id)

    assert outcome.status == "success"
    assert len(captured) == 1
    event, payload = captured[0]
    assert event == "executor_run_success"
    # Pre-feature fields preserved.
    assert payload["executor_name"] == "hc-notify-executor"
    assert payload["to_version"]
    # Additive field.
    assert payload["health_check"]["outcome"] == "healthy"
    assert payload["health_check"]["strategy"] == "runtime_native"
    # Recovery hook did not run — field must be absent.
    assert "recovery_outcome" not in payload


@pytest.mark.asyncio
async def test_notification_payload_omits_health_check_when_strategy_none(
    storage, scheduler, monkeypatch
):
    """Runs without post-update health checks omit the ``health_check`` key."""
    executor = await _build_executor(
        storage,
        tracker_name="hc-no-notify",
        profile=HealthCheckProfile(),
    )
    adapter = _FakeAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id),
        current_image="hc-no-notify:1.0.0",
        update_result=RuntimeUpdateResult(
            updated=True, old_image="hc-no-notify:1.0.0", new_image="hc-no-notify:2.0.0"
        ),
        health_results=[],
    )
    scheduler._adapters[executor.id] = adapter

    await storage.create_notifier(
        {
            "name": "hc-no-webhook",
            "type": "webhook",
            "url": "https://example.com/hook",
            "events": ["executor_run_success"],
            "enabled": True,
            "language": "en",
        }
    )

    captured: list[tuple[str, dict]] = []

    async def _fake_notify(self, event, payload):
        captured.append((event, payload))

    monkeypatch.setattr("releasetracker.notifiers.webhook.WebhookNotifier.notify", _fake_notify)

    await scheduler.run_executor_now(executor.id)

    event, payload = captured[0]
    assert event == "executor_run_success"
    assert "health_check" not in payload
    assert "recovery_outcome" not in payload


# ---- manual-only health check failures -----------------------------------


class _NoAutoRecoverAdapter(_FakeAdapter):
    """FakeAdapter variant that records whether recover_from_snapshot is called."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recover_calls: int = 0

    async def recover_from_snapshot(self, target_ref, snapshot):
        self.recover_calls += 1
        return RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image="recovered",
            new_container_id="recovered-container-id",
        )


@pytest.mark.asyncio
async def test_unhealthy_health_check_never_invokes_snapshot_recovery(storage, scheduler):
    """Unhealthy probes fail the run and leave rollback as a manual action."""
    executor = await _build_executor(
        storage,
        tracker_name="hc-manual-only",
        profile=HealthCheckProfile(
            strategy="runtime_native",
            grace_period_seconds=0,
            attempt_timeout_seconds=1,
            interval_seconds=1,
            probe_window_seconds=2,
            failure_policy="mark_failed",
        ),
    )
    adapter = _NoAutoRecoverAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id),
        current_image="hc-manual-only:1.0.0",
        update_result=RuntimeUpdateResult(
            updated=True,
            old_image="hc-manual-only:1.0.0",
            new_image="hc-manual-only:2.0.0",
        ),
        health_results=[ProbeAttemptResult(healthy=False, last_error="still unready")] * 5,
    )
    scheduler._adapters[executor.id] = adapter

    outcome = await scheduler.run_executor_now(executor.id)

    assert outcome.status == "failed"
    assert outcome.message.startswith("health_check_failed:")
    assert adapter.recover_calls == 0
    run = await scheduler.storage.get_latest_executor_run(executor.id)
    assert run.diagnostics["health_check"]["outcome"] == "unhealthy"
    assert "recovery_outcome" not in run.diagnostics
    assert "recovery_error" not in run.diagnostics


@pytest.mark.asyncio
async def test_legacy_mark_failed_and_recover_normalizes_to_mark_failed():
    profile = HealthCheckProfile(
        strategy="runtime_native",
        grace_period_seconds=0,
        attempt_timeout_seconds=1,
        interval_seconds=1,
        probe_window_seconds=2,
        failure_policy="mark_failed_and_recover",
    )

    assert profile.failure_policy == "mark_failed"
