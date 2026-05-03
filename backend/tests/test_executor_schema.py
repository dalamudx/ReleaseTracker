from datetime import datetime, timedelta
from typing import Literal

import aiosqlite
import pytest

from helpers.executor_runtime import (
    create_portainer_runtime_connection,
    save_docker_tracker_config,
)
from releasetracker.config import (
    Channel,
    ExecutorConfig,
    ExecutorServiceBinding,
    MaintenanceWindowConfig,
    RuntimeConnectionConfig,
)
from releasetracker.models import ExecutorRunHistory, ExecutorSnapshot, ExecutorStatus


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


async def _create_portainer_runtime_connection(
    storage,
    *,
    name: str = "portainer-prod",
) -> int:
    return await create_portainer_runtime_connection(storage, name=name)


async def _create_tracker_source_id(storage, name: str = "nginx") -> int:
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


@pytest.mark.asyncio
async def test_executor_config_round_trip_with_status(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_source_id = await _create_tracker_source_id(storage)
    maintenance = MaintenanceWindowConfig(
        timezone="UTC",
        days_of_week=[1, 3, 5],
        start_time="02:00",
        end_time="03:00",
    )
    executor_config = ExecutorConfig(
        name="docker-nginx",
        runtime_type="docker",
        runtime_connection_id=runtime_id,
        tracker_name="nginx",
        tracker_source_id=tracker_source_id,
        enabled=True,
        update_mode="maintenance_window",
        image_reference_mode="tag",
        target_ref={"mode": "container", "container_id": "container-1", "image": "nginx:1.25"},
        maintenance_window=maintenance,
        description="nginx executor",
    )

    executor_id = await storage.save_executor_config(executor_config)
    saved_executor = await storage.get_executor_config(executor_id)
    saved_by_name = await storage.get_executor_config_by_name("docker-nginx")
    all_executors = await storage.get_all_executor_configs()
    paginated = await storage.get_executor_configs_paginated(skip=0, limit=10)
    total = await storage.get_total_executor_configs_count()

    assert saved_executor is not None
    assert saved_by_name is not None
    assert saved_executor.model_dump() == saved_by_name.model_dump()
    assert total == 1
    assert [executor.id for executor in all_executors] == [executor_id]
    assert [executor.id for executor in paginated] == [executor_id]
    assert saved_executor.model_dump() == {
        **executor_config.model_dump(),
        "id": executor_id,
    }

    status = ExecutorStatus(
        executor_id=executor_id,
        last_run_at=datetime(2026, 3, 24, 12, 0, 0),
        last_result="success",
        last_error=None,
        last_version="1.26.0",
    )
    await storage.update_executor_status(status)

    fetched_status = await storage.get_executor_status(executor_id)
    all_status = await storage.get_all_executor_status()

    assert fetched_status is not None
    assert fetched_status.executor_id == executor_id
    assert fetched_status.last_result == "success"
    assert fetched_status.last_version == "1.26.0"
    assert fetched_status.last_error is None
    assert fetched_status.last_run_at == datetime(2026, 3, 24, 12, 0, 0)
    assert len(all_status) == 1
    assert all_status[0].executor_id == executor_id


@pytest.mark.asyncio
async def test_executor_run_history_records_success_failure_and_skip(storage):
    runtime_id = await _create_runtime_connection(storage, name="prod-podman")
    tracker_source_id = await _create_tracker_source_id(storage)
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="podman-nginx",
            runtime_type="podman",
            runtime_connection_id=runtime_id,
            tracker_name="nginx",
            tracker_source_id=tracker_source_id,
            enabled=True,
            update_mode="immediate",
            target_ref={"mode": "container", "container_name": "nginx"},
            description="podman executor",
        )
    )

    start = datetime(2026, 3, 24, 12, 0, 0)
    success_run_id = await storage.create_executor_run(
        ExecutorRunHistory(
            executor_id=executor_id,
            started_at=start,
            finished_at=start + timedelta(minutes=1),
            status="success",
            from_version="1.24.0",
            to_version="1.25.0",
            message="updated successfully",
        )
    )
    failed_run_id = await storage.create_executor_run(
        ExecutorRunHistory(
            executor_id=executor_id,
            started_at=start + timedelta(minutes=2),
            status="failed",
            from_version="1.25.0",
            to_version=None,
            message="image pull failed",
        )
    )
    skipped_run_id = await storage.create_executor_run(
        ExecutorRunHistory(
            executor_id=executor_id,
            started_at=start + timedelta(minutes=4),
            status="skipped",
            from_version="1.25.0",
            to_version="1.25.0",
            message="outside maintenance window",
        )
    )

    finalized_failed_at = start + timedelta(minutes=3)
    finalized_skipped_at = start + timedelta(minutes=5)
    await storage.finalize_executor_run(
        failed_run_id,
        status="failed",
        finished_at=finalized_failed_at,
        to_version="1.25.0",
        message="runtime rejected update",
    )
    await storage.finalize_executor_run(
        skipped_run_id,
        status="skipped",
        finished_at=finalized_skipped_at,
        to_version="1.25.0",
        message="window closed",
    )

    success_run = await storage.get_executor_run(success_run_id)
    failed_run = await storage.get_executor_run(failed_run_id)
    skipped_run = await storage.get_executor_run(skipped_run_id)
    history = await storage.get_executor_run_history(executor_id, skip=0, limit=10)
    latest = await storage.get_latest_executor_run(executor_id)

    assert success_run is not None
    assert success_run.status == "success"
    assert success_run.finished_at == start + timedelta(minutes=1)
    assert success_run.to_version == "1.25.0"

    assert failed_run is not None
    assert failed_run.status == "failed"
    assert failed_run.finished_at == finalized_failed_at
    assert failed_run.to_version == "1.25.0"
    assert failed_run.message == "runtime rejected update"

    assert skipped_run is not None
    assert skipped_run.status == "skipped"
    assert skipped_run.finished_at == finalized_skipped_at
    assert skipped_run.message == "window closed"

    assert [run.status for run in history] == ["skipped", "failed", "success"]
    assert latest is not None
    assert latest.id == skipped_run_id


