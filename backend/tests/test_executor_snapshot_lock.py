"""Tests for snapshot lock/unlock functionality."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

import pytest

from helpers.executor_runtime import save_docker_tracker_config
from releasetracker.config import (
    Channel,
    ExecutorConfig,
    RuntimeConnectionConfig,
)
from releasetracker.models import ExecutorSnapshot
from releasetracker.services.snapshot_service import (
    InFlightRollbackRegistry,
    SnapshotLockedError,
    SnapshotService,
)


async def _create_runtime_connection(
    storage,
    name: str,
    runtime_type: Literal["docker", "podman", "kubernetes"] = "docker",
) -> int:
    return await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name=name,
            type=runtime_type,
            enabled=True,
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={"token": "secret-token"},
            description="runtime for lock tests",
        )
    )


async def _create_tracker_source_id(storage, name: str) -> int:
    await save_docker_tracker_config(
        storage,
        name=name,
        image=name,
        channels=[Channel(name="stable", enabled=True, type="release")],
    )
    aggregate_tracker = await storage.get_aggregate_tracker(name)
    assert aggregate_tracker is not None
    assert aggregate_tracker.sources[0].id is not None
    return aggregate_tracker.sources[0].id


async def _create_executor(storage, *, name: str) -> int:
    runtime_id = await _create_runtime_connection(storage, f"{name}-runtime")
    tracker_source_id = await _create_tracker_source_id(storage, f"{name}-tracker")
    return await storage.save_executor_config(
        ExecutorConfig(
            name=name,
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=f"{name}-tracker",
            tracker_source_id=tracker_source_id,
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "lock-container"},
        )
    )


async def _seed_snapshots(storage, executor_id: int, count: int) -> list[int]:
    base = datetime(2026, 4, 1, 0, 0, 0)
    ids: list[int] = []
    for index in range(count):
        ids.append(
            await storage.create_executor_snapshot(
                ExecutorSnapshot(
                    executor_id=executor_id,
                    snapshot_data={"image": f"sample-web:1.{index}.0"},
                    trigger="pre_update",
                    image_at_capture=f"sample-web:1.{index}.0",
                    created_at=base + timedelta(minutes=index),
                    updated_at=base + timedelta(minutes=index),
                )
            )
        )
    return ids


# ---------------------------------------------------------------------------
# Storage-level lock/unlock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_executor_snapshot_locked_sets_flag(storage):
    executor_id = await _create_executor(storage, name="lock-set")
    ids = await _seed_snapshots(storage, executor_id, count=1)
    snapshot_id = ids[0]

    result = await storage.set_executor_snapshot_locked(executor_id, snapshot_id, locked=True)
    assert result is True

    snapshot = await storage.get_executor_snapshot_by_id(executor_id, snapshot_id)
    assert snapshot is not None
    assert snapshot.locked is True


@pytest.mark.asyncio
async def test_set_executor_snapshot_locked_clears_flag(storage):
    executor_id = await _create_executor(storage, name="lock-clear")
    ids = await _seed_snapshots(storage, executor_id, count=1)
    snapshot_id = ids[0]

    await storage.set_executor_snapshot_locked(executor_id, snapshot_id, locked=True)
    result = await storage.set_executor_snapshot_locked(executor_id, snapshot_id, locked=False)
    assert result is True

    snapshot = await storage.get_executor_snapshot_by_id(executor_id, snapshot_id)
    assert snapshot is not None
    assert snapshot.locked is False


@pytest.mark.asyncio
async def test_set_executor_snapshot_locked_returns_false_for_missing(storage):
    executor_id = await _create_executor(storage, name="lock-missing")
    result = await storage.set_executor_snapshot_locked(executor_id, 99999, locked=True)
    assert result is False


@pytest.mark.asyncio
async def test_snapshot_locked_field_defaults_to_false(storage):
    executor_id = await _create_executor(storage, name="lock-default")
    ids = await _seed_snapshots(storage, executor_id, count=1)
    snapshot = await storage.get_executor_snapshot_by_id(executor_id, ids[0])
    assert snapshot is not None
    assert snapshot.locked is False


# ---------------------------------------------------------------------------
# SnapshotService: prune skips locked snapshots
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prune_skips_locked_snapshots(storage):
    executor_id = await _create_executor(storage, name="lock-prune")
    ids = await _seed_snapshots(storage, executor_id, count=5)

    # Lock the oldest snapshot (ids[0]).
    await storage.set_executor_snapshot_locked(executor_id, ids[0], locked=True)

    service = SnapshotService(storage)
    deleted = await service.prune_after_insert(executor_id, retention=3)

    # ids[1] is the only unlocked overflow candidate; ids[0] is locked and must survive.
    assert deleted == [ids[1]]
    remaining_ids = {
        snap.id
        for snap in await storage.list_executor_snapshots(executor_id, limit=10, offset=0)
    }
    assert ids[0] in remaining_ids
    assert ids[1] not in remaining_ids


@pytest.mark.asyncio
async def test_prune_skips_all_locked_overflow(storage):
    executor_id = await _create_executor(storage, name="lock-prune-all")
    ids = await _seed_snapshots(storage, executor_id, count=5)

    # Lock both overflow candidates.
    await storage.set_executor_snapshot_locked(executor_id, ids[0], locked=True)
    await storage.set_executor_snapshot_locked(executor_id, ids[1], locked=True)

    service = SnapshotService(storage)
    deleted = await service.prune_after_insert(executor_id, retention=3)

    assert deleted == []
    assert await storage.count_executor_snapshots(executor_id) == 5


# ---------------------------------------------------------------------------
# SnapshotService: delete rejects locked snapshots
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_locked_snapshot_raises_snapshot_locked_error(storage):
    executor_id = await _create_executor(storage, name="lock-delete")
    ids = await _seed_snapshots(storage, executor_id, count=1)
    snapshot_id = ids[0]

    await storage.set_executor_snapshot_locked(executor_id, snapshot_id, locked=True)

    service = SnapshotService(storage)
    with pytest.raises(SnapshotLockedError):
        await service.delete_snapshot(executor_id, snapshot_id)

    # Snapshot must still exist.
    assert await storage.get_executor_snapshot_by_id(executor_id, snapshot_id) is not None


@pytest.mark.asyncio
async def test_delete_unlocked_snapshot_succeeds(storage):
    executor_id = await _create_executor(storage, name="lock-delete-ok")
    ids = await _seed_snapshots(storage, executor_id, count=1)
    snapshot_id = ids[0]

    service = SnapshotService(storage)
    deleted = await service.delete_snapshot(executor_id, snapshot_id)
    assert deleted is True
    assert await storage.get_executor_snapshot_by_id(executor_id, snapshot_id) is None


@pytest.mark.asyncio
async def test_delete_locked_snapshot_takes_priority_over_in_flight(storage):
    """SnapshotLockedError is raised before SnapshotInUseError."""
    executor_id = await _create_executor(storage, name="lock-priority")
    ids = await _seed_snapshots(storage, executor_id, count=1)
    snapshot_id = ids[0]

    await storage.set_executor_snapshot_locked(executor_id, snapshot_id, locked=True)

    registry = InFlightRollbackRegistry()
    await registry.register(snapshot_id)

    service = SnapshotService(storage, registry=registry)
    with pytest.raises(SnapshotLockedError):
        await service.delete_snapshot(executor_id, snapshot_id)


# ---------------------------------------------------------------------------
# SnapshotService: set_snapshot_locked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_set_snapshot_locked_round_trip(storage):
    executor_id = await _create_executor(storage, name="lock-svc")
    ids = await _seed_snapshots(storage, executor_id, count=1)
    snapshot_id = ids[0]

    service = SnapshotService(storage)

    assert await service.set_snapshot_locked(executor_id, snapshot_id, locked=True) is True
    snap = await storage.get_executor_snapshot_by_id(executor_id, snapshot_id)
    assert snap is not None and snap.locked is True

    assert await service.set_snapshot_locked(executor_id, snapshot_id, locked=False) is True
    snap = await storage.get_executor_snapshot_by_id(executor_id, snapshot_id)
    assert snap is not None and snap.locked is False


@pytest.mark.asyncio
async def test_service_set_snapshot_locked_returns_false_for_missing(storage):
    executor_id = await _create_executor(storage, name="lock-svc-missing")
    service = SnapshotService(storage)
    assert await service.set_snapshot_locked(executor_id, 99999, locked=True) is False


# ---------------------------------------------------------------------------
# SnapshotService: list_snapshots exposes locked field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_snapshots_exposes_locked_field(storage):
    executor_id = await _create_executor(storage, name="lock-list")
    ids = await _seed_snapshots(storage, executor_id, count=2)

    await storage.set_executor_snapshot_locked(executor_id, ids[0], locked=True)

    service = SnapshotService(storage)
    view = await service.list_snapshots(executor_id, page=1, page_size=10)

    by_id = {item.id: item for item in view.items}
    assert by_id[ids[0]].locked is True
    assert by_id[ids[1]].locked is False
