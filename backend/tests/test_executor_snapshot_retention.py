"""Tests for the snapshot retention pruning service."""

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
from releasetracker.models import ExecutorRunHistory, ExecutorSnapshot
from releasetracker.services.snapshot_service import SnapshotService
from releasetracker.storage.sqlite import SQLiteStorage


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
            description="runtime for retention tests",
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
            target_ref={"mode": "container", "container_id": "retention-container"},
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


@pytest.mark.asyncio
async def test_prune_is_noop_when_count_below_retention(storage):
    executor_id = await _create_executor(storage, name="retention-noop")
    await _seed_snapshots(storage, executor_id, count=3)

    service = SnapshotService(storage)
    deleted = await service.prune_after_insert(executor_id, retention=10)

    assert deleted == []
    assert await storage.count_executor_snapshots(executor_id) == 3


@pytest.mark.asyncio
async def test_prune_drops_oldest_beyond_cap(storage):
    executor_id = await _create_executor(storage, name="retention-overflow")
    ids = await _seed_snapshots(storage, executor_id, count=5)

    service = SnapshotService(storage)
    deleted = await service.prune_after_insert(executor_id, retention=3)

    # Oldest two go (ids[0], ids[1]); newest three remain.
    assert set(deleted) == {ids[0], ids[1]}
    remaining = await storage.list_executor_snapshots(executor_id, limit=10, offset=0)
    assert {snap.id for snap in remaining} == set(ids[2:])


@pytest.mark.asyncio
async def test_prune_excludes_in_flight_rollback_ids(storage):
    executor_id = await _create_executor(storage, name="retention-in-flight")
    ids = await _seed_snapshots(storage, executor_id, count=5)

    claim = await storage.claim_executor_snapshot_for_rollback(
        executor_id=executor_id,
        snapshot_id=ids[0],
        run=ExecutorRunHistory(executor_id=executor_id, started_at=datetime.now(), status="queued"),
        active_statuses=frozenset({"queued", "running", "health_checking"}),
    )
    assert claim is not None

    service = SnapshotService(storage)
    deleted = await service.prune_after_insert(executor_id, retention=3)

    # Only the other overflow candidate (ids[1]) may be deleted.
    assert deleted == [ids[1]]
    remaining_ids = {
        snap.id for snap in await storage.list_executor_snapshots(executor_id, limit=10, offset=0)
    }
    assert ids[0] in remaining_ids
    assert ids[1] not in remaining_ids


@pytest.mark.asyncio
async def test_prune_skips_when_retention_is_invalid(storage):
    executor_id = await _create_executor(storage, name="retention-invalid")
    await _seed_snapshots(storage, executor_id, count=3)

    service = SnapshotService(storage)
    deleted = await service.prune_after_insert(executor_id, retention=0)

    assert deleted == []
    assert await storage.count_executor_snapshots(executor_id) == 3


@pytest.mark.asyncio
async def test_snapshot_claim_is_visible_from_another_storage_instance(storage):
    executor_id = await _create_executor(storage, name="retention-persistent-claim")
    snapshot_id = (await _seed_snapshots(storage, executor_id, count=1))[0]
    claim = await storage.claim_executor_snapshot_for_rollback(
        executor_id=executor_id,
        snapshot_id=snapshot_id,
        run=ExecutorRunHistory(executor_id=executor_id, started_at=datetime.now(), status="queued"),
        active_statuses=frozenset({"queued", "running", "health_checking"}),
    )
    assert claim is not None

    second_storage = SQLiteStorage(
        storage.db_path,
        system_key_manager=storage.system_key_manager,
    )
    try:
        assert await second_storage.is_executor_snapshot_claimed(
            executor_id=executor_id,
            snapshot_id=snapshot_id,
        )
    finally:
        await second_storage.close()