@pytest.mark.asyncio
async def test_executor_persistence_keeps_tracker_and_release_tables_untouched(storage):
    runtime_id = await _create_runtime_connection(storage, name="prod-k8s")
    tracker_source_id = await _create_tracker_source_id(storage)
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="k8s-nginx",
            runtime_type="kubernetes",
            runtime_connection_id=runtime_id,
            tracker_name="nginx",
            tracker_source_id=tracker_source_id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "kubernetes_workload",
                "namespace": "default",
                "kind": "Deployment",
                "name": "nginx",
            },
            service_bindings=[
                ExecutorServiceBinding(
                    service="nginx", tracker_source_id=tracker_source_id, channel_name="stable"
                ),
            ],
            description="k8s executor",
        )
    )
    await storage.update_executor_status(
        ExecutorStatus(
            executor_id=executor_id,
            last_run_at=datetime(2026, 3, 24, 14, 0, 0),
            last_result="skipped",
            last_error=None,
            last_version="1.25.0",
        )
    )
    run_id = await storage.create_executor_run(
        ExecutorRunHistory(
            executor_id=executor_id,
            started_at=datetime(2026, 3, 24, 14, 0, 0),
            status="skipped",
            from_version="1.25.0",
            to_version="1.25.0",
            message="manual mode",
        )
    )
    await storage.finalize_executor_run(
        run_id,
        status="skipped",
        finished_at=datetime(2026, 3, 24, 14, 1, 0),
        to_version="1.25.0",
        message="manual mode",
    )

    async with aiosqlite.connect(storage.db_path) as db:
        counts = {}
        for table_name in (
            "executors",
            "executor_status",
            "executor_run_history",
            "trackers",
            "tracker_status",
        ):
            cursor = await db.execute(f"SELECT COUNT(*) FROM {table_name}")
            row = await cursor.fetchone()
            counts[table_name] = row[0] if row else None

    assert counts["executors"] == 1
    assert counts["executor_status"] == 1
    assert counts["executor_run_history"] == 1
    assert counts["trackers"] == 1
    assert counts["tracker_status"] == 0


