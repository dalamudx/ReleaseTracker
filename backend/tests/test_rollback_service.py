"""RollbackService orchestration tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

import pytest
from fastapi import HTTPException

from helpers.executor_runtime import save_docker_tracker_config
from releasetracker.config import (
    Channel,
    ExecutorConfig,
    RuntimeConnectionConfig,
)
from releasetracker.executors.base import BaseRuntimeAdapter, RuntimeUpdateResult
from releasetracker.models import ExecutorRunHistory, ExecutorSnapshot
from releasetracker.services.rollback_service import RollbackService
from releasetracker.services.snapshot_service import SnapshotService


# ---- Helpers --------------------------------------------------------------


class _RollbackAdapter(BaseRuntimeAdapter):
    """Adapter with overridable snapshot + recover hooks."""

    def __init__(
        self,
        runtime_connection,
        *,
        capture_raises: Exception | None = None,
        validate_raises: Exception | None = None,
        recover_raises: Exception | None = None,
        recover_result: RuntimeUpdateResult | None = None,
        current_image: str = "acme/api:1.0.0",
    ):
        super().__init__(runtime_connection)
        self._capture_raises = capture_raises
        self._validate_raises = validate_raises
        self._recover_raises = recover_raises
        self._recover_result = recover_result or RuntimeUpdateResult(
            updated=True, old_image=None, new_image="acme/api:prev"
        )
        self._current_image = current_image
        self.recover_calls = 0
        self.capture_calls = 0
        self.recover_snapshot_args: list[dict] = []

    async def discover_targets(self):
        return []

    async def validate_target_ref(self, target_ref):
        return None

    async def get_current_image(self, target_ref):
        return self._current_image

    async def capture_snapshot(self, target_ref, current_image):
        self.capture_calls += 1
        if self._capture_raises is not None:
            raise self._capture_raises
        return {
            "runtime_type": self.runtime_connection.type,
            "image": current_image,
            "container_id": target_ref.get("container_id", "c1"),
        }

    async def validate_snapshot(self, target_ref, snapshot):
        if self._validate_raises is not None:
            raise self._validate_raises

    async def update_image(self, target_ref, new_image):
        raise NotImplementedError

    async def recover_from_snapshot(self, target_ref, snapshot):
        self.recover_calls += 1
        self.recover_snapshot_args.append(snapshot)
        if self._recover_raises is not None:
            raise self._recover_raises
        return self._recover_result


async def _create_runtime_connection(
    storage,
    *,
    name: str,
    runtime_type: Literal["docker", "podman", "kubernetes"] = "docker",
) -> int:
    return await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name=name,
            type=runtime_type,
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


async def _create_executor(storage, *, name: str) -> ExecutorConfig:
    runtime_id = await _create_runtime_connection(storage, name=f"{name}-runtime")
    tracker_source_id = await _create_tracker_source(storage, name=f"{name}-tracker")
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name=name,
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=f"{name}-tracker",
            tracker_source_id=tracker_source_id,
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": f"{name}-c1"},
        )
    )
    return await storage.get_executor_config(executor_id)


async def _seed_snapshot(
    storage,
    executor_id: int,
    *,
    image: str = "acme/api:1.0.0",
    trigger: str = "pre_update",
    offset_minutes: int = 0,
) -> int:
    return await storage.create_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={"image": image},
            trigger=trigger,
            image_at_capture=image,
            created_at=datetime(2026, 5, 8, 10, 0, 0) + timedelta(minutes=offset_minutes),
            updated_at=datetime(2026, 5, 8, 10, 0, 0) + timedelta(minutes=offset_minutes),
        )
    )


async def _seed_run(
    storage,
    executor_id: int,
    *,
    status: str,
) -> int:
    return await storage.create_executor_run(
        ExecutorRunHistory(
            executor_id=executor_id,
            started_at=datetime(2026, 5, 8, 12, 0, 0),
            status=status,
        )
    )


# ---- Tests ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_with_default_snapshot_uses_most_recent(storage):
    executor = await _create_executor(storage, name="rb-default")
    await _seed_snapshot(storage, executor.id, image="acme/api:1.0.0", offset_minutes=0)
    newest_id = await _seed_snapshot(
        storage, executor.id, image="acme/api:2.0.0", offset_minutes=5
    )

    adapter = _RollbackAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id)
    )
    service = RollbackService(storage, SnapshotService(storage))

    outcome = await service.rollback(
        executor_config=executor,
        adapter=adapter,
        snapshot_id=None,
        actor="alice",
    )

    assert outcome.recovery_outcome == "succeeded"
    assert outcome.run.status == "success"
    assert outcome.run.diagnostics["run_trigger"] == "manual_rollback"
    assert outcome.run.diagnostics["snapshot_id"] == newest_id
    assert outcome.run.diagnostics["actor"] == "alice"
    assert outcome.run.from_version == "acme/api:1.0.0"
    assert outcome.run.to_version == "acme/api:2.0.0"
    # Pre-rollback snapshot captured.
    assert adapter.capture_calls == 1
    assert adapter.recover_calls == 1
    stored_snapshots = await storage.list_executor_snapshots(
        executor.id, limit=10, offset=0
    )
    triggers = [snap.trigger for snap in stored_snapshots]
    assert "pre_rollback" in triggers


@pytest.mark.asyncio
async def test_rollback_with_explicit_snapshot_id_restores_that_row(storage):
    executor = await _create_executor(storage, name="rb-explicit")
    oldest_id = await _seed_snapshot(
        storage, executor.id, image="acme/api:1.0.0", offset_minutes=0
    )
    await _seed_snapshot(
        storage, executor.id, image="acme/api:2.0.0", offset_minutes=5
    )

    adapter = _RollbackAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id)
    )
    service = RollbackService(storage, SnapshotService(storage))
    outcome = await service.rollback(
        executor_config=executor,
        adapter=adapter,
        snapshot_id=oldest_id,
        actor=None,
    )
    assert outcome.run.diagnostics["snapshot_id"] == oldest_id


@pytest.mark.asyncio
async def test_rollback_refreshes_container_id_after_recreate(storage):
    executor = await _create_executor(storage, name="rb-container-refresh")
    await _seed_snapshot(storage, executor.id, image="acme/api:1.0.0")

    adapter = _RollbackAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id),
        recover_result=RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image="acme/api:1.0.0",
            new_container_id="recovered-container-id",
        ),
    )
    service = RollbackService(storage, SnapshotService(storage))
    outcome = await service.rollback(
        executor_config=executor,
        adapter=adapter,
        snapshot_id=None,
        actor=None,
    )

    assert outcome.recovery_outcome == "succeeded"
    refreshed = await storage.get_executor_config(executor.id)
    assert refreshed.target_ref["container_id"] == "recovered-container-id"


@pytest.mark.asyncio
async def test_rollback_rejects_foreign_snapshot_id(storage):
    executor_a = await _create_executor(storage, name="rb-foreign-a")
    executor_b = await _create_executor(storage, name="rb-foreign-b")
    a_snap = await _seed_snapshot(storage, executor_a.id, image="a:1")

    adapter = _RollbackAdapter(
        await storage.get_runtime_connection(executor_b.runtime_connection_id)
    )
    service = RollbackService(storage, SnapshotService(storage))
    with pytest.raises(HTTPException) as excinfo:
        await service.rollback(
            executor_config=executor_b,
            adapter=adapter,
            snapshot_id=a_snap,
            actor=None,
        )
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_rollback_404_when_no_snapshot_available(storage):
    executor = await _create_executor(storage, name="rb-empty")
    adapter = _RollbackAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id)
    )
    service = RollbackService(storage, SnapshotService(storage))
    with pytest.raises(HTTPException) as excinfo:
        await service.rollback(
            executor_config=executor,
            adapter=adapter,
            snapshot_id=None,
            actor=None,
        )
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_rollback_409_when_executor_has_active_run(storage):
    executor = await _create_executor(storage, name="rb-busy")
    await _seed_snapshot(storage, executor.id)
    await _seed_run(storage, executor.id, status="running")

    adapter = _RollbackAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id)
    )
    service = RollbackService(storage, SnapshotService(storage))
    with pytest.raises(HTTPException) as excinfo:
        await service.rollback(
            executor_config=executor,
            adapter=adapter,
            snapshot_id=None,
            actor=None,
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["active_run_status"] == "running"


@pytest.mark.asyncio
async def test_rollback_pre_rollback_capture_failure_skips_recover(storage):
    executor = await _create_executor(storage, name="rb-cap-fail")
    await _seed_snapshot(storage, executor.id, image="acme/api:1.0.0")

    adapter = _RollbackAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id),
        capture_raises=RuntimeError("docker daemon down"),
    )
    service = RollbackService(storage, SnapshotService(storage))
    outcome = await service.rollback(
        executor_config=executor,
        adapter=adapter,
        snapshot_id=None,
        actor=None,
    )
    assert outcome.run.status == "failed"
    assert outcome.recovery_outcome == "failed"
    assert "pre_rollback_capture_error" in outcome.run.diagnostics
    # recover_from_snapshot must NOT have been called.
    assert adapter.recover_calls == 0


@pytest.mark.asyncio
async def test_rollback_not_supported_when_adapter_cannot_recover(storage):
    executor = await _create_executor(storage, name="rb-no-recover")
    await _seed_snapshot(storage, executor.id, image="acme/api:1.0.0")

    adapter = _RollbackAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id),
        recover_raises=NotImplementedError("no recover"),
    )
    service = RollbackService(storage, SnapshotService(storage))
    outcome = await service.rollback(
        executor_config=executor,
        adapter=adapter,
        snapshot_id=None,
        actor=None,
    )
    assert outcome.run.status == "failed"
    assert outcome.recovery_outcome == "not_supported"


@pytest.mark.asyncio
async def test_rollback_does_not_run_health_check_phase(storage):
    """Rollback run terminates without post-update health checks."""
    executor = await _create_executor(storage, name="rb-no-hc")
    await _seed_snapshot(storage, executor.id, image="acme/api:1.0.0")

    adapter = _RollbackAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id)
    )
    service = RollbackService(storage, SnapshotService(storage))
    outcome = await service.rollback(
        executor_config=executor,
        adapter=adapter,
        snapshot_id=None,
        actor=None,
    )
    assert outcome.run.status == "success"
    assert "health_check" not in (outcome.run.diagnostics or {})


@pytest.mark.asyncio
async def test_rollback_propagates_recovery_error_to_run(storage):
    executor = await _create_executor(storage, name="rb-recover-err")
    await _seed_snapshot(storage, executor.id, image="acme/api:1.0.0")

    adapter = _RollbackAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id),
        recover_raises=RuntimeError(
            '500 Server Error: Internal Server Error (creating container '
            'storage: the container name "cool_carson" is already in use)'
        ),
    )
    service = RollbackService(storage, SnapshotService(storage))
    outcome = await service.rollback(
        executor_config=executor,
        adapter=adapter,
        snapshot_id=None,
        actor=None,
    )
    assert outcome.run.status == "failed"
    assert outcome.recovery_outcome == "failed"
    assert outcome.recovery_error is not None
    assert "cool_carson" in outcome.recovery_error
    assert outcome.run.diagnostics["recovery_error"] == outcome.recovery_error
    assert "cool_carson" in outcome.run.message


@pytest.mark.asyncio
async def test_rollback_surface_pre_rollback_capture_error(storage):
    executor = await _create_executor(storage, name="rb-capture-err")
    await _seed_snapshot(storage, executor.id, image="acme/api:1.0.0")

    adapter = _RollbackAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id),
        capture_raises=RuntimeError("docker daemon down"),
    )
    service = RollbackService(storage, SnapshotService(storage))
    outcome = await service.rollback(
        executor_config=executor,
        adapter=adapter,
        snapshot_id=None,
        actor=None,
    )
    assert outcome.run.status == "failed"
    assert outcome.recovery_outcome == "failed"
    assert outcome.recovery_error == "docker daemon down"
    assert "docker daemon down" in outcome.run.message


@pytest.mark.asyncio
async def test_rollback_passes_target_snapshot_to_adapter_not_prerollback(storage):
    """Regression: after pre_rollback capture the storage's "latest" row is
    the row we just inserted. The adapter must still receive the *target*
    snapshot data, not the pre_rollback one."""
    executor = await _create_executor(storage, name="rb-target-snapshot")
    target_snapshot_id = await _seed_snapshot(
        storage, executor.id, image="acme/api:1.0.0", offset_minutes=0
    )
    # Newer row that would otherwise be picked up by "most recent" semantics.
    await _seed_snapshot(
        storage, executor.id, image="acme/api:2.0.0", offset_minutes=5
    )

    adapter = _RollbackAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id)
    )
    service = RollbackService(storage, SnapshotService(storage))
    outcome = await service.rollback(
        executor_config=executor,
        adapter=adapter,
        snapshot_id=target_snapshot_id,
        actor=None,
    )
    assert outcome.recovery_outcome == "succeeded"
    # Exactly one call: the rollback recovery itself.
    assert adapter.recover_calls == 1
    recovered = adapter.recover_snapshot_args[0]
    assert recovered["image"] == "acme/api:1.0.0", (
        "adapter must receive the target snapshot data, not the pre_rollback row"
    )
