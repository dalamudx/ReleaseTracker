"""SnapshotService list / detail tests."""

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
    REDACTED_MARKER,
    SnapshotService,
)


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


async def _create_executor(storage, *, name: str) -> int:
    runtime_id = await _create_runtime_connection(storage, name=f"{name}-runtime")
    tracker_source_id = await _create_tracker_source(storage, name=f"{name}-tracker")
    return await storage.save_executor_config(
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


@pytest.mark.asyncio
async def test_list_snapshots_paginates_newest_first(storage):
    executor_id = await _create_executor(storage, name="svc-list")

    base = datetime(2026, 4, 1, 0, 0, 0)
    created_ids = []
    for index in range(7):
        created_ids.append(
            await storage.create_executor_snapshot(
                ExecutorSnapshot(
                    executor_id=executor_id,
                    snapshot_data={"image": f"acme/api:1.{index}.0"},
                    trigger="pre_update",
                    image_at_capture=f"acme/api:1.{index}.0",
                    created_at=base + timedelta(minutes=index),
                    updated_at=base + timedelta(minutes=index),
                )
            )
        )

    service = SnapshotService(storage)
    page_one = await service.list_snapshots(executor_id, page=1, page_size=3)
    page_two = await service.list_snapshots(executor_id, page=2, page_size=3)

    assert page_one.total == 7
    assert page_one.page == 1
    assert page_one.page_size == 3
    assert [item.id for item in page_one.items] == list(reversed(created_ids[-3:]))

    assert page_two.total == 7
    assert [item.id for item in page_two.items] == list(reversed(created_ids[1:4]))


@pytest.mark.asyncio
async def test_list_snapshots_clamps_page_size_to_100(storage):
    executor_id = await _create_executor(storage, name="svc-clamp")
    await storage.create_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={"image": "acme/api:1"},
            trigger="pre_update",
        )
    )
    service = SnapshotService(storage)
    view = await service.list_snapshots(executor_id, page=1, page_size=500)
    assert view.page_size == 100


@pytest.mark.asyncio
async def test_get_snapshot_returns_none_for_foreign_executor(storage):
    executor_a = await _create_executor(storage, name="svc-foreign-a")
    executor_b = await _create_executor(storage, name="svc-foreign-b")
    snapshot_id = await storage.create_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_a,
            snapshot_data={"image": "acme/api:1"},
            trigger="pre_update",
        )
    )

    service = SnapshotService(storage)
    assert await service.get_snapshot(executor_a, snapshot_id) is not None
    assert await service.get_snapshot(executor_b, snapshot_id) is None


@pytest.mark.asyncio
async def test_get_snapshot_redacts_snapshot_data_on_read(storage):
    executor_id = await _create_executor(storage, name="svc-redact")
    snapshot_id = await storage.create_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={
                "image": "acme/api:1",
                "env": [
                    {"name": "LOG_LEVEL", "value": "info"},
                    {"name": "DB_PASSWORD", "value": "hunter2"},
                ],
                "token": "plaintext",
            },
            trigger="pre_update",
            image_at_capture="acme/api:1",
        )
    )

    service = SnapshotService(storage)
    detail = await service.get_snapshot(executor_id, snapshot_id, runtime_type="portainer")
    assert detail is not None
    assert detail.snapshot_data["token"] == REDACTED_MARKER
    db_env = next(
        entry for entry in detail.snapshot_data["env"] if entry["name"] == "DB_PASSWORD"
    )
    assert db_env["value"] == REDACTED_MARKER
    # Non-sensitive entries stay intact so the UI can display them.
    keep = next(
        entry for entry in detail.snapshot_data["env"] if entry["name"] == "LOG_LEVEL"
    )
    assert keep["value"] == "info"
    # Metadata fields are preserved on the detail view.
    assert detail.trigger == "pre_update"
    assert detail.image_at_capture == "acme/api:1"
    assert detail.unredacted_persisted is False


@pytest.mark.asyncio
async def test_redact_for_persist_returns_redacted_payload_and_flag(storage):
    service = SnapshotService(storage)
    payload = {
        "image": "acme/api:1",
        "env": [{"name": "DB_TOKEN", "value": "plaintext"}],
    }
    redacted, needs_marker = service.redact_for_persist(payload, runtime_type="portainer")
    assert redacted["env"][0]["value"] == REDACTED_MARKER
    # The redactor covers Portainer + K8s thoroughly; the flag stays False.
    assert needs_marker is False