@pytest.mark.asyncio
async def test_executor_snapshot_save_overwrites_latest_snapshot_for_same_executor(storage):
    runtime_id = await _create_runtime_connection(storage, name="prod-snapshot")
    tracker_source_id = await _create_tracker_source_id(storage)
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="snapshot-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name="nginx",
            tracker_source_id=tracker_source_id,
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-snapshot"},
            description="snapshot executor",
        )
    )

    await storage.save_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={
                "image": "nginx:1.24.0",
                "target": {"container_id": "container-snapshot"},
                "runtime": {"type": "docker"},
            },
        )
    )
    first_snapshot = await storage.get_executor_snapshot(executor_id)

    await storage.save_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={
                "image": "nginx:1.25.0",
                "target": {"container_id": "container-snapshot"},
                "runtime": {"type": "docker", "region": "prod"},
            },
        )
    )
    latest_snapshot = await storage.get_executor_snapshot(executor_id)

    assert first_snapshot is not None
    assert latest_snapshot is not None
    assert latest_snapshot.executor_id == executor_id
    assert latest_snapshot.snapshot_data == {
        "image": "nginx:1.25.0",
        "target": {"container_id": "container-snapshot"},
        "runtime": {"type": "docker", "region": "prod"},
    }
    assert latest_snapshot.created_at == first_snapshot.created_at
    assert latest_snapshot.updated_at >= first_snapshot.updated_at

    async with aiosqlite.connect(storage.db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM executor_snapshots WHERE executor_id = ?", (executor_id,)
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == 1


@pytest.mark.asyncio
async def test_executor_snapshot_storage_stays_separate_from_run_history(storage):
    runtime_id = await _create_runtime_connection(storage, name="prod-separation")
    tracker_source_id = await _create_tracker_source_id(storage)
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="snapshot-separation-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name="nginx",
            tracker_source_id=tracker_source_id,
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-separation"},
            description="snapshot separation executor",
        )
    )

    await storage.save_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={
                "image": "nginx:1.25.0",
                "target": {"container_id": "container-separation"},
                "recovery": {"strategy": "recreate"},
            },
        )
    )
    run_id = await storage.create_executor_run(
        ExecutorRunHistory(
            executor_id=executor_id,
            started_at=datetime(2026, 3, 24, 15, 0, 0),
            status="success",
            from_version="1.24.0",
            to_version="1.25.0",
            message="updated successfully",
        )
    )

    snapshot = await storage.get_executor_snapshot(executor_id)
    run = await storage.get_executor_run(run_id)

    assert snapshot is not None
    assert snapshot.snapshot_data == {
        "image": "nginx:1.25.0",
        "target": {"container_id": "container-separation"},
        "recovery": {"strategy": "recreate"},
    }
    assert run is not None
    assert run.message == "updated successfully"
    assert run.to_version == "1.25.0"

    async with aiosqlite.connect(storage.db_path) as db:
        counts = {}
        for table_name in ("executor_snapshots", "executor_run_history"):
            cursor = await db.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE executor_id = ?", (executor_id,)
            )
            row = await cursor.fetchone()
            counts[table_name] = row[0] if row else None

    assert counts["executor_snapshots"] == 1
    assert counts["executor_run_history"] == 1


def test_executor_target_ref_container_requires_explicit_mode():
    with pytest.raises(ValueError, match="target_ref.mode is required"):
        ExecutorConfig(
            name="docker-mode-required",
            runtime_type="docker",
            runtime_connection_id=1,
            tracker_name="nginx",
            tracker_source_id=1,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={"container_id": "container-legacy"},
        )


@pytest.mark.parametrize("runtime_type", ["docker", "podman"])
def test_executor_config_rejects_unknown_target_ref_mode(runtime_type):
    with pytest.raises(
        ValueError,
        match="target_ref.mode must be one of: container, portainer_stack, docker_compose, kubernetes_workload",
    ):
        ExecutorConfig(
            name=f"{runtime_type}-unknown-mode-executor",
            runtime_type=runtime_type,
            runtime_connection_id=1,
            tracker_name=f"{runtime_type}-unknown",
            tracker_source_id=1,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "legacy_mode",
            },
        )


def test_executor_target_ref_portainer_stack_round_trip_shape_is_explicit():
    config = ExecutorConfig(
        name="portainer-stack",
        runtime_type="portainer",
        runtime_connection_id=1,
        tracker_name="nginx",
        tracker_source_id=1,
        channel_name="stable",
        enabled=True,
        update_mode="manual",
        target_ref={
            "mode": "portainer_stack",
            "endpoint_id": 2,
            "stack_id": 11,
            "stack_name": "release-stack",
            "stack_type": "standalone",
            "entrypoint": "docker-compose.yml",
            "project_path": "/data/compose/11",
        },
        service_bindings=[
            ExecutorServiceBinding(service="api", tracker_source_id=1, channel_name="stable"),
        ],
    )

    assert config.target_ref == {
        "mode": "portainer_stack",
        "endpoint_id": 2,
        "stack_id": 11,
        "stack_name": "release-stack",
        "stack_type": "standalone",
        "entrypoint": "docker-compose.yml",
        "project_path": "/data/compose/11",
    }


