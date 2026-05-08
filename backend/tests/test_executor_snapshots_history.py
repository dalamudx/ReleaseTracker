"""Storage-level tests for the multi-row executor snapshot history.

Focuses on the new storage surface added in Phase A:
- ``create_executor_snapshot`` inserts distinct rows per capture.
- ``get_executor_snapshot`` returns the most recent snapshot.
- ``list_executor_snapshots`` pages newest-first.
- ``get_executor_snapshot_by_id`` scopes by executor.
- ``delete_executor_snapshots`` deletes only the requested ids.
"""

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


async def _create_runtime_connection(
    storage,
    name: str = "prod-docker",
    runtime_type: Literal["docker", "podman", "kubernetes"] = "docker",
) -> int:
    runtime_config = RuntimeConnectionConfig(
        name=name,
        type=runtime_type,
        enabled=True,
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={"token": "secret-token"},
        description="primary runtime",
    )
    return await storage.create_runtime_connection(runtime_config)


async def _create_tracker_source_id(storage, name: str = "sample-web") -> int:
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


async def _create_executor(storage, *, name: str = "history-executor") -> int:
    runtime_id = await _create_runtime_connection(storage, name=f"{name}-runtime")
    tracker_source_id = await _create_tracker_source_id(storage, name=f"{name}-tracker")
    return await storage.save_executor_config(
        ExecutorConfig(
            name=name,
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=f"{name}-tracker",
            tracker_source_id=tracker_source_id,
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-history"},
            description="snapshot history executor",
        )
    )


@pytest.mark.asyncio
async def test_create_executor_snapshot_inserts_distinct_rows(storage):
    executor_id = await _create_executor(storage)

    first_id = await storage.create_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={"image": "sample-web:1.24.0"},
            trigger="pre_update",
            image_at_capture="sample-web:1.24.0",
            executor_run_id=None,
        )
    )
    second_id = await storage.create_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={"image": "sample-web:1.25.0"},
            trigger="pre_update",
            image_at_capture="sample-web:1.25.0",
            executor_run_id=None,
        )
    )

    assert first_id != second_id
    assert await storage.count_executor_snapshots(executor_id) == 2


@pytest.mark.asyncio
async def test_get_executor_snapshot_returns_most_recent(storage):
    executor_id = await _create_executor(storage, name="history-latest-executor")

    # Earlier snapshot
    await storage.create_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={"image": "sample-web:1.0.0"},
            trigger="pre_update",
            image_at_capture="sample-web:1.0.0",
            created_at=datetime(2026, 5, 1, 9, 0, 0),
            updated_at=datetime(2026, 5, 1, 9, 0, 0),
        )
    )
    # Later snapshot
    later_id = await storage.create_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={"image": "sample-web:2.0.0"},
            trigger="pre_rollback",
            image_at_capture="sample-web:2.0.0",
            created_at=datetime(2026, 5, 8, 9, 0, 0),
            updated_at=datetime(2026, 5, 8, 9, 0, 0),
        )
    )

    snapshot = await storage.get_executor_snapshot(executor_id)
    assert snapshot is not None
    assert snapshot.id == later_id
    assert snapshot.trigger == "pre_rollback"
    assert snapshot.image_at_capture == "sample-web:2.0.0"


@pytest.mark.asyncio
async def test_list_executor_snapshots_pages_newest_first(storage):
    executor_id = await _create_executor(storage, name="history-page-executor")

    base = datetime(2026, 4, 1, 0, 0, 0)
    ids = []
    for index in range(5):
        snapshot_id = await storage.create_executor_snapshot(
            ExecutorSnapshot(
                executor_id=executor_id,
                snapshot_data={"image": f"sample-web:1.{index}.0"},
                trigger="pre_update",
                image_at_capture=f"sample-web:1.{index}.0",
                created_at=base + timedelta(minutes=index),
                updated_at=base + timedelta(minutes=index),
            )
        )
        ids.append(snapshot_id)

    page_one = await storage.list_executor_snapshots(executor_id, limit=2, offset=0)
    page_two = await storage.list_executor_snapshots(executor_id, limit=2, offset=2)
    page_three = await storage.list_executor_snapshots(executor_id, limit=2, offset=4)

    assert [snap.id for snap in page_one] == list(reversed(ids[-2:]))
    assert [snap.id for snap in page_two] == list(reversed(ids[1:3]))
    assert [snap.id for snap in page_three] == [ids[0]]


@pytest.mark.asyncio
async def test_get_executor_snapshot_by_id_scopes_by_executor(storage):
    executor_id_a = await _create_executor(storage, name="history-scope-a")
    executor_id_b = await _create_executor(storage, name="history-scope-b")

    snapshot_id = await storage.create_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id_a,
            snapshot_data={"image": "a:1"},
            trigger="pre_update",
            image_at_capture="a:1",
        )
    )

    assert (
        await storage.get_executor_snapshot_by_id(executor_id_a, snapshot_id)
    ).id == snapshot_id
    # Foreign executor must see nothing.
    assert await storage.get_executor_snapshot_by_id(executor_id_b, snapshot_id) is None
    # Missing id returns None too.
    assert await storage.get_executor_snapshot_by_id(executor_id_a, 999_999) is None


@pytest.mark.asyncio
async def test_delete_executor_snapshots_removes_only_requested_ids(storage):
    executor_id = await _create_executor(storage, name="history-delete-executor")

    ids = []
    for index in range(3):
        ids.append(
            await storage.create_executor_snapshot(
                ExecutorSnapshot(
                    executor_id=executor_id,
                    snapshot_data={"image": f"sample-web:2.{index}.0"},
                    trigger="pre_update",
                    image_at_capture=f"sample-web:2.{index}.0",
                )
            )
        )

    deleted = await storage.delete_executor_snapshots(executor_id, [ids[0], ids[2]])
    assert deleted == 2
    remaining = await storage.list_executor_snapshots(executor_id, limit=10, offset=0)
    assert [snap.id for snap in remaining] == [ids[1]]


@pytest.mark.asyncio
async def test_delete_executor_snapshots_is_noop_for_empty_list(storage):
    executor_id = await _create_executor(storage, name="history-delete-empty")
    await storage.create_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={"image": "sample-web:1"},
            trigger="pre_update",
        )
    )

    assert await storage.delete_executor_snapshots(executor_id, []) == 0
    assert await storage.count_executor_snapshots(executor_id) == 1


@pytest.mark.asyncio
async def test_save_executor_snapshot_shim_delegates_to_history_insert(storage):
    executor_id = await _create_executor(storage, name="history-shim-executor")

    await storage.save_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={"image": "shim:1"},
            trigger="pre_update",
            image_at_capture="shim:1",
        )
    )
    await storage.save_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={"image": "shim:2"},
            trigger="pre_update",
            image_at_capture="shim:2",
        )
    )

    # Shim must not overwrite: both rows persist and the newest is returned.
    assert await storage.count_executor_snapshots(executor_id) == 2
    latest = await storage.get_executor_snapshot(executor_id)
    assert latest is not None
    assert latest.image_at_capture == "shim:2"