def test_executor_target_ref_portainer_stack_rejects_missing_identity_fields():
    with pytest.raises(ValueError, match="stack_name"):
        ExecutorConfig(
            name="portainer-stack-invalid",
            runtime_type="portainer",
            runtime_connection_id=1,
            tracker_name="nginx",
            tracker_source_id=1,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "portainer_stack",
                "endpoint_id": 2,
                "stack_id": 11,
                "stack_type": "standalone",
            },
        )


@pytest.mark.parametrize("runtime_type", ["docker", "podman", "kubernetes"])
def test_executor_target_ref_portainer_stack_rejects_non_portainer_runtime(runtime_type):
    with pytest.raises(ValueError, match="only supported when runtime_type is 'portainer'"):
        ExecutorConfig(
            name=f"{runtime_type}-portainer-stack-invalid",
            runtime_type=runtime_type,
            runtime_connection_id=1,
            tracker_name="nginx",
            tracker_source_id=1,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "portainer_stack",
                "endpoint_id": 2,
                "stack_id": 11,
                "stack_name": "release-stack",
                "stack_type": "standalone",
            },
        )


def test_executor_target_ref_portainer_runtime_rejects_non_portainer_stack_mode():
    with pytest.raises(
        ValueError, match="portainer runtime requires target_ref.mode 'portainer_stack'"
    ):
        ExecutorConfig(
            name="portainer-container-mode-invalid",
            runtime_type="portainer",
            runtime_connection_id=1,
            tracker_name="nginx",
            tracker_source_id=1,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "container",
                "container_name": "nginx",
            },
        )


def test_executor_target_ref_portainer_stack_accepts_grouped_service_bindings():
    config = ExecutorConfig(
        name="portainer-stack-bindings",
        runtime_type="portainer",
        runtime_connection_id=1,
        tracker_name="portainer-api",
        tracker_source_id=1,
        channel_name="stable",
        enabled=True,
        update_mode="manual",
        target_ref={
            "mode": "portainer_stack",
            "endpoint_id": 2,
            "stack_id": 11,
            "stack_name": "release-stack",
            "stack_type": "standalone",
        },
        service_bindings=[
            ExecutorServiceBinding(service="api", tracker_source_id=1, channel_name="stable"),
            ExecutorServiceBinding(service="worker", tracker_source_id=2, channel_name="stable"),
        ],
    )

    assert config.target_ref["mode"] == "portainer_stack"
    assert [binding.model_dump() for binding in config.service_bindings] == [
        {"service": "api", "tracker_source_id": 1, "channel_name": "stable"},
        {"service": "worker", "tracker_source_id": 2, "channel_name": "stable"},
    ]


@pytest.mark.parametrize("runtime_type", ["docker", "podman"])
def test_executor_target_ref_docker_compose_accepts_grouped_service_bindings(runtime_type):
    config = ExecutorConfig(
        name=f"{runtime_type}-compose-bindings",
        runtime_type=runtime_type,
        runtime_connection_id=1,
        tracker_name="compose-api",
        tracker_source_id=1,
        channel_name="stable",
        enabled=True,
        update_mode="manual",
        target_ref={
            "mode": "docker_compose",
            "project": "release-stack",
            "working_dir": "/srv/release-stack",
            "config_files": ["compose.yml", "compose.prod.yml"],
            "services": [
                {"service": "api", "image": "ghcr.io/acme/api:1.2.3"},
                {"service": "worker", "image": "ghcr.io/acme/worker:1.0.0"},
            ],
        },
        service_bindings=[
            ExecutorServiceBinding(service="api", tracker_source_id=1, channel_name="stable"),
            ExecutorServiceBinding(service="worker", tracker_source_id=2, channel_name="stable"),
        ],
    )

    assert config.target_ref == {
        "mode": "docker_compose",
        "project": "release-stack",
        "working_dir": "/srv/release-stack",
        "config_files": ["compose.yml", "compose.prod.yml"],
        "services": [
            {"service": "api", "image": "ghcr.io/acme/api:1.2.3"},
            {"service": "worker", "image": "ghcr.io/acme/worker:1.0.0"},
        ],
    }


@pytest.mark.parametrize(
    ("runtime_type", "message"),
    [
        ("kubernetes", "only supported when runtime_type is 'docker' or 'podman'"),
        ("portainer", "portainer runtime requires target_ref.mode 'portainer_stack'"),
    ],
)
def test_executor_target_ref_docker_compose_rejects_non_docker_runtime(runtime_type, message):
    with pytest.raises(ValueError, match=message):
        ExecutorConfig(
            name=f"{runtime_type}-docker-compose-invalid",
            runtime_type=runtime_type,
            runtime_connection_id=1,
            tracker_name="nginx",
            tracker_source_id=1,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "docker_compose",
                "project": "release-stack",
                "working_dir": "/srv/release-stack",
                "config_files": ["compose.yml"],
            },
            service_bindings=[
                ExecutorServiceBinding(service="api", tracker_source_id=1, channel_name="stable"),
            ],
        )


def test_executor_target_ref_kubernetes_workload_accepts_grouped_service_bindings():
    config = ExecutorConfig(
        name="k8s-workload-bindings",
        runtime_type="kubernetes",
        runtime_connection_id=1,
        tracker_name="k8s-api",
        tracker_source_id=1,
        channel_name="stable",
        enabled=True,
        update_mode="manual",
        target_ref={
            "mode": "kubernetes_workload",
            "namespace": "apps",
            "kind": "Deployment",
            "name": "worker",
            "services": [
                {"service": "api", "image": "ghcr.io/acme/api:1.2.3"},
                {"service": "sidecar", "image": "ghcr.io/acme/sidecar:1.0.0"},
            ],
            "service_count": 2,
        },
        service_bindings=[
            ExecutorServiceBinding(service="api", tracker_source_id=1, channel_name="stable"),
            ExecutorServiceBinding(service="sidecar", tracker_source_id=2, channel_name="stable"),
        ],
    )

    assert config.target_ref == {
        "mode": "kubernetes_workload",
        "namespace": "apps",
        "kind": "Deployment",
        "name": "worker",
        "services": [
            {"service": "api", "image": "ghcr.io/acme/api:1.2.3"},
            {"service": "sidecar", "image": "ghcr.io/acme/sidecar:1.0.0"},
        ],
        "service_count": 2,
    }
    assert [binding.model_dump() for binding in config.service_bindings] == [
        {"service": "api", "tracker_source_id": 1, "channel_name": "stable"},
        {"service": "sidecar", "tracker_source_id": 2, "channel_name": "stable"},
    ]


@pytest.mark.parametrize("runtime_type", ["docker", "podman", "portainer"])
def test_executor_target_ref_kubernetes_workload_rejects_non_kubernetes_runtime(runtime_type):
    message = (
        "portainer runtime requires target_ref.mode 'portainer_stack'"
        if runtime_type == "portainer"
        else "only supported when runtime_type is 'kubernetes'"
    )
    with pytest.raises(ValueError, match=message):
        ExecutorConfig(
            name=f"{runtime_type}-kubernetes-workload-invalid",
            runtime_type=runtime_type,
            runtime_connection_id=1,
            tracker_name="nginx",
            tracker_source_id=1,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "kubernetes_workload",
                "namespace": "apps",
                "kind": "Deployment",
                "name": "api",
            },
            service_bindings=[
                ExecutorServiceBinding(service="api", tracker_source_id=1, channel_name="stable"),
            ],
        )


@pytest.mark.asyncio
async def test_executor_service_bindings_table_enforces_unique_service_per_executor(storage):
    runtime_id = await _create_portainer_runtime_connection(
        storage,
        name="portainer-group-unique-runtime",
    )
    tracker_source_id = await _create_tracker_source_id(storage, name="portainer-group-unique")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="portainer-group-unique-executor",
            runtime_type="portainer",
            runtime_connection_id=runtime_id,
            tracker_name="portainer-group-unique",
            tracker_source_id=tracker_source_id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "portainer_stack",
                "endpoint_id": 2,
                "stack_id": 11,
                "stack_name": "release-stack",
                "stack_type": "standalone",
            },
            service_bindings=[
                ExecutorServiceBinding(
                    service="api", tracker_source_id=tracker_source_id, channel_name="stable"
                )
            ],
        )
    )

    async with aiosqlite.connect(storage.db_path) as db:
        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                """
                INSERT INTO executor_service_bindings
                (executor_id, service, tracker_source_id, channel_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    executor_id,
                    "api",
                    tracker_source_id,
                    "stable",
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                ),
            )


@pytest.mark.asyncio
async def test_portainer_executor_storage_round_trip_preserves_target_identity_bindings_and_status_history(
    storage,
):
    runtime_id = await _create_portainer_runtime_connection(
        storage, name="portainer-storage-roundtrip"
    )
    api_source_id = await _create_tracker_source_id(storage, name="portainer-storage-api")
    worker_source_id = await _create_tracker_source_id(storage, name="portainer-storage-worker")

    target_ref = {
        "mode": "portainer_stack",
        "endpoint_id": 2,
        "stack_id": 11,
        "stack_name": "release-stack",
        "stack_type": "standalone",
        "entrypoint": "docker-compose.yml",
        "project_path": "/data/compose/11",
    }

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="portainer-storage-executor",
            runtime_type="portainer",
            runtime_connection_id=runtime_id,
            tracker_name="portainer-storage-api",
            tracker_source_id=api_source_id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref=target_ref,
            service_bindings=[
                ExecutorServiceBinding(
                    service="api",
                    tracker_source_id=api_source_id,
                    channel_name="stable",
                ),
                ExecutorServiceBinding(
                    service="worker",
                    tracker_source_id=worker_source_id,
                    channel_name="stable",
                ),
            ],
        )
    )

    saved = await storage.get_executor_config(executor_id)
    saved_by_name = await storage.get_executor_config_by_name("portainer-storage-executor")
    all_executors = await storage.get_all_executor_configs()
    paginated = await storage.get_executor_configs_paginated(skip=0, limit=10)

    assert saved is not None
    assert saved_by_name is not None
    assert saved.target_ref == target_ref
    assert saved_by_name.target_ref == target_ref
    assert [binding.model_dump() for binding in saved.service_bindings] == [
        {"service": "api", "tracker_source_id": api_source_id, "channel_name": "stable"},
        {"service": "worker", "tracker_source_id": worker_source_id, "channel_name": "stable"},
    ]
    assert [binding.model_dump() for binding in saved_by_name.service_bindings] == [
        {"service": "api", "tracker_source_id": api_source_id, "channel_name": "stable"},
        {"service": "worker", "tracker_source_id": worker_source_id, "channel_name": "stable"},
    ]
    assert [
        binding.model_dump()
        for binding in next(
            item for item in all_executors if item.id == executor_id
        ).service_bindings
    ] == [
        {"service": "api", "tracker_source_id": api_source_id, "channel_name": "stable"},
        {"service": "worker", "tracker_source_id": worker_source_id, "channel_name": "stable"},
    ]
    assert [
        binding.model_dump()
        for binding in next(item for item in paginated if item.id == executor_id).service_bindings
    ] == [
        {"service": "api", "tracker_source_id": api_source_id, "channel_name": "stable"},
        {"service": "worker", "tracker_source_id": worker_source_id, "channel_name": "stable"},
    ]
    assert next(item for item in all_executors if item.id == executor_id).target_ref == target_ref
    assert next(item for item in paginated if item.id == executor_id).target_ref == target_ref

    await storage.update_executor_status(
        ExecutorStatus(
            executor_id=executor_id,
            last_run_at=datetime(2026, 4, 1, 12, 0, 0),
            last_result="failed",
            last_error=(
                "portainer-stack run finished: 0 updated, 0 skipped, 1 failed; "
                "details: worker: failed (Portainer stack service image missing: worker)"
            ),
            last_version=None,
        )
    )
    await storage.create_executor_run(
        ExecutorRunHistory(
            executor_id=executor_id,
            started_at=datetime(2026, 4, 1, 12, 0, 0),
            finished_at=datetime(2026, 4, 1, 12, 1, 0),
            status="failed",
            from_version=None,
            to_version=None,
            message=(
                "portainer-stack run finished: 0 updated, 0 skipped, 1 failed; "
                "details: worker: failed (Portainer stack service image missing: worker)"
            ),
        )
    )

    status = await storage.get_executor_status(executor_id)
    history = await storage.get_executor_run_history(executor_id, skip=0, limit=10)

    assert status is not None
    assert status.last_result == "failed"
    assert status.last_error is not None
    assert "Portainer stack service image missing: worker" in status.last_error
    assert status.last_version is None

    assert len(history) == 1
    assert history[0].status == "failed"
    assert history[0].from_version is None
    assert history[0].to_version is None
    assert history[0].message is not None
    assert "Portainer stack service image missing: worker" in history[0].message


@pytest.mark.asyncio
async def test_executor_desired_state_survives_storage_restart(storage):
    runtime_id = await _create_runtime_connection(storage, name="desired-state-runtime")
    tracker_source_id = await _create_tracker_source_id(storage, name="desired-state-tracker")
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="desired-state-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name="desired-state-tracker",
            tracker_source_id=tracker_source_id,
            channel_name="stable",
            enabled=True,
            update_mode="immediate",
            target_ref={"mode": "container", "container_id": "desired-state-container"},
        )
    )

    enqueued = await storage.upsert_executor_desired_state(
        executor_id=executor_id,
        desired_state_revision="desired-state-tracker:stable:rev-1",
        desired_target={
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "identity_key": "2.1.0@sha256:1111111111111111111111111111111111111111111111111111111111111111",
        },
    )
    assert enqueued is True

    await storage.close()
    reopened_storage = type(storage)(storage.db_path, system_key_manager=storage.system_key_manager)
    try:
        persisted_state = await reopened_storage.get_executor_desired_state(executor_id)
        assert persisted_state is not None
        assert persisted_state.executor_id == executor_id
        assert persisted_state.pending is True
        assert persisted_state.desired_state_revision == "desired-state-tracker:stable:rev-1"
        assert persisted_state.desired_target["identity_key"].startswith("2.1.0@sha256:")
    finally:
        await reopened_storage.close()


@pytest.mark.asyncio
async def test_executor_desired_state_dedupes_and_coalesces_for_grouped_executor(storage):
    runtime_id = await _create_portainer_runtime_connection(storage, name="desired-state-portainer")
    api_source_id = await _create_tracker_source_id(storage, name="desired-state-api")
    worker_source_id = await _create_tracker_source_id(storage, name="desired-state-worker")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="desired-state-grouped-executor",
            runtime_type="portainer",
            runtime_connection_id=runtime_id,
            tracker_name="desired-state-api",
            tracker_source_id=api_source_id,
            channel_name="stable",
            enabled=True,
            update_mode="immediate",
            target_ref={
                "mode": "portainer_stack",
                "endpoint_id": 2,
                "stack_id": 11,
                "stack_name": "desired-state-stack",
                "stack_type": "standalone",
            },
            service_bindings=[
                ExecutorServiceBinding(
                    service="api",
                    tracker_source_id=api_source_id,
                    channel_name="stable",
                ),
                ExecutorServiceBinding(
                    service="worker",
                    tracker_source_id=worker_source_id,
                    channel_name="stable",
                ),
            ],
        )
    )

    first_enqueue = await storage.upsert_executor_desired_state(
        executor_id=executor_id,
        desired_state_revision="grouped:rev-1",
        desired_target={
            "services": {
                "api": "2.0.0@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "worker": "2.0.0@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            }
        },
    )
    duplicate_enqueue = await storage.upsert_executor_desired_state(
        executor_id=executor_id,
        desired_state_revision="grouped:rev-1",
        desired_target={
            "services": {
                "api": "2.0.0@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "worker": "2.0.0@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            }
        },
    )
    coalesced_enqueue = await storage.upsert_executor_desired_state(
        executor_id=executor_id,
        desired_state_revision="grouped:rev-2",
        desired_target={
            "services": {
                "api": "2.0.1@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
                "worker": "2.0.0@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            }
        },
    )

    assert first_enqueue is True
    assert duplicate_enqueue is False
    assert coalesced_enqueue is True

    desired_state = await storage.get_executor_desired_state(executor_id)
    assert desired_state is not None
    assert desired_state.desired_state_revision == "grouped:rev-2"
    assert desired_state.pending is True
    assert desired_state.desired_target["services"]["api"].startswith("2.0.1@sha256:")

    async with aiosqlite.connect(storage.db_path) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM executor_desired_state WHERE executor_id = ?",
                (executor_id,),
            )
        ).fetchone()
    assert row is not None
    assert row[0] == 1


@pytest.mark.asyncio
async def test_projection_trigger_enqueue_persists_current_truth_desired_state(storage):
    runtime_id = await _create_runtime_connection(storage, name="projection-trigger-runtime")
    tracker_source_id = await _create_tracker_source_id(storage, name="projection-trigger-tracker")
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="projection-trigger-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name="projection-trigger-tracker",
            tracker_source_id=tracker_source_id,
            channel_name="stable",
            enabled=True,
            update_mode="immediate",
            target_ref={"mode": "container", "container_id": "projection-trigger-container"},
        )
    )

    first_enqueue = await storage.enqueue_executor_projection_trigger_work(
        executor_id=executor_id,
        tracker_name="projection-trigger-tracker",
        previous_version="1.0.0",
        current_version="1.1.0",
    )
    duplicate_enqueue = await storage.enqueue_executor_projection_trigger_work(
        executor_id=executor_id,
        tracker_name="projection-trigger-tracker",
        previous_version="1.0.0",
        current_version="1.1.0",
    )
    updated_enqueue = await storage.enqueue_executor_projection_trigger_work(
        executor_id=executor_id,
        tracker_name="projection-trigger-tracker",
        previous_version="1.1.0",
        current_version="1.2.0",
    )

    assert first_enqueue is True
    assert duplicate_enqueue is False
    assert updated_enqueue is True

    desired_state = await storage.get_executor_desired_state(executor_id)
    assert desired_state is not None
    assert desired_state.pending is True
    assert desired_state.desired_state_revision == "projection-trigger-tracker:1.2.0"
    assert desired_state.desired_target == {
        "tracker_name": "projection-trigger-tracker",
        "previous_version": "1.1.0",
        "current_version": "1.2.0",
        "previous_identity_key": None,
        "current_identity_key": "1.2.0",
    }


@pytest.mark.asyncio
async def test_executor_desired_state_claim_defer_and_complete_semantics(storage):
    runtime_id = await _create_runtime_connection(storage, name="desired-state-claim-runtime")
    tracker_source_id = await _create_tracker_source_id(storage, name="desired-state-claim-tracker")
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="desired-state-claim-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name="desired-state-claim-tracker",
            tracker_source_id=tracker_source_id,
            channel_name="stable",
            enabled=True,
            update_mode="immediate",
            target_ref={"mode": "container", "container_id": "desired-state-claim-container"},
        )
    )

    now = datetime(2026, 4, 27, 12, 0, 0)
    first_eligible_at = now + timedelta(minutes=30)
    await storage.upsert_executor_desired_state(
        executor_id=executor_id,
        desired_state_revision="claim:rev-1",
        desired_target={"identity_key": "3.1.0@sha256:" + "d" * 64},
        next_eligible_at=first_eligible_at,
    )

    pending_before_window = await storage.list_pending_executor_desired_states(now=now)
    claimed_before_window = await storage.claim_pending_executor_desired_states(
        claimed_by="executor-worker-a",
        now=now,
    )
    assert pending_before_window == []
    assert claimed_before_window == []

    claimed = await storage.claim_pending_executor_desired_states(
        claimed_by="executor-worker-a",
        now=first_eligible_at,
        lease_seconds=120,
    )
    assert len(claimed) == 1
    assert claimed[0].executor_id == executor_id
    assert claimed[0].claimed_by == "executor-worker-a"

    second_claim = await storage.claim_pending_executor_desired_states(
        claimed_by="executor-worker-b",
        now=first_eligible_at,
    )
    assert second_claim == []

    wrong_complete = await storage.complete_executor_desired_state(
        executor_id,
        claimed_by="executor-worker-b",
    )
    assert wrong_complete is False

    deferred_at = first_eligible_at + timedelta(minutes=20)
    deferred = await storage.defer_executor_desired_state(
        executor_id,
        next_eligible_at=deferred_at,
        claimed_by="executor-worker-a",
    )
    assert deferred is True

    claimed_before_deferred_window = await storage.claim_pending_executor_desired_states(
        claimed_by="executor-worker-b",
        now=deferred_at - timedelta(minutes=1),
    )
    assert claimed_before_deferred_window == []

    claimed_after_deferred_window = await storage.claim_pending_executor_desired_states(
        claimed_by="executor-worker-b",
        now=deferred_at,
    )
    assert len(claimed_after_deferred_window) == 1
    assert claimed_after_deferred_window[0].executor_id == executor_id

    completed = await storage.complete_executor_desired_state(
        executor_id,
        claimed_by="executor-worker-b",
    )
    assert completed is True

    finalized_state = await storage.get_executor_desired_state(executor_id)
    assert finalized_state is not None
    assert finalized_state.pending is False
    assert finalized_state.claimed_by is None
    assert finalized_state.last_completed_revision == "claim:rev-1"

    deduped_after_completion = await storage.upsert_executor_desired_state(
        executor_id=executor_id,
        desired_state_revision="claim:rev-1",
        desired_target={"identity_key": "3.1.0@sha256:" + "d" * 64},
    )
    assert deduped_after_completion is False
