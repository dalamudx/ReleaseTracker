import asyncio
from datetime import datetime, timezone
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import httpx
import pytest

from helpers.executor_adapters import MutableFakeRuntimeAdapter
from helpers.executor_docker import FakeDockerClient, FakeDockerContainer
from helpers.executor_runtime import (
    create_runtime_connection,
    save_docker_tracker_config,
    seed_docker_release,
)

config_path = Path(__file__).resolve().parents[1] / "src" / "releasetracker" / "config.py"
spec = importlib.util.spec_from_file_location("releasetracker.config", config_path)
assert spec is not None
assert spec.loader is not None
config_module = importlib.util.module_from_spec(spec)
sys.modules["releasetracker.config"] = config_module
spec.loader.exec_module(config_module)

ExecutorConfig = config_module.ExecutorConfig
MaintenanceWindowConfig = config_module.MaintenanceWindowConfig
RuntimeConnectionConfig = config_module.RuntimeConnectionConfig
TrackerConfig = config_module.TrackerConfig
from releasetracker.executor_scheduler import ExecutorScheduler  # noqa: E402
from releasetracker.executors.docker import DockerRuntimeAdapter  # noqa: E402
from releasetracker.executors.kubernetes import KubernetesRuntimeAdapter  # noqa: E402
from releasetracker.executors.podman import PodmanRuntimeAdapter  # noqa: E402
from releasetracker.executors.portainer import PortainerRuntimeAdapter  # noqa: E402
from releasetracker.executors.base import (  # noqa: E402
    BaseRuntimeAdapter,
    RuntimeMutationError,
    RuntimeUpdateResult,
)
from releasetracker.models import (  # noqa: E402
    AggregateTracker,
    Credential,
    Release,
    ReleaseChannel,
    TrackerSource,
)


class FakeAdapter(MutableFakeRuntimeAdapter):
    def __init__(
        self,
        runtime_connection,
        *,
        current_image: str,
        storage=None,
        executor_id: int | None = None,
        invalid_snapshot: bool = False,
        fail_after_destructive_update: bool = False,
        recovery_should_fail: bool = False,
    ):
        super().__init__(
            runtime_connection,
            current_image=current_image,
            invalid_target_key="invalid",
            invalid_target_value=True,
            invalid_target_message="invalid target",
            include_runtime_type_in_snapshot=True,
            storage=storage,
            executor_id=executor_id,
            invalid_snapshot=invalid_snapshot,
            fail_after_destructive_update=fail_after_destructive_update,
            recovery_should_fail=recovery_should_fail,
            recovery_old_image="broken:partial",
            recovery_message="runtime recovered from snapshot",
        )


class SlowFakeAdapter(FakeAdapter):
    def __init__(self, runtime_connection, *, current_image: str):
        super().__init__(runtime_connection, current_image=current_image)
        self.started_runs = 0
        self.completed_runs = 0
        self.release_update = asyncio.Event()

    async def update_image(self, target_ref, new_image: str):
        self.started_runs += 1
        await self.release_update.wait()
        result = await super().update_image(target_ref, new_image)
        self.completed_runs += 1
        return result


@pytest.mark.asyncio
async def test_desired_state_tick_runs_reconcile_in_background(storage):
    scheduler = ExecutorScheduler(storage)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_claim(*, claimed_by, now, limit, lease_seconds):
        started.set()
        await release.wait()
        return []

    original_claim = storage.claim_pending_executor_desired_states
    storage.claim_pending_executor_desired_states = slow_claim
    try:
        await scheduler._reconcile_pending_desired_states_tick()
        await asyncio.wait_for(started.wait(), timeout=1)
        assert scheduler._desired_state_consume_lock.locked()

        await scheduler._reconcile_pending_desired_states_tick()
        assert len(scheduler._background_tasks) == 1

        release.set()
        await asyncio.gather(*scheduler._background_tasks)
    finally:
        storage.claim_pending_executor_desired_states = original_claim
        await scheduler.shutdown()


class FakeDockerComposeAdapter(DockerRuntimeAdapter):
    def __init__(
        self, runtime_connection, *, current_images: dict[str, str], fail_update: str | None = None
    ):
        super().__init__(runtime_connection, client=FakeDockerClient([]))
        self.current_images = current_images
        self.fail_update = fail_update
        self.update_calls: list[tuple[dict, dict[str, str]]] = []

    async def validate_target_ref(self, target_ref):
        await super().validate_target_ref(target_ref)

    async def fetch_compose_service_images(self, target_ref):
        return dict(self.current_images)

    async def update_compose_services(self, target_ref, service_target_images):
        if self.fail_update:
            raise RuntimeError(self.fail_update)
        self.update_calls.append((target_ref, dict(service_target_images)))
        self.current_images.update(service_target_images)
        return RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image="; ".join(
                f"{service}={image}" for service, image in sorted(service_target_images.items())
            ),
            message="fake docker compose updated",
        )


class FakeKubernetesWorkloadAdapter(KubernetesRuntimeAdapter):
    def __init__(
        self, runtime_connection, *, current_images: dict[str, str], fail_update: str | None = None
    ):
        super().__init__(runtime_connection, apps_api=None)
        self.current_images = current_images
        self.fail_update = fail_update
        self.update_calls: list[tuple[dict, dict[str, str]]] = []

    async def validate_target_ref(self, target_ref):
        if target_ref.get("mode") != "kubernetes_workload":
            raise ValueError("invalid kubernetes workload target")

    async def fetch_workload_service_images(self, target_ref) -> dict[str, str | None]:
        return dict(self.current_images)

    async def update_workload_services(self, target_ref, service_target_images):
        if self.fail_update:
            raise RuntimeError(self.fail_update)
        self.update_calls.append((target_ref, dict(service_target_images)))
        self.current_images.update(service_target_images)
        return RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image="; ".join(
                f"{service}={image}" for service, image in sorted(service_target_images.items())
            ),
            message="fake kubernetes workload updated",
        )


class FakeHelmReleaseAdapter(KubernetesRuntimeAdapter):
    def __init__(
        self,
        runtime_connection,
        *,
        current_chart_version: str | None,
        fail_update: str | None = None,
    ):
        super().__init__(runtime_connection, apps_api=None)
        self.current_chart_version = current_chart_version
        self.fail_update = fail_update
        self.update_calls: list[tuple[dict, str, str, str | None]] = []
        self.snapshot_calls: list[dict] = []

    async def validate_target_ref(self, target_ref):
        if target_ref.get("mode") != "helm_release":
            raise ValueError("invalid helm release target")

    async def get_helm_release_version(self, target_ref):
        return self.current_chart_version

    async def capture_helm_release_snapshot(self, target_ref):
        self.snapshot_calls.append(target_ref)
        return {
            "mode": "helm_release",
            "namespace": target_ref["namespace"],
            "release_name": target_ref["release_name"],
            "chart_name": "certd",
            "chart_version": self.current_chart_version or "1.0.0",
            "release": {"name": target_ref["release_name"]},
        }

    async def validate_helm_release_snapshot(self, target_ref, snapshot):
        await super().validate_helm_release_snapshot(target_ref, snapshot)

    async def upgrade_helm_release(self, target_ref, *, chart_ref, chart_version, repo_url):
        self.update_calls.append((target_ref, chart_ref, chart_version, repo_url))
        if self.fail_update:
            raise RuntimeMutationError(self.fail_update, destructive_started=True)
        self.current_chart_version = chart_version
        return RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image=chart_version,
            message="fake helm upgraded",
        )

    async def recover_helm_release_from_snapshot(self, target_ref, snapshot):
        self.current_chart_version = snapshot["chart_version"]
        return RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image=snapshot["chart_version"],
            message="fake helm recovered",
        )


class FakePodmanComposeAdapter(PodmanRuntimeAdapter):
    def __init__(
        self,
        runtime_connection,
        *,
        current_images: dict[str, str],
        fail_update: str | None = None,
    ):
        super().__init__(runtime_connection, client=FakeDockerClient([]))
        self.current_images = current_images
        self.fail_update = fail_update
        self.update_calls: list[tuple[dict, dict[str, str]]] = []

    async def validate_target_ref(self, target_ref):
        await super().validate_target_ref(target_ref)

    async def fetch_compose_service_images(self, target_ref):
        return dict(self.current_images)

    async def update_compose_services(self, target_ref, service_target_images):
        self.update_calls.append((target_ref, dict(service_target_images)))
        if self.fail_update:
            raise RuntimeMutationError(self.fail_update, destructive_started=True)
        self.current_images.update(service_target_images)
        return RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image="; ".join(
                f"{service}={image}" for service, image in sorted(service_target_images.items())
            ),
            message="pod-aware grouped compose update completed",
        )


class FakePortainerHttpResponse:
    def __init__(self, *, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class FakePortainerHttpClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls: list[tuple[str, str, dict | None, dict | None]] = []

    async def request(self, method: str, path: str, params=None, json=None, timeout=None):
        normalized_params = dict(params) if isinstance(params, dict) else None
        normalized_json = dict(json) if isinstance(json, dict) else None
        self.calls.append((method, path, normalized_params, normalized_json))
        key = (method, path, normalized_params.get("endpointId") if normalized_params else None)
        response = self.responses.get(key)
        if response is None:
            raise AssertionError(f"unexpected Portainer request: {key}")
        if isinstance(response, Exception):
            raise response
        return response


def _maintenance_executor(
    executor_id: int,
    *,
    start_time: str,
    end_time: str,
    days_of_week: list[int] | None = None,
) -> ExecutorConfig:
    return ExecutorConfig(
        id=executor_id,
        name=f"maintenance-{executor_id}",
        runtime_type="docker",
        runtime_connection_id=1,
        tracker_name="tracker",
        enabled=True,
        update_mode="maintenance_window",
        target_ref={"mode": "container", "container_id": f"container-{executor_id}"},
        maintenance_window=MaintenanceWindowConfig(
            timezone="UTC",
            days_of_week=days_of_week or [],
            start_time=start_time,
            end_time=end_time,
        ),
    )


@pytest.mark.asyncio
async def test_release_history_cleanup_segments_merge_continuous_windows(storage):
    now = datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc)
    scheduler = ExecutorScheduler(storage, now_provider=lambda: now)
    configs = [
        _maintenance_executor(1, start_time="02:00", end_time="03:00"),
        _maintenance_executor(2, start_time="02:30", end_time="04:00"),
        _maintenance_executor(3, start_time="04:00", end_time="06:00"),
        _maintenance_executor(4, start_time="08:00", end_time="09:00"),
    ]

    segments = scheduler._collect_maintenance_cleanup_segments(configs)

    assert segments[0].start_at.isoformat() == "2026-03-24T02:00:00+00:00"
    assert segments[0].end_at.isoformat() == "2026-03-24T06:00:00+00:00"
    assert segments[0].executor_ids == frozenset({1, 2, 3})
    assert segments[1].start_at.isoformat() == "2026-03-24T08:00:00+00:00"
    assert segments[1].end_at.isoformat() == "2026-03-24T09:00:00+00:00"
    assert segments[1].executor_ids == frozenset({4})


@pytest.mark.asyncio
async def test_release_history_cleanup_schedule_defaults_to_0200_without_windows(storage):
    scheduler = ExecutorScheduler(
        storage,
        now_provider=lambda: datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc),
    )

    await scheduler.refresh_release_history_cleanup_schedule([])

    job = scheduler.scheduler_host.get_job("release_history_cleanup", "default_0200")
    assert job is not None
    assert str(job.trigger).startswith("cron[")


@pytest.mark.asyncio
async def test_replace_tag_on_current_image_keeps_repo(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "frontend"
    await save_docker_tracker_config(storage, name=tracker_name, image="should-not-use")
    await _create_tracker_release(storage, tracker_name, "2.1.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="frontend-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            image_selection_mode="replace_tag_on_current_image",
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-front"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("2.1.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="ghcr.io/acme/frontend:2.0.0",
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success", outcome.message
    assert adapter.update_calls == ["ghcr.io/acme/frontend:2.1.0"]


@pytest.mark.asyncio
async def test_replace_tag_on_current_image_keeps_registry_port_without_existing_tag(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "frontend-port"
    await save_docker_tracker_config(storage, name=tracker_name, image="unused-for-replace-mode")
    await _create_tracker_release(storage, tracker_name, "2.1.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="frontend-port-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            image_selection_mode="replace_tag_on_current_image",
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-front-port"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("2.1.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="registry.local:5000/acme/frontend",
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success", outcome.message
    assert adapter.update_calls == ["registry.local:5000/acme/frontend:2.1.0"]


@pytest.mark.asyncio
async def test_replace_tag_on_current_image_with_digest_keeps_registry_port(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "frontend-digest-port"
    await save_docker_tracker_config(storage, name=tracker_name, image="unused-for-replace-mode")
    await _create_tracker_release(storage, tracker_name, "2.1.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="frontend-digest-port-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            image_selection_mode="replace_tag_on_current_image",
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-front-digest-port"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("2.1.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="registry.local:5000/acme/frontend@sha256:abcdef123456",
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert adapter.update_calls == ["registry.local:5000/acme/frontend:2.1.0"]


@pytest.mark.asyncio
async def test_use_tracker_image_and_tag_overrides_repo(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "backend"
    await save_docker_tracker_config(storage, name=tracker_name, image="ghcr.io/acme/backend")
    await _create_tracker_release(storage, tracker_name, "3.0.1")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="backend-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            image_selection_mode="use_tracker_image_and_tag",
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-back"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("3.0.1", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="docker.io/library/backend:2.9.0",
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert adapter.update_calls == ["ghcr.io/acme/backend:3.0.1"]


@pytest.mark.asyncio
async def test_use_tracker_image_and_tag_requires_tracker_image(storage, monkeypatch):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "missing-image"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="ghcr.io/acme/missing-image",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "1.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="missing-image-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            channel_name="stable",
            enabled=True,
            image_selection_mode="use_tracker_image_and_tag",
            update_mode="immediate",
            target_ref={"mode": "container", "container_id": "container-missing"},
        )
    )

    scheduler = ExecutorScheduler(storage)

    class MissingImageTrackerSource:
        id = 999999
        source_key = "legacy"
        source_type = "docker"
        enabled = True
        source_config = {}

    monkeypatch.setattr(
        scheduler,
        "_resolve_tracker_binding",
        AsyncMock(return_value=(tracker_name, MissingImageTrackerSource())),
    )
    monkeypatch.setattr(
        scheduler,
        "_resolve_tracker_latest_target",
        AsyncMock(return_value=("1.0.0", None)),
    )

    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="missing:0.1.0",
    )
    scheduler._adapters[executor_id] = adapter

    config = await storage.get_executor_config(executor_id)
    outcome = await scheduler._execute_executor(config, manual=False)

    assert outcome.status == "failed"
    assert outcome.message == "tracker source image is required for tracker image selection mode"


@pytest.mark.asyncio
async def test_scheduler_fails_when_tracker_source_binding_is_stale_even_if_tracker_is_resolvable(
    storage,
):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "stale-binding-app"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="ghcr.io/acme/stale-binding-app",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "1.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="stale-binding-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=999999,
            channel_name="stable",
            enabled=True,
            image_selection_mode="use_tracker_image_and_tag",
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-stale-binding"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("1.1.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="ghcr.io/acme/stale-binding-app:0.9.0",
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "failed"
    assert outcome.message == "tracker source binding missing"
    assert adapter.update_calls == []


@pytest.mark.asyncio
async def test_executor_scheduler_reject_tracker_name_inference_without_tracker_source_id(
    storage,
):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "tracker-name-inference-app"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="ghcr.io/acme/tracker-name-inference-app",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "1.0.0")

    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None
    await storage.update_aggregate_tracker(
        AggregateTracker(
            id=aggregate_tracker.id,
            name=tracker_name,
            primary_changelog_source_key="origin",
            created_at=aggregate_tracker.created_at,
            sources=[
                TrackerSource(
                    source_key="origin",
                    source_type="container",
                    source_rank=0,
                    source_config={
                        "image": "ghcr.io/acme/tracker-name-inference-app",
                        "registry": "ghcr.io",
                    },
                ),
            ],
        )
    )

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="tracker-name-inference-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-tracker-name-inference"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("1.1.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="ghcr.io/acme/tracker-name-inference-app:0.9.0",
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "failed"
    assert outcome.message == "tracker source binding missing"
    assert adapter.update_calls == []


async def test_scheduler_fails_when_bound_tracker_source_is_disabled(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "disabled-binding-app"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="ghcr.io/acme/disabled-binding-app",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "1.0.0")

    legacy_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert legacy_tracker is not None
    await storage.update_aggregate_tracker(
        AggregateTracker(
            id=legacy_tracker.id,
            name=tracker_name,
            primary_changelog_source_key="origin",
            created_at=legacy_tracker.created_at,
            sources=[
                TrackerSource(
                    source_key="origin",
                    source_type="container",
                    enabled=False,
                    source_rank=0,
                    source_config={
                        "image": "ghcr.io/acme/disabled-binding-app",
                        "registry": "ghcr.io",
                    },
                )
            ],
        )
    )
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None
    disabled_source = aggregate_tracker.sources[0]

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="disabled-binding-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=disabled_source.id,
            channel_name="stable",
            enabled=True,
            image_selection_mode="use_tracker_image_and_tag",
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-disabled-binding"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("1.1.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="ghcr.io/acme/disabled-binding-app:0.9.0",
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "failed"
    assert outcome.message == "tracker source binding missing"
    assert adapter.update_calls == []


@pytest.mark.asyncio
async def test_scheduler_marks_run_failed_when_runtime_connection_is_disabled(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "runtime-disabled-app"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="runtime-disabled-app",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "1.1.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="runtime-disabled-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-runtime-disabled"},
        )
    )

    runtime_connection = await storage.get_runtime_connection(runtime_id)
    assert runtime_connection is not None
    await storage.update_runtime_connection(
        runtime_id,
        runtime_connection.model_copy(update={"enabled": False}),
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("1.2.3", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="runtime-disabled-app:1.0.0",
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "failed"
    assert outcome.message == "runtime connection disabled"
    assert adapter.update_calls == []


async def _create_runtime_connection(storage) -> int:
    return await create_runtime_connection(
        storage,
        name="prod-docker",
        runtime_type="docker",
        description="primary runtime",
    )


async def _create_portainer_runtime_connection(storage) -> int:
    credential_id = await storage.create_credential(
        Credential(
            name="portainer-prod-api-key",
            type="portainer_runtime",
            secrets={"api_key": "portainer-api-key"},
        )
    )
    return await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name="portainer-prod",
            type="portainer",
            enabled=True,
            credential_id=credential_id,
            config={"base_url": "https://portainer.example", "endpoint_id": 2},
            secrets={},
            description="portainer runtime",
        )
    )


async def _create_tracker_release(
    storage,
    tracker_name: str,
    version: str,
    *,
    prerelease: bool = False,
    published_at: datetime | None = None,
) -> None:
    release_published_at = published_at or datetime.now(timezone.utc)
    await seed_docker_release(
        storage,
        tracker_name=tracker_name,
        version=version,
        prerelease=prerelease,
        published_at=release_published_at,
    )

    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    if aggregate_tracker is None or aggregate_tracker.id is None:
        return

    runtime_source = storage._select_runtime_source(aggregate_tracker)
    if runtime_source is None or runtime_source.id is None:
        return

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
                prerelease=prerelease,
                channel_name="canary" if prerelease else "stable",
            )
        ],
    )


async def _create_bound_tracker_release(
    storage,
    tracker_name: str,
    version: str,
    *,
    commit_sha: str | None,
    published_at: datetime,
    prerelease: bool = False,
) -> None:
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None
    runtime_source = next(
        (
            source
            for source in aggregate_tracker.sources
            if source.source_type == "container" and source.enabled
        ),
        None,
    )
    assert runtime_source is not None
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
                published_at=published_at,
                url=f"https://example.com/{tracker_name}/{version}",
                prerelease=prerelease,
                commit_sha=commit_sha,
                channel_name="canary" if prerelease else "stable",
            )
        ],
    )


async def _create_bound_helm_release(
    storage,
    tracker_name: str,
    *,
    app_version: str,
    chart_version: str,
    published_at: datetime,
) -> None:
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None and aggregate_tracker.id is not None
    runtime_source = next(
        (
            source
            for source in aggregate_tracker.sources
            if source.source_type == "helm" and source.enabled
        ),
        None,
    )
    assert runtime_source is not None
    await storage.save_source_observations(
        aggregate_tracker.id,
        runtime_source,
        [
            Release(
                tracker_name=tracker_name,
                tracker_type="helm",
                name=tracker_name,
                tag_name=chart_version,
                version=app_version,
                app_version=app_version,
                chart_version=chart_version,
                published_at=published_at,
                url=f"https://charts.example/{tracker_name}",
                prerelease=False,
                channel_name="stable",
            )
        ],
    )


async def _get_tracker_source_id(storage, tracker_name: str) -> int:
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None
    runtime_source = storage._select_runtime_source(aggregate_tracker)
    assert runtime_source is not None and runtime_source.id is not None

    if not runtime_source.release_channels:
        tracker_config = await storage.get_tracker_config(tracker_name)
        assert tracker_config is not None
        await storage.update_aggregate_tracker(
            AggregateTracker(
                id=aggregate_tracker.id,
                name=aggregate_tracker.name,
                enabled=aggregate_tracker.enabled,
                primary_changelog_source_key=aggregate_tracker.primary_changelog_source_key,
                created_at=aggregate_tracker.created_at,
                changelog_policy=aggregate_tracker.changelog_policy,
                description=aggregate_tracker.description,
                sources=[
                    (
                        source.model_copy(
                            update={
                                "release_channels": [
                                    ReleaseChannel(
                                        release_channel_key=f"{source.source_key}-{channel.name}",
                                        name=channel.name,
                                        type=channel.type,
                                        enabled=channel.enabled,
                                        include_pattern=channel.include_pattern,
                                        exclude_pattern=channel.exclude_pattern,
                                    )
                                    for channel in tracker_config.channels
                                ]
                            }
                        )
                        if source.id == runtime_source.id
                        else source
                    )
                    for source in aggregate_tracker.sources
                ],
            )
        )
        aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
        assert aggregate_tracker is not None
        runtime_source = storage._select_runtime_source(aggregate_tracker)
        assert runtime_source is not None and runtime_source.id is not None

    return runtime_source.id


def _mock_scheduler_target(scheduler: ExecutorScheduler, *targets: tuple[str, str | None]) -> None:
    if len(targets) == 1:
        scheduler._resolve_tracker_latest_target = AsyncMock(return_value=targets[0])
        return

    scheduler._resolve_tracker_latest_target = AsyncMock(side_effect=list(targets))


@pytest.mark.asyncio
async def test_docker_replace_mode_prefers_digest_identity_when_same_version_has_no_digest_row(
    storage,
):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "digest-prefer"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="ghcr.io/acme/digest-prefer",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    digest = "sha256:" + "a" * 64
    await _create_bound_tracker_release(
        storage,
        tracker_name,
        "2.4.0",
        commit_sha=None,
        published_at=datetime(2026, 3, 25, tzinfo=timezone.utc),
    )
    await _create_bound_tracker_release(
        storage,
        tracker_name,
        "2.4.0",
        commit_sha=digest,
        published_at=datetime(2026, 3, 24, tzinfo=timezone.utc),
    )

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="digest-prefer-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            image_selection_mode="replace_tag_on_current_image",
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-digest-prefer"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("2.4.0", digest))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="ghcr.io/acme/digest-prefer:2.3.0",
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert adapter.update_calls == [f"ghcr.io/acme/digest-prefer@{digest}"]


@pytest.mark.asyncio
async def test_docker_tag_reference_mode_ignores_available_digest(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "tag-reference-mode"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="ghcr.io/acme/tag-reference-mode",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    digest = "sha256:" + "b" * 64
    await _create_bound_tracker_release(
        storage,
        tracker_name,
        "2.5.0",
        commit_sha=digest,
        published_at=datetime(2026, 3, 24, tzinfo=timezone.utc),
    )

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="tag-reference-mode-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            image_selection_mode="replace_tag_on_current_image",
            image_reference_mode="tag",
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-tag-reference-mode"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("2.5.0", digest))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="ghcr.io/acme/tag-reference-mode:2.4.0",
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert adapter.update_calls == ["ghcr.io/acme/tag-reference-mode:2.5.0"]


@pytest.mark.asyncio
async def test_docker_tracker_image_mode_falls_back_to_tag_when_digest_missing(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "digest-fallback"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="ghcr.io/acme/digest-fallback",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_bound_tracker_release(
        storage,
        tracker_name,
        "3.0.1",
        commit_sha=None,
        published_at=datetime(2026, 3, 24, tzinfo=timezone.utc),
    )

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="digest-fallback-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            image_selection_mode="use_tracker_image_and_tag",
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-digest-fallback"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("3.0.1", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="docker.io/library/digest-fallback:2.9.9",
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert adapter.update_calls == ["ghcr.io/acme/digest-fallback:3.0.1"]


@pytest.mark.asyncio
async def test_docker_same_version_digest_flip_updates_target_image(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "digest-flip"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="ghcr.io/acme/digest-flip",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    old_digest = "sha256:" + "1" * 64
    new_digest = "sha256:" + "2" * 64
    await _create_bound_tracker_release(
        storage,
        tracker_name,
        "1.0.0",
        commit_sha=old_digest,
        published_at=datetime(2026, 3, 24, tzinfo=timezone.utc),
    )

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="digest-flip-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            image_selection_mode="replace_tag_on_current_image",
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-digest-flip"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("1.0.0", old_digest), ("1.0.0", new_digest))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image=f"ghcr.io/acme/digest-flip@{old_digest}",
    )
    scheduler._adapters[executor_id] = adapter

    first_outcome = await scheduler.run_executor_now(executor_id)
    assert first_outcome.status == "skipped"
    assert adapter.update_calls == []

    await _create_bound_tracker_release(
        storage,
        tracker_name,
        "1.0.0",
        commit_sha=new_digest,
        published_at=datetime(2026, 3, 25, tzinfo=timezone.utc),
    )

    second_outcome = await scheduler.run_executor_now(executor_id)
    assert second_outcome.status == "success"
    assert adapter.update_calls == [f"ghcr.io/acme/digest-flip@{new_digest}"]


def test_non_docker_target_rendering_ignores_digest_and_keeps_tag_semantics(storage):
    scheduler = ExecutorScheduler(storage)
    rendered_image = scheduler._build_target_image(
        current_image="ghcr.io/acme/non-docker:1.0.0",
        target_version="1.1.0",
        target_digest="sha256:" + "f" * 64,
        executor_config=ExecutorConfig(
            name="non-docker-render",
            runtime_type="docker",
            runtime_connection_id=1,
            tracker_name="non-docker-render",
            enabled=True,
            image_selection_mode="replace_tag_on_current_image",
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-non-docker-render"},
        ),
        tracker_source=TrackerSource(
            source_key="github-origin",
            source_type="github",
            source_config={"repo": "acme/non-docker-render"},
        ),
        tracker_source_type="github",
    )

    assert rendered_image == "ghcr.io/acme/non-docker:1.1.0"


async def _wait_for_run_status(
    storage,
    run_id: int,
    expected_status: str,
    *,
    timeout: float = 1.0,
    interval: float = 0.01,
):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last_status = None
    while loop.time() < deadline:
        run_record = await storage.get_executor_run(run_id)
        if run_record is not None:
            last_status = run_record.status
            if last_status == expected_status:
                return run_record
        await asyncio.sleep(interval)
    raise AssertionError(
        f"run {run_id} did not reach status '{expected_status}' within {timeout}s (last: {last_status})"
    )


async def _wait_for_executor_idle(
    scheduler: ExecutorScheduler,
    executor_id: int,
    *,
    timeout: float = 1.0,
    interval: float = 0.01,
) -> None:
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if executor_id not in scheduler._running_executor_ids:
            return
        await asyncio.sleep(interval)


async def _wait_for_adapter_run_count(
    adapter: SlowFakeAdapter,
    *,
    attribute: str,
    expected_count: int,
    timeout: float = 1.0,
    interval: float = 0.01,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if getattr(adapter, attribute) >= expected_count:
            return
        await asyncio.sleep(interval)
    raise AssertionError(
        f"adapter.{attribute} did not reach {expected_count} within {timeout}s "
        f"(last: {getattr(adapter, attribute)})"
    )


@pytest.mark.asyncio
async def test_manual_executor_run_updates_image_and_status(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "nginx"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="nginx",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "1.1.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="docker-nginx",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-1", "image": "nginx"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("1.1.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="ghcr.io/library/nginx:1.0.0",
        storage=storage,
        executor_id=executor_id,
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert adapter.update_calls == ["ghcr.io/library/nginx:1.1.0"]

    status = await storage.get_executor_status(executor_id)
    assert status is not None
    assert status.last_result == "success"
    assert status.last_version == "ghcr.io/library/nginx:1.1.0"

    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].status == "success"
    assert history[0].from_version == "ghcr.io/library/nginx:1.0.0"
    assert history[0].to_version == "ghcr.io/library/nginx:1.1.0"

    snapshot = await storage.get_executor_snapshot(executor_id)
    assert snapshot is not None
    assert snapshot.snapshot_data == {
        "runtime_type": "docker",
        "image": "ghcr.io/library/nginx:1.0.0",
        "target_ref": {"mode": "container", "container_id": "container-1"},
    }
    assert adapter.snapshot_seen_before_update is not None
    assert adapter.snapshot_seen_before_update.snapshot_data == snapshot.snapshot_data


@pytest.mark.asyncio
async def test_manual_mode_executor_is_not_auto_executed_from_desired_state(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "manual-only"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="manual-only",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "1.1.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="manual-only-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "container",
                "container_id": "container-manual-only",
                "image": "manual-only",
            },
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("9.9.9", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="manual-only:1.0.0",
    )
    scheduler._adapters[executor_id] = adapter

    await storage.upsert_executor_desired_state(
        executor_id=executor_id,
        desired_state_revision="manual-only:rev-1",
        desired_target={"identity_key": "manual-only:1.1.0"},
    )

    await scheduler.start()
    await asyncio.sleep(0.05)

    assert adapter.update_calls == []
    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history == []
    status = await storage.get_executor_status(executor_id)
    assert status is None

    desired_state = await storage.get_executor_desired_state(executor_id)
    assert desired_state is not None
    assert desired_state.pending is True
    assert desired_state.next_eligible_at is not None

    await scheduler.shutdown()
    scheduler.scheduler_host.scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_invalid_snapshot_aborts_before_runtime_mutation(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "snapshot-abort"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="snapshot-abort",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "1.1.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="snapshot-abort-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "container",
                "container_id": "container-abort",
                "image": "snapshot-abort",
            },
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("1.1.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="snapshot-abort:1.0.0",
        invalid_snapshot=True,
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "failed"
    assert "snapshot must be a non-empty dict" in (outcome.message or "")
    assert adapter.update_calls == []
    assert await storage.get_executor_snapshot(executor_id) is None

    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].status == "failed"
    assert "snapshot must be a non-empty dict" in (history[0].message or "")


@pytest.mark.asyncio
async def test_failed_update_triggers_automatic_recovery_from_snapshot(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "recoverable-nginx"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="recoverable-nginx",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "1.1.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="recoverable-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "container",
                "container_id": "container-recover",
                "image": "recoverable-nginx",
            },
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("1.1.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="ghcr.io/acme/recoverable-nginx:1.0.0",
        storage=storage,
        executor_id=executor_id,
        fail_after_destructive_update=True,
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "failed"
    assert "simulated update failure after destructive steps" in (outcome.message or "")
    assert "automatic recovery succeeded: runtime recovered from snapshot" in (
        outcome.message or ""
    )
    assert adapter.update_calls == ["ghcr.io/acme/recoverable-nginx:1.1.0"]
    assert len(adapter.recovery_calls) == 1
    assert adapter.current_image == "ghcr.io/acme/recoverable-nginx:1.0.0"

    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].status == "failed"
    assert "automatic recovery succeeded" in (history[0].message or "")
    assert history[0].from_version == "ghcr.io/acme/recoverable-nginx:1.0.0"
    assert history[0].to_version == "ghcr.io/acme/recoverable-nginx:1.0.0"

    status = await storage.get_executor_status(executor_id)
    assert status is not None
    assert status.last_result == "failed"
    assert status.last_version == "ghcr.io/acme/recoverable-nginx:1.0.0"
    assert status.last_error == "simulated update failure after destructive steps"


@pytest.mark.asyncio
async def test_recovery_failure_is_surfaced_clearly(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "recovery-failure"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="recovery-failure",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "1.1.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="recovery-failure-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "container",
                "container_id": "container-recovery-fail",
                "image": "recovery-failure",
            },
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("1.1.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="recovery-failure:1.0.0",
        storage=storage,
        executor_id=executor_id,
        fail_after_destructive_update=True,
        recovery_should_fail=True,
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "failed"
    assert "simulated update failure after destructive steps" in (outcome.message or "")
    assert "automatic recovery failed: simulated recovery failure" in (outcome.message or "")
    assert adapter.update_calls == ["recovery-failure:1.1.0"]
    assert len(adapter.recovery_calls) == 1

    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].status == "failed"
    assert "automatic recovery failed" in (history[0].message or "")

    status = await storage.get_executor_status(executor_id)
    assert status is not None
    assert status.last_result == "failed"
    assert status.last_error == (
        "simulated update failure after destructive steps; recovery failed: simulated recovery failure"
    )


@pytest.mark.asyncio
async def test_docker_scheduler_recreates_container_when_image_only_update_is_unavailable(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "docker-fallback-success"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="docker-fallback-success",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "1.1.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="docker-fallback-success-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "container",
                "container_id": "docker-fallback-1",
                "image": "docker-fallback-success",
            },
        )
    )

    container = FakeDockerContainer(
        container_id="docker-fallback-1",
        name="docker-fallback-success",
        image="docker-fallback-success:1.0.0",
        attrs={
            "Config": {
                "Env": ["FOO=bar"],
                "Cmd": ["app"],
                "Entrypoint": None,
                "Labels": {"service": "docker-fallback-success"},
            },
            "HostConfig": {
                "PortBindings": {"80/tcp": [{"HostIp": "", "HostPort": "8080"}]},
                "Binds": ["/host/data:/data:rw"],
                "RestartPolicy": {"Name": "always"},
                "NetworkMode": "bridge",
            },
        },
    )
    client = FakeDockerClient([container])
    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("1.1.0", None))
    scheduler._adapters[executor_id] = DockerRuntimeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        client=client,
    )

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert outcome.to_version == "docker-fallback-success:1.1.0"
    assert client.images.pull_calls == ["docker-fallback-success:1.1.0"]
    assert len(container.stop_calls) == 1
    assert len(container.remove_calls) == 1
    assert len(client.containers.create_calls) == 1

    updated_config = await storage.get_executor_config(executor_id)
    assert updated_config is not None
    assert updated_config.target_ref["container_id"] == "recreated-1"

    snapshot = await storage.get_executor_snapshot(executor_id)
    assert snapshot is not None
    assert snapshot.snapshot_data["container_id"] == "docker-fallback-1"
    assert snapshot.snapshot_data["image"] == "docker-fallback-success:1.0.0"
    assert snapshot.snapshot_data["create_config"] == {
        "image": "docker-fallback-success:1.0.0",
        "name": "docker-fallback-success",
        "environment": ["FOO=bar"],
        "command": ["app"],
        "ports": {"80/tcp": ["", 8080]},
        "volumes": {"/host/data": {"bind": "/data", "mode": "rw"}},
        "restart_policy": {"Name": "always"},
        "network_mode": "bridge",
        "labels": {"service": "docker-fallback-success"},
    }


@pytest.mark.asyncio
async def test_docker_scheduler_recovers_from_snapshot_after_recreate_failure(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "docker-fallback-recovery"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="docker-fallback-recovery",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "1.1.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="docker-fallback-recovery-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "container",
                "container_id": "docker-recovery-1",
                "image": "docker-fallback-recovery",
            },
        )
    )

    container = FakeDockerContainer(
        container_id="docker-recovery-1",
        name="docker-fallback-recovery",
        image="docker-fallback-recovery:1.0.0",
        attrs={
            "Config": {
                "Env": ["FOO=bar"],
                "Cmd": ["app"],
                "Entrypoint": None,
                "Labels": {"service": "docker-fallback-recovery"},
            },
            "HostConfig": {
                "PortBindings": {"80/tcp": [{"HostIp": "", "HostPort": "8080"}]},
                "Binds": ["/host/data:/data:rw"],
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "NetworkMode": "bridge",
            },
        },
    )
    client = FakeDockerClient([container], create_should_fail=True)
    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("1.1.0", None))
    scheduler._adapters[executor_id] = DockerRuntimeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        client=client,
    )

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "failed"
    assert "docker update failed after destructive steps began" in (outcome.message or "")
    assert "automatic recovery succeeded: runtime recovered from snapshot" in (
        outcome.message or ""
    )
    assert client.images.pull_calls == [
        "docker-fallback-recovery:1.1.0",
        "docker-fallback-recovery:1.0.0",
    ]
    assert len(container.stop_calls) == 1
    assert len(container.remove_calls) == 1
    assert len(container.start_calls) == 1
    assert len(client.containers.create_calls) == 1

    updated_config = await storage.get_executor_config(executor_id)
    assert updated_config is not None
    assert updated_config.target_ref["container_id"] == "docker-recovery-1"

    status = await storage.get_executor_status(executor_id)
    assert status is not None
    assert status.last_result == "failed"
    assert status.last_version == "docker-fallback-recovery:1.0.0"
    assert status.last_error == (
        "docker update failed after destructive steps began: simulated docker recreate failure"
    )


@pytest.mark.asyncio
async def test_maintenance_window_desired_state_defers_without_skip_history_spam(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "redis"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="redis",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "7.4")

    outside_window_now = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="docker-redis",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="maintenance_window",
            target_ref={"mode": "container", "container_id": "container-2", "image": "redis"},
            maintenance_window=MaintenanceWindowConfig(
                timezone="UTC",
                days_of_week=[0],
                start_time="01:00",
                end_time="02:00",
            ),
        )
    )

    scheduler = ExecutorScheduler(storage, now_provider=lambda: outside_window_now)
    _mock_scheduler_target(scheduler, ("7.4", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="redis:7.2",
    )
    scheduler._adapters[executor_id] = adapter

    await storage.enqueue_executor_projection_trigger_work(
        executor_id=executor_id,
        tracker_name=tracker_name,
        previous_version="7.2",
        current_version="7.4",
    )

    await scheduler.reconcile_pending_desired_states()

    assert adapter.update_calls == []
    history = await storage.get_executor_run_history(executor_id, limit=10)
    assert history == []

    desired_state = await storage.get_executor_desired_state(executor_id)
    assert desired_state is not None
    assert desired_state.pending is True
    assert desired_state.next_eligible_at is not None
    assert desired_state.next_eligible_at > outside_window_now


@pytest.mark.asyncio
async def test_maintenance_window_pending_desired_state_runs_once_when_window_opens(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "redis-window-open"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="redis-window-open",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "7.4")

    current_now = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="docker-redis-window-open",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="maintenance_window",
            target_ref={
                "mode": "container",
                "container_id": "container-window-open",
                "image": "redis-window-open",
            },
            maintenance_window=MaintenanceWindowConfig(
                timezone="UTC",
                days_of_week=[0],
                start_time="01:00",
                end_time="02:00",
            ),
        )
    )

    scheduler = ExecutorScheduler(storage, now_provider=lambda: current_now)
    _mock_scheduler_target(scheduler, ("7.4", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="redis-window-open:7.2",
    )
    scheduler._adapters[executor_id] = adapter

    await storage.enqueue_executor_projection_trigger_work(
        executor_id=executor_id,
        tracker_name=tracker_name,
        previous_version="7.2",
        current_version="7.4",
    )

    await scheduler.reconcile_pending_desired_states()

    assert adapter.update_calls == []
    assert await storage.get_executor_run_history(executor_id, limit=10) == []
    deferred_state = await storage.get_executor_desired_state(executor_id)
    assert deferred_state is not None
    assert deferred_state.pending is True

    current_now = datetime(2026, 3, 30, 1, 30, tzinfo=timezone.utc)
    await scheduler.reconcile_pending_desired_states()

    assert adapter.update_calls == ["redis-window-open:7.4"]
    final_state = await storage.get_executor_desired_state(executor_id)
    assert final_state is not None
    assert final_state.pending is False
    history = await storage.get_executor_run_history(executor_id, limit=10)
    assert len(history) == 1
    assert history[0].status == "success"

    await scheduler.reconcile_pending_desired_states()
    history_after = await storage.get_executor_run_history(executor_id, limit=10)
    assert len(history_after) == 1


@pytest.mark.asyncio
async def test_maintenance_window_desired_state_uses_latest_system_timezone(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "redis-global-timezone"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="redis-global-timezone",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "7.4")
    await storage.set_setting("system.timezone", "Asia/Shanghai")

    # Sunday 17:30 UTC is Monday 01:30 in Asia/Shanghai. The executor-local
    # timezone is intentionally UTC to prove scheduling uses the global setting.
    current_now = datetime(2026, 3, 29, 17, 30, tzinfo=timezone.utc)
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="docker-redis-global-timezone",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="maintenance_window",
            target_ref={
                "mode": "container",
                "container_id": "container-global-timezone",
                "image": "redis-global-timezone",
            },
            maintenance_window=MaintenanceWindowConfig(
                timezone="UTC",
                days_of_week=[0],
                start_time="01:00",
                end_time="02:00",
            ),
        )
    )

    scheduler = ExecutorScheduler(storage, now_provider=lambda: current_now)
    scheduler._system_timezone = "UTC"
    _mock_scheduler_target(scheduler, ("7.4", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="redis-global-timezone:7.2",
    )
    scheduler._adapters[executor_id] = adapter

    await storage.enqueue_executor_projection_trigger_work(
        executor_id=executor_id,
        tracker_name=tracker_name,
        previous_version="7.2",
        current_version="7.4",
    )

    await scheduler.reconcile_pending_desired_states()

    assert adapter.update_calls == ["redis-global-timezone:7.4"]
    desired_state = await storage.get_executor_desired_state(executor_id)
    assert desired_state is not None
    assert desired_state.pending is False


@pytest.mark.asyncio
async def test_refresh_executor_enqueues_existing_projection_for_maintenance_window(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "window-created-after-projection"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="window-created-after-projection",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "8.0.0")

    current_now = datetime(2026, 3, 30, 1, 30, tzinfo=timezone.utc)
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="window-created-after-projection-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="maintenance_window",
            target_ref={
                "mode": "container",
                "container_id": "container-window-created-after-projection",
            },
            maintenance_window=MaintenanceWindowConfig(
                timezone="UTC",
                days_of_week=[0],
                start_time="01:00",
                end_time="02:00",
            ),
        )
    )

    scheduler = ExecutorScheduler(storage, now_provider=lambda: current_now)
    _mock_scheduler_target(scheduler, ("8.0.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="window-created-after-projection:7.0.0",
    )
    scheduler._adapters[executor_id] = adapter

    await scheduler.refresh_executor(executor_id)
    await scheduler.reconcile_pending_desired_states()

    assert adapter.update_calls == ["window-created-after-projection:8.0.0"]
    desired_state = await storage.get_executor_desired_state(executor_id)
    assert desired_state is not None
    assert desired_state.pending is False


@pytest.mark.asyncio
async def test_refresh_executor_defers_existing_projection_until_maintenance_window(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "window-created-before-open"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="window-created-before-open",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "9.0.0")

    current_now = datetime(2026, 3, 30, 0, 30, tzinfo=timezone.utc)
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="window-created-before-open-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="maintenance_window",
            target_ref={
                "mode": "container",
                "container_id": "container-window-created-before-open",
            },
            maintenance_window=MaintenanceWindowConfig(
                timezone="UTC",
                days_of_week=[0],
                start_time="01:00",
                end_time="02:00",
            ),
        )
    )

    scheduler = ExecutorScheduler(storage, now_provider=lambda: current_now)
    _mock_scheduler_target(scheduler, ("9.0.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="window-created-before-open:8.0.0",
    )
    scheduler._adapters[executor_id] = adapter

    await scheduler.refresh_executor(executor_id)
    await scheduler.reconcile_pending_desired_states()

    assert adapter.update_calls == []
    deferred_state = await storage.get_executor_desired_state(executor_id)
    assert deferred_state is not None
    assert deferred_state.pending is True
    assert deferred_state.next_eligible_at == datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc)

    current_now = datetime(2026, 3, 30, 1, 30, tzinfo=timezone.utc)
    await scheduler.reconcile_pending_desired_states()

    assert adapter.update_calls == ["window-created-before-open:9.0.0"]


@pytest.mark.asyncio
async def test_maintenance_window_executor_is_not_interval_scheduled_and_manual_run_bypasses_window(
    storage,
):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "window-manual-bypass"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="window-manual-bypass",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "4.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="window-manual-bypass-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="maintenance_window",
            target_ref={
                "mode": "container",
                "container_id": "container-window",
                "image": "window-manual-bypass",
            },
            maintenance_window=MaintenanceWindowConfig(
                timezone="UTC",
                days_of_week=[0],
                start_time="01:00",
                end_time="02:00",
            ),
        )
    )

    scheduler = ExecutorScheduler(
        storage, now_provider=lambda: datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)
    )
    _mock_scheduler_target(scheduler, ("4.0.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="window-manual-bypass:3.0.0",
    )
    scheduler._adapters[executor_id] = adapter

    config = await storage.get_executor_config(executor_id)
    assert config is not None

    await scheduler._add_or_update_executor_job(config)

    assert scheduler.scheduler_host.scheduler.get_job(f"executor_{executor_id}") is None

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert adapter.update_calls == ["window-manual-bypass:4.0.0"]
    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].status == "success"


@pytest.mark.asyncio
async def test_immediate_mode_executes(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "api"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="api",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "2.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="docker-api",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="immediate",
            target_ref={"mode": "container", "container_id": "container-3", "image": "api"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("2.0.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="api:1.9.0",
    )
    scheduler._adapters[executor_id] = adapter

    config = await storage.get_executor_config(executor_id)
    outcome = await scheduler._execute_executor(config, manual=False)

    assert outcome.status == "success"
    assert adapter.update_calls == ["api:2.0.0"]


@pytest.mark.asyncio
async def test_immediate_mode_refresh_enqueues_existing_projection_without_per_executor_interval_job(
    storage,
):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "interval-immediate"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="interval-immediate",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "2.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="interval-immediate-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="immediate",
            target_ref={
                "mode": "container",
                "container_id": "container-interval-immediate",
                "image": "interval-immediate",
            },
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("2.0.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="interval-immediate:1.0.0",
    )
    scheduler._adapters[executor_id] = adapter

    await scheduler.refresh_executor(executor_id)
    await scheduler.reconcile_pending_desired_states()

    job = scheduler.scheduler_host.scheduler.get_job(f"executor_{executor_id}")
    assert job is None
    assert adapter.update_calls == ["interval-immediate:2.0.0"]
    desired_state = await storage.get_executor_desired_state(executor_id)
    assert desired_state is not None
    assert desired_state.pending is False


@pytest.mark.asyncio
async def test_start_reconciles_pending_desired_state_without_startup_executor_sweep(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "startup-reconcile"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="startup-reconcile",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "2.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="startup-reconcile-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="immediate",
            target_ref={"mode": "container", "container_id": "container-startup-reconcile"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("2.0.0", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="startup-reconcile:1.0.0",
    )
    scheduler._adapters[executor_id] = adapter

    await storage.enqueue_executor_projection_trigger_work(
        executor_id=executor_id,
        tracker_name=tracker_name,
        previous_version="1.0.0",
        current_version="2.0.0",
    )

    await scheduler.start()

    desired_state = None
    for _ in range(50):
        desired_state = await storage.get_executor_desired_state(executor_id)
        if desired_state is not None and not desired_state.pending:
            break
        await asyncio.sleep(0.02)

    assert desired_state is not None
    assert desired_state.pending is False
    assert adapter.update_calls == ["startup-reconcile:2.0.0"]

    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].status == "success"
    assert history[0].from_version == "startup-reconcile:1.0.0"
    assert history[0].to_version == "startup-reconcile:2.0.0"

    await scheduler.shutdown()
    scheduler.scheduler_host.scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_manual_and_auto_paths_share_overlap_protection_during_desired_state_consumption(
    storage,
):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "manual-auto-overlap"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="manual-auto-overlap",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "6.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="manual-auto-overlap-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="immediate",
            target_ref={"mode": "container", "container_id": "container-manual-auto-overlap"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("6.0.0", None))
    adapter = SlowFakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="manual-auto-overlap:5.0.0",
    )
    scheduler._adapters[executor_id] = adapter

    await storage.enqueue_executor_projection_trigger_work(
        executor_id=executor_id,
        tracker_name=tracker_name,
        previous_version="5.0.0",
        current_version="6.0.0",
    )

    auto_task = asyncio.create_task(scheduler.reconcile_pending_desired_states())
    await _wait_for_adapter_run_count(adapter, attribute="started_runs", expected_count=1)

    with pytest.raises(ValueError, match="already running"):
        await scheduler.run_executor_now(executor_id)

    adapter.release_update.set()
    await auto_task
    await _wait_for_executor_idle(scheduler, executor_id)

    history = await storage.get_executor_run_history(executor_id, limit=10)
    assert len(history) == 1
    assert history[0].status == "success"
    desired_state = await storage.get_executor_desired_state(executor_id)
    assert desired_state is not None
    assert desired_state.pending is False


@pytest.mark.asyncio
async def test_start_does_not_register_run_history_pruning_job(storage, monkeypatch):
    scheduler = ExecutorScheduler(storage)
    created_coroutines = []

    def fake_create_task(coro):
        created_coroutines.append(coro)
        task = AsyncMock()
        task.add_done_callback = lambda callback: None
        return task

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    await scheduler.start()

    assert scheduler.scheduler_host.scheduler.get_job("prune_executor_run_history") is None
    assert scheduler.scheduler_host.scheduler.get_job("executor_desired_state_reconcile") is not None
    assert len(created_coroutines) == 1

    for coro in created_coroutines:
        coro.close()

    scheduler.scheduler_host.scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_noop_when_runtime_image_matches_target(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "worker"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="worker",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "3.2.1")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="docker-worker",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="immediate",
            target_ref={"mode": "container", "container_id": "container-4", "image": "worker"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("3.2.1", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="ghcr.io/acme/worker:3.2.1",
    )
    scheduler._adapters[executor_id] = adapter

    config = await storage.get_executor_config(executor_id)
    outcome = await scheduler._execute_executor(config, manual=False)

    assert outcome.status == "skipped"
    assert outcome.from_version == "ghcr.io/acme/worker:3.2.1"
    assert outcome.to_version == "ghcr.io/acme/worker:3.2.1"
    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].status == "skipped"
    assert history[0].message == "runtime already at target image"
    assert history[0].from_version == "ghcr.io/acme/worker:3.2.1"
    assert history[0].to_version == "ghcr.io/acme/worker:3.2.1"


@pytest.mark.asyncio
async def test_two_executors_on_same_tracker_resolve_different_channels(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "multi-channel-app"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="multi-channel-app",
        registry="registry-1.docker.io",
        channels=[
            config_module.Channel(name="stable", enabled=True, type="release"),
            config_module.Channel(name="canary", enabled=True, type="prerelease"),
        ],
    )
    legacy_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert legacy_tracker is not None
    await storage.update_aggregate_tracker(
        AggregateTracker(
            id=legacy_tracker.id,
            name=tracker_name,
            enabled=True,
            primary_changelog_source_key="image",
            created_at=legacy_tracker.created_at,
            sources=[
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=0,
                    source_config={
                        "image": "multi-channel-app",
                        "registry": "registry-1.docker.io",
                    },
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="image-stable",
                            name="stable",
                            type="release",
                            enabled=True,
                        ),
                        ReleaseChannel(
                            release_channel_key="image-canary",
                            name="canary",
                            type="prerelease",
                            enabled=True,
                        ),
                    ],
                )
            ],
        )
    )

    stable_release = Release(
        tracker_name=tracker_name,
        tracker_type="container",
        name="1.0.0",
        tag_name="1.0.0",
        version="1.0.0",
        published_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        url="https://example.com/1.0.0",
        prerelease=False,
    )
    canary_release = Release(
        tracker_name=tracker_name,
        tracker_type="container",
        name="2.0.0-rc1",
        tag_name="2.0.0-rc1",
        version="2.0.0-rc1",
        published_at=datetime(2026, 3, 20, tzinfo=timezone.utc),
        url="https://example.com/2.0.0-rc1",
        prerelease=True,
    )
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None
    runtime_source = storage._select_runtime_source(aggregate_tracker)
    assert runtime_source is not None
    await storage.save_source_observations(
        aggregate_tracker.id,
        runtime_source,
        [stable_release, canary_release],
    )

    stable_executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="stable-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "stable-container"},
        )
    )
    canary_executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="canary-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="canary",
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "canary-container"},
        )
    )

    rc = RuntimeConnectionConfig(
        name="prod-docker",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    scheduler = ExecutorScheduler(storage)
    stable_adapter = FakeAdapter(rc, current_image="multi-channel-app:0.9.0")
    canary_adapter = FakeAdapter(rc, current_image="multi-channel-app:0.9.0")
    scheduler._adapters[stable_executor_id] = stable_adapter
    scheduler._adapters[canary_executor_id] = canary_adapter

    stable_outcome = await scheduler.run_executor_now(stable_executor_id)
    canary_outcome = await scheduler.run_executor_now(canary_executor_id)

    assert stable_outcome.status == "success"
    assert stable_adapter.update_calls == ["multi-channel-app:1.0.0"]

    assert canary_outcome.status == "success"
    assert canary_adapter.update_calls == ["multi-channel-app:2.0.0-rc1"]


@pytest.mark.asyncio
async def test_source_bound_executor_canary_uses_bound_source_channels_when_tracker_channels_differ(
    storage,
):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "aether-source-bound-scope"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="ghcr.io/acme/aether",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )

    legacy_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert legacy_tracker is not None
    await storage.update_aggregate_tracker(
        AggregateTracker(
            id=legacy_tracker.id,
            name=tracker_name,
            enabled=True,
            primary_changelog_source_key="stable-origin",
            created_at=legacy_tracker.created_at,
            sources=[
                TrackerSource(
                    source_key="stable-origin",
                    source_type="container",
                    source_rank=0,
                    source_config={"image": "ghcr.io/acme/aether", "registry": "ghcr.io"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="stable-origin-stable",
                            name="stable",
                            type="release",
                            enabled=True,
                        )
                    ],
                ),
                TrackerSource(
                    source_key="aether-origin",
                    source_type="container",
                    source_rank=1,
                    source_config={"image": "ghcr.io/acme/aether", "registry": "ghcr.io"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="aether-origin-canary",
                            name="canary",
                            type="prerelease",
                            include_pattern=".*rc.*",
                            enabled=True,
                        )
                    ],
                ),
            ],
        )
    )

    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None and aggregate_tracker.id is not None
    stable_source = next(
        source for source in aggregate_tracker.sources if source.source_key == "stable-origin"
    )
    canary_source = next(
        source for source in aggregate_tracker.sources if source.source_key == "aether-origin"
    )
    assert stable_source.id is not None
    assert canary_source.id is not None

    await storage.save_source_observations(
        aggregate_tracker.id,
        stable_source,
        [
            Release(
                tracker_name=tracker_name,
                tracker_type="container",
                name="0.6.0",
                tag_name="0.6.0",
                version="0.6.0",
                published_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
                url=f"https://example.com/{tracker_name}/0.6.0",
                prerelease=False,
                channel_name="stable",
            )
        ],
    )
    await storage.save_source_observations(
        aggregate_tracker.id,
        canary_source,
        [
            Release(
                tracker_name=tracker_name,
                tracker_type="container",
                name="0.7.0-rc17",
                tag_name="0.7.0-rc17",
                version="0.7.0-rc17",
                published_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
                url=f"https://example.com/{tracker_name}/0.7.0-rc17",
                prerelease=True,
                channel_name="canary",
            ),
            Release(
                tracker_name=tracker_name,
                tracker_type="container",
                name="0.7.0-rc18",
                tag_name="0.7.0-rc18",
                version="0.7.0-rc18",
                published_at=datetime(2026, 4, 11, tzinfo=timezone.utc),
                url=f"https://example.com/{tracker_name}/0.7.0-rc18",
                prerelease=True,
                channel_name="canary",
            ),
        ],
    )

    scheduler = ExecutorScheduler(storage)

    aggregate_target = await scheduler._resolve_tracker_latest_target(
        tracker_name,
        "canary",
        tracker_source_id=None,
        tracker_source_type=None,
    )
    assert aggregate_target is None

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="aether-canary-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=canary_source.id,
            channel_name="canary",
            enabled=True,
            image_selection_mode="replace_tag_on_current_image",
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-aether"},
        )
    )

    rc = RuntimeConnectionConfig(
        name="prod-docker",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    adapter = FakeAdapter(rc, current_image="ghcr.io/acme/aether:0.7.0-rc16")
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert adapter.update_calls == ["ghcr.io/acme/aether:0.7.0-rc18"]


@pytest.mark.asyncio
async def test_executor_run_skips_when_bound_channel_has_no_releases(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "sparse-tracker"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="sparse-tracker",
        registry="registry-1.docker.io",
        channels=[
            config_module.Channel(name="stable", enabled=True, type="release"),
            config_module.Channel(name="canary", enabled=True, type="prerelease"),
        ],
    )
    await _create_tracker_release(storage, tracker_name, "1.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="canary-empty-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="canary",
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-sparse"},
        )
    )

    rc = RuntimeConnectionConfig(
        name="prod-docker",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    scheduler = ExecutorScheduler(storage)
    scheduler._adapters[executor_id] = FakeAdapter(rc, current_image="sparse-tracker:0.1.0")

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "skipped"
    assert outcome.message == "tracker has no versions"


@pytest.mark.asyncio
async def test_executor_run_does_not_resolve_target_from_canonical_when_projection_empty(storage):
    tracker_name = "canonical-only-executor"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image=f"ghcr.io/acme/{tracker_name}",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )

    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None and aggregate_tracker.id is not None
    runtime_source = storage._select_runtime_source(aggregate_tracker)
    assert runtime_source is not None

    await storage.save_source_observations(
        aggregate_tracker.id,
        runtime_source,
        [
            Release(
                tracker_name=tracker_name,
                tracker_type="container",
                name="1.0.0",
                tag_name="1.0.0",
                version="1.0.0",
                published_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
                url=f"https://example.com/{tracker_name}/1.0.0",
                prerelease=False,
                channel_name="stable",
            )
        ],
    )

    assert len(await storage.get_canonical_releases(tracker_name)) == 1

    async with aiosqlite.connect(storage.db_path) as db:
        await db.execute(
            "DELETE FROM tracker_current_releases WHERE aggregate_tracker_id = ?",
            (aggregate_tracker.id,),
        )
        await db.commit()

    scheduler = ExecutorScheduler(storage)
    target = await scheduler._resolve_tracker_latest_target(
        tracker_name,
        "stable",
        tracker_source_id=None,
        tracker_source_type=None,
    )
    assert target is None


@pytest.mark.asyncio
async def test_executor_run_uses_bound_tracker_name_even_if_another_tracker_has_newer_release(
    storage,
):
    runtime_id = await _create_runtime_connection(storage)

    await save_docker_tracker_config(
        storage,
        name="bound-tracker",
        enabled=True,
        image="bound-app",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await save_docker_tracker_config(
        storage,
        name="other-tracker",
        enabled=True,
        image="other-app",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "bound-tracker", "1.2.3")
    await _create_tracker_release(storage, "other-tracker", "9.9.9")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="bound-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name="bound-tracker",
            tracker_source_id=await _get_tracker_source_id(storage, "bound-tracker"),
            channel_name="stable",
            enabled=True,
            image_selection_mode="replace_tag_on_current_image",
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-bound"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("1.2.3", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="ghcr.io/acme/bound-app:0.9.0",
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert adapter.update_calls == ["ghcr.io/acme/bound-app:1.2.3"]


@pytest.mark.asyncio
async def test_executor_scheduler_explicit_tracker_source_binding_uses_bound_source(
    storage, monkeypatch
):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "source-bound-app"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="ghcr.io/acme/source-bound-app",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    legacy_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert legacy_tracker is not None
    await storage.update_aggregate_tracker(
        AggregateTracker(
            id=legacy_tracker.id,
            name=tracker_name,
            primary_changelog_source_key="origin",
            created_at=legacy_tracker.created_at,
            sources=[
                TrackerSource(
                    source_key="origin",
                    source_type="container",
                    source_rank=0,
                    source_config={
                        "image": "ghcr.io/acme/source-bound-app",
                        "registry": "ghcr.io",
                    },
                ),
            ],
        )
    )
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None
    origin_source = next(
        source for source in aggregate_tracker.sources if source.source_key == "origin"
    )
    assert origin_source.id is not None

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="source-bound-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=origin_source.id,
            channel_name="stable",
            enabled=True,
            image_selection_mode="use_tracker_image_and_tag",
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-source-bound"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("9.9.9", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="ghcr.io/acme/source-bound-app:0.9.0",
    )
    scheduler._adapters[executor_id] = adapter
    monkeypatch.setattr(
        scheduler,
        "_resolve_tracker_latest_target",
        AsyncMock(return_value=("1.2.3", None)),
    )

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert adapter.update_calls == ["ghcr.io/acme/source-bound-app:1.2.3"]


@pytest.mark.asyncio
async def test_executor_config_round_trip_persists_channel_name(storage):
    runtime_id = await _create_runtime_connection(storage)
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="channel-round-trip",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name="nginx",
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "c1"},
        )
    )
    loaded = await storage.get_executor_config(executor_id)
    assert loaded is not None
    assert loaded.channel_name == "stable"


@pytest.mark.asyncio
async def test_executor_run_sends_executor_specific_notification(storage, monkeypatch):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "notify-worker"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="notify-worker",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "9.9.9")
    await storage.create_notifier(
        {
            "name": "executor-webhook",
            "type": "webhook",
            "url": "https://example.com/webhook",
            "events": ["executor_run_success"],
            "enabled": True,
            "language": "zh",
        }
    )

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="notify-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "container",
                "container_id": "container-9",
                "image": "notify-worker",
            },
        )
    )

    await storage.set_setting("system.timezone", "Asia/Shanghai")
    scheduler = ExecutorScheduler(storage, now_provider=lambda: datetime(2026, 5, 3, 17, 10, 0))
    _mock_scheduler_target(scheduler, ("9.9.9", None))
    adapter = FakeAdapter(
        RuntimeConnectionConfig(
            name="prod-docker",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_image="ghcr.io/acme/notify-worker:1.0.0",
    )
    scheduler._adapters[executor_id] = adapter

    captured: list[tuple[str, dict]] = []

    async def fake_notify(self, event: str, payload):
        captured.append((event, payload, self.language))

    monkeypatch.setattr("releasetracker.notifiers.webhook.WebhookNotifier.notify", fake_notify)

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert captured
    event, payload, language = captured[0]
    assert event == "executor_run_success"
    assert language == "zh"
    assert payload["entity"] == "executor_run"
    assert payload["executor_name"] == "notify-executor"
    assert payload["tracker_name"] == tracker_name
    assert payload["target_mode"] == "container"
    assert payload["finished_at"] == "2026-05-03T09:10:00Z"
    assert payload["from_version"] == "ghcr.io/acme/notify-worker:1.0.0"
    assert payload["to_version"] == "ghcr.io/acme/notify-worker:9.9.9"


@pytest.mark.asyncio
async def test_run_executor_now_async_returns_run_id_immediately(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "async-worker"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="async-worker",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "5.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="async-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-async"},
        )
    )

    rc = RuntimeConnectionConfig(
        name="prod-docker",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("5.0.0", None))
    scheduler._adapters[executor_id] = FakeAdapter(rc, current_image="async-worker:4.0.0")

    run_id = await scheduler.run_executor_now_async(executor_id)

    assert isinstance(run_id, int)
    run_record = await storage.get_executor_run(run_id)
    assert run_record is not None
    assert run_record.status in ("queued", "running", "success")

    await _wait_for_run_status(storage, run_id, "success")
    await _wait_for_executor_idle(scheduler, executor_id)


@pytest.mark.asyncio
async def test_run_executor_now_async_duplicate_rejected(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "dup-guard"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="dup-guard",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "1.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="dup-guard-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-dup"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    scheduler._running_executor_ids.add(executor_id)

    try:
        with pytest.raises(ValueError, match="already running"):
            await scheduler.run_executor_now_async(executor_id)
    finally:
        scheduler._running_executor_ids.discard(executor_id)


@pytest.mark.asyncio
async def test_run_executor_now_async_queued_status_recorded(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "status-tracker"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="status-tracker",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "2.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="status-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-status"},
        )
    )

    rc = RuntimeConnectionConfig(
        name="prod-docker",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("2.0.0", None))
    scheduler._adapters[executor_id] = FakeAdapter(rc, current_image="status-tracker:1.0.0")

    run_id = await scheduler.run_executor_now_async(executor_id)

    initial_run = await storage.get_executor_run(run_id)
    assert initial_run is not None
    assert initial_run.status in ("queued", "running", "success")

    final_run = await _wait_for_run_status(storage, run_id, "success")
    assert final_run.finished_at is not None
    await _wait_for_executor_idle(scheduler, executor_id)


@pytest.mark.asyncio
async def test_run_executor_now_async_skip_finalizes_original_run(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "async-skip"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="async-skip",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="async-skip-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-async-skip"},
        )
    )

    rc = RuntimeConnectionConfig(
        name="prod-docker",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    scheduler = ExecutorScheduler(storage)
    scheduler._adapters[executor_id] = FakeAdapter(rc, current_image="async-skip:1.0.0")

    run_id = await scheduler.run_executor_now_async(executor_id)

    final_run = await _wait_for_run_status(storage, run_id, "skipped")
    await _wait_for_executor_idle(scheduler, executor_id)

    assert final_run.id == run_id
    assert final_run.finished_at is not None
    assert final_run.message == "tracker has no versions"
    assert await storage.get_total_executor_run_history_count(executor_id) == 1


@pytest.mark.asyncio
async def test_run_executor_now_async_failure_finalizes_original_run(storage):
    runtime_config = RuntimeConnectionConfig(
        name="prod-docker-disabled",
        type="docker",
        enabled=False,
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    runtime_id = await storage.create_runtime_connection(runtime_config)
    tracker_name = "async-fail"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="async-fail",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "1.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="async-fail-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-async-fail"},
        )
    )

    scheduler = ExecutorScheduler(storage)
    run_id = await scheduler.run_executor_now_async(executor_id)

    final_run = await _wait_for_run_status(storage, run_id, "failed")
    await _wait_for_executor_idle(scheduler, executor_id)

    assert final_run.id == run_id
    assert final_run.finished_at is not None
    assert final_run.message == "runtime connection disabled"
    assert await storage.get_total_executor_run_history_count(executor_id) == 1


@pytest.mark.asyncio
async def test_run_executor_now_async_rejects_overlap_but_allows_rerun_after_completion(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "async-overlap"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="async-overlap",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "6.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="async-overlap-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": "container-overlap"},
        )
    )

    rc = RuntimeConnectionConfig(
        name="prod-docker",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("6.0.0", None), ("7.0.0", None))
    adapter = SlowFakeAdapter(rc, current_image="async-overlap:5.0.0")
    scheduler._adapters[executor_id] = adapter

    first_run_id = await scheduler.run_executor_now_async(executor_id)
    await _wait_for_adapter_run_count(adapter, attribute="started_runs", expected_count=1)

    with pytest.raises(ValueError, match="already running"):
        await scheduler.run_executor_now_async(executor_id)

    adapter.release_update.set()
    await _wait_for_adapter_run_count(adapter, attribute="completed_runs", expected_count=1)
    first_run = await _wait_for_run_status(storage, first_run_id, "success")
    await _wait_for_executor_idle(scheduler, executor_id)

    assert first_run is not None
    assert first_run.status == "success"
    assert first_run.from_version == "async-overlap:5.0.0"
    assert first_run.to_version == "async-overlap:6.0.0"

    adapter.release_update.clear()
    await _create_tracker_release(storage, tracker_name, "7.0.0")
    second_run_id = await scheduler.run_executor_now_async(executor_id)
    await _wait_for_adapter_run_count(adapter, attribute="started_runs", expected_count=2)

    second_run = await storage.get_executor_run(second_run_id)
    assert second_run is not None
    assert second_run.status in ("queued", "running")
    assert second_run.finished_at is None
    assert second_run_id != first_run_id
    assert adapter.completed_runs == 1

    adapter.release_update.set()
    await _wait_for_adapter_run_count(adapter, attribute="completed_runs", expected_count=2)
    second_run = await _wait_for_run_status(storage, second_run_id, "success")
    await _wait_for_executor_idle(scheduler, executor_id)

    assert second_run.from_version == "async-overlap:6.0.0"
    assert second_run.to_version == "async-overlap:7.0.0"
    assert adapter.update_calls == ["async-overlap:6.0.0", "async-overlap:7.0.0"]


class FakeRecreateAdapter(BaseRuntimeAdapter):
    def __init__(
        self,
        runtime_connection,
        *,
        current_image: str,
        new_container_id: str = "new-id",
        fail_update: bool = False,
    ):
        super().__init__(runtime_connection)
        self.current_image = current_image
        self.new_container_id = new_container_id
        self.update_calls: list[str] = []
        self.recovery_calls: list[dict] = []
        self.fail_update = fail_update

    async def discover_targets(self):
        return []

    async def validate_target_ref(self, target_ref):
        pass

    async def get_current_image(self, target_ref) -> str:
        return self.current_image

    async def capture_snapshot(self, target_ref, current_image: str):
        return {
            "runtime_type": "podman",
            "image": current_image,
            "target_ref": target_ref,
            "container_id": target_ref.get("container_id", "old-id"),
            "container_name": target_ref.get("container_name"),
        }

    async def validate_snapshot(self, target_ref, snapshot):
        if not isinstance(snapshot, dict) or not snapshot:
            raise ValueError("snapshot must be a non-empty dict")
        if snapshot.get("target_ref") != target_ref:
            raise ValueError("snapshot target_ref mismatch")

    async def update_image(self, target_ref, new_image: str):
        self.update_calls.append(new_image)
        if self.fail_update:
            raise RuntimeMutationError("simulated update failure", destructive_started=True)
        old = self.current_image
        self.current_image = new_image
        return RuntimeUpdateResult(
            updated=True,
            old_image=old,
            new_image=new_image,
            new_container_id=self.new_container_id,
        )

    async def recover_from_snapshot(self, target_ref, snapshot):
        self.recovery_calls.append(snapshot)
        image = snapshot.get("image")
        if not isinstance(image, str):
            raise ValueError("snapshot.image must be a string")
        self.current_image = image
        return RuntimeUpdateResult(
            updated=True,
            old_image=None,
            new_image=image,
            message="runtime recovered from snapshot",
            new_container_id=self.new_container_id,
        )


@pytest.mark.asyncio
async def test_target_ref_container_id_refreshed_after_recreate_update(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "recreate-app"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="recreate-app",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "2.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="recreate-executor",
            runtime_type="podman",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "container",
                "container_id": "old-container-id",
                "container_name": "recreate-app",
            },
        )
    )

    rc = RuntimeConnectionConfig(
        name="prod-podman",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("2.0.0", None))
    scheduler._adapters[executor_id] = FakeRecreateAdapter(
        rc, current_image="recreate-app:1.0.0", new_container_id="fresh-container-id"
    )

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"

    refreshed = await storage.get_executor_config(executor_id)
    assert refreshed is not None
    assert refreshed.target_ref["container_id"] == "fresh-container-id"
    assert refreshed.target_ref["container_name"] == "recreate-app"


@pytest.mark.asyncio
async def test_target_ref_container_id_refreshed_after_recovery(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "recover-app"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="recover-app",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "2.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="recover-executor",
            runtime_type="podman",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "container",
                "container_id": "old-container-id",
                "container_name": "recover-app",
            },
        )
    )

    rc = RuntimeConnectionConfig(
        name="prod-podman",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("2.0.0", None))
    scheduler._adapters[executor_id] = FakeRecreateAdapter(
        rc,
        current_image="recover-app:1.0.0",
        new_container_id="recovered-container-id",
        fail_update=True,
    )

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "failed"
    assert "automatic recovery succeeded" in (outcome.message or "")

    refreshed = await storage.get_executor_config(executor_id)
    assert refreshed is not None
    assert refreshed.target_ref["container_id"] == "recovered-container-id"
    assert refreshed.target_ref["container_name"] == "recover-app"


@pytest.mark.asyncio
async def test_subsequent_run_uses_refreshed_container_id(storage):
    runtime_id = await _create_runtime_connection(storage)
    tracker_name = "stale-id-app"
    await save_docker_tracker_config(
        storage,
        name=tracker_name,
        enabled=True,
        image="stale-id-app",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, tracker_name, "2.0.0")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="stale-id-executor",
            runtime_type="podman",
            runtime_connection_id=runtime_id,
            tracker_name=tracker_name,
            tracker_source_id=await _get_tracker_source_id(storage, tracker_name),
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "container",
                "container_id": "old-id",
                "container_name": "stale-id-app",
            },
        )
    )

    rc = RuntimeConnectionConfig(
        name="prod-podman",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    scheduler = ExecutorScheduler(storage)
    _mock_scheduler_target(scheduler, ("2.0.0", None), ("3.0.0", None))
    adapter = FakeRecreateAdapter(
        rc, current_image="stale-id-app:1.0.0", new_container_id="new-id-after-recreate"
    )
    scheduler._adapters[executor_id] = adapter

    first_outcome = await scheduler.run_executor_now(executor_id)
    assert first_outcome.status == "success"

    refreshed = await storage.get_executor_config(executor_id)
    assert refreshed is not None
    assert refreshed.target_ref["container_id"] == "new-id-after-recreate"

    await _create_tracker_release(storage, tracker_name, "3.0.0")

    second_outcome = await scheduler.run_executor_now(executor_id)
    assert second_outcome.status == "success"


@pytest.mark.asyncio
async def test_portainer_stack_executor_updates_bound_services_via_single_stack_update(storage):
    runtime_id = await _create_portainer_runtime_connection(storage)

    await save_docker_tracker_config(
        storage,
        name="portainer-api",
        enabled=True,
        image="ghcr.io/acme/api",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "portainer-api", "1.1.0")
    api_source_id = await _get_tracker_source_id(storage, "portainer-api")

    await save_docker_tracker_config(
        storage,
        name="portainer-worker",
        enabled=True,
        image="ghcr.io/acme/worker",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "portainer-worker", "2.0.0")
    worker_source_id = await _get_tracker_source_id(storage, "portainer-worker")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="portainer-stack-executor",
            runtime_type="portainer",
            runtime_connection_id=runtime_id,
            tracker_name="portainer-api",
            tracker_source_id=api_source_id,
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
                config_module.ExecutorServiceBinding(
                    service="worker",
                    tracker_source_id=worker_source_id,
                    channel_name="stable",
                ),
                config_module.ExecutorServiceBinding(
                    service="api",
                    tracker_source_id=api_source_id,
                    channel_name="stable",
                ),
            ],
        )
    )

    scheduler = ExecutorScheduler(storage)
    scheduler._resolve_tracker_latest_target = AsyncMock(
        side_effect=[("1.1.0", None), ("2.0.0", None)]
    )

    adapter_runtime = RuntimeConnectionConfig(
        name="portainer-prod",
        type="portainer",
        credential_id=1,
        config={"base_url": "https://portainer.example", "endpoint_id": 2},
        secrets={"api_key": "portainer-api-key"},
    )
    fake_client = FakePortainerHttpClient(
        {
            (
                "GET",
                "/api/stacks/11",
                2,
            ): FakePortainerHttpResponse(
                status_code=200,
                payload={
                    "Id": 11,
                    "EndpointId": 2,
                    "Name": "release-stack",
                    "Type": 2,
                    "Env": [],
                },
            ),
            (
                "GET",
                "/api/stacks/11/file",
                2,
            ): FakePortainerHttpResponse(
                status_code=200,
                payload={
                    "StackFileContent": (
                        "services:\n"
                        "  api:\n"
                        "    image: ghcr.io/acme/api:1.0.0\n"
                        "  db:\n"
                        "    image: postgres:16\n"
                        "  worker:\n"
                        "    image: ghcr.io/acme/worker:1.0.0\n"
                    )
                },
            ),
            (
                "PUT",
                "/api/stacks/11",
                2,
            ): FakePortainerHttpResponse(status_code=200, payload={"Id": 11}),
        }
    )
    scheduler._adapters[executor_id] = PortainerRuntimeAdapter(adapter_runtime, client=fake_client)

    persisted_config = await storage.get_executor_config(executor_id)
    assert persisted_config is not None
    assert [binding.model_dump() for binding in persisted_config.service_bindings] == [
        {
            "service": "api",
            "tracker_source_id": api_source_id,
            "channel_name": "stable",
        },
        {
            "service": "worker",
            "tracker_source_id": worker_source_id,
            "channel_name": "stable",
        },
    ]

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert outcome.from_version == "api=ghcr.io/acme/api:1.0.0; worker=ghcr.io/acme/worker:1.0.0"
    assert outcome.to_version == "api=ghcr.io/acme/api:1.1.0; worker=ghcr.io/acme/worker:2.0.0"
    assert outcome.message is not None
    assert "portainer-stack run finished: 2 updated, 0 skipped, 0 failed" in outcome.message
    assert "api: success" in outcome.message
    assert "worker: success" in outcome.message

    put_call = next(call for call in fake_client.calls if call[0] == "PUT")
    payload = put_call[3]
    assert payload is not None
    assert "ghcr.io/acme/api:1.1.0" in payload["stackFileContent"]
    assert "ghcr.io/acme/worker:2.0.0" in payload["stackFileContent"]
    assert "postgres:16" in payload["stackFileContent"]

    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].status == "success"
    assert history[0].from_version == "api=ghcr.io/acme/api:1.0.0; worker=ghcr.io/acme/worker:1.0.0"
    assert history[0].to_version == "api=ghcr.io/acme/api:1.1.0; worker=ghcr.io/acme/worker:2.0.0"
    assert history[0].message is not None
    assert "portainer-stack run finished: 2 updated, 0 skipped, 0 failed" in history[0].message
    assert "api: success" in history[0].message
    assert "worker: success" in history[0].message
    assert history[0].diagnostics == {
        "kind": "portainer_stack",
        "summary": {
            "updated_count": 2,
            "skipped_count": 0,
            "failed_count": 0,
            "group_message": "Portainer stack updated via API for services: api, worker",
        },
        "services": [
            {
                "service": "api",
                "status": "success",
                "from_version": "ghcr.io/acme/api:1.0.0",
                "to_version": "ghcr.io/acme/api:1.1.0",
                "message": "Portainer stack updated via API for services: api, worker",
            },
            {
                "service": "worker",
                "status": "success",
                "from_version": "ghcr.io/acme/worker:1.0.0",
                "to_version": "ghcr.io/acme/worker:2.0.0",
                "message": "Portainer stack updated via API for services: api, worker",
            },
        ],
    }

    status = await storage.get_executor_status(executor_id)
    assert status is not None
    assert status.last_result == "success"
    assert status.last_error is None
    assert status.last_version == "api=ghcr.io/acme/api:1.1.0; worker=ghcr.io/acme/worker:2.0.0"


@pytest.mark.asyncio
async def test_docker_compose_executor_updates_only_bound_services_via_single_group_update(storage):
    runtime_id = await _create_runtime_connection(storage)

    await save_docker_tracker_config(
        storage,
        name="compose-api",
        enabled=True,
        image="ghcr.io/acme/api",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "compose-api", "1.1.0")
    api_source_id = await _get_tracker_source_id(storage, "compose-api")

    await save_docker_tracker_config(
        storage,
        name="compose-worker",
        enabled=True,
        image="ghcr.io/acme/worker",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "compose-worker", "2.0.0")
    worker_source_id = await _get_tracker_source_id(storage, "compose-worker")

    target_ref = {
        "mode": "docker_compose",
        "project": "release-stack",
        "working_dir": "/srv/release-stack",
        "config_files": ["compose.yml"],
        "services": [
            {"service": "api", "image": "ghcr.io/acme/api:1.0.0"},
            {"service": "worker", "image": "ghcr.io/acme/worker:1.0.0"},
            {"service": "db", "image": "postgres:16"},
        ],
    }
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="docker-compose-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name="compose-api",
            tracker_source_id=api_source_id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref=target_ref,
            service_bindings=[
                config_module.ExecutorServiceBinding(
                    service="worker",
                    tracker_source_id=worker_source_id,
                    channel_name="stable",
                ),
                config_module.ExecutorServiceBinding(
                    service="api",
                    tracker_source_id=api_source_id,
                    channel_name="stable",
                ),
            ],
        )
    )

    scheduler = ExecutorScheduler(storage)
    scheduler._resolve_tracker_latest_target = AsyncMock(
        side_effect=[("1.1.0", None), ("2.0.0", None)]
    )
    adapter_runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    adapter = FakeDockerComposeAdapter(
        adapter_runtime,
        current_images={
            "api": "ghcr.io/acme/api:1.0.0",
            "worker": "ghcr.io/acme/worker:1.0.0",
            "db": "postgres:16",
        },
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert outcome.from_version == "api=ghcr.io/acme/api:1.0.0; worker=ghcr.io/acme/worker:1.0.0"
    assert outcome.to_version == "api=ghcr.io/acme/api:1.1.0; worker=ghcr.io/acme/worker:2.0.0"
    assert outcome.message is not None
    assert "docker-compose run finished: 2 updated, 0 skipped, 0 failed" in outcome.message
    assert adapter.update_calls == [
        (
            target_ref,
            {
                "api": "ghcr.io/acme/api:1.1.0",
                "worker": "ghcr.io/acme/worker:2.0.0",
            },
        )
    ]

    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].diagnostics == {
        "kind": "docker_compose",
        "summary": {
            "updated_count": 2,
            "skipped_count": 0,
            "failed_count": 0,
            "group_message": "fake docker compose updated",
        },
        "services": [
            {
                "service": "api",
                "status": "success",
                "from_version": "ghcr.io/acme/api:1.0.0",
                "to_version": "ghcr.io/acme/api:1.1.0",
                "message": "updated",
            },
            {
                "service": "worker",
                "status": "success",
                "from_version": "ghcr.io/acme/worker:1.0.0",
                "to_version": "ghcr.io/acme/worker:2.0.0",
                "message": "updated",
            },
        ],
    }


@pytest.mark.asyncio
async def test_helm_release_executor_upgrades_chart_version_from_helm_source(storage):
    runtime_id = await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name="k8s-prod",
            type="kubernetes",
            enabled=True,
            config={"namespace": "apps", "in_cluster": True},
            secrets={},
            description="kubernetes runtime",
        )
    )
    await storage.save_tracker_config(
        TrackerConfig(
            name="certd-chart",
            type="helm",
            enabled=True,
            repo="https://charts.example",
            chart="certd",
            channels=[config_module.Channel(name="stable", enabled=True, type="release")],
        )
    )
    await _create_bound_helm_release(
        storage,
        "certd-chart",
        app_version="2.0.0",
        chart_version="0.8.0",
        published_at=datetime(2026, 3, 25, tzinfo=timezone.utc),
    )
    source_id = await _get_tracker_source_id(storage, "certd-chart")
    target_ref = {
        "mode": "helm_release",
        "namespace": "apps",
        "release_name": "certd",
        "chart_name": "certd",
        "chart_version": "0.7.0",
    }
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="certd-release-executor",
            runtime_type="kubernetes",
            runtime_connection_id=runtime_id,
            tracker_name="certd-chart",
            tracker_source_id=source_id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref=target_ref,
        )
    )

    scheduler = ExecutorScheduler(storage)
    adapter = FakeHelmReleaseAdapter(
        RuntimeConnectionConfig(
            name="k8s-prod",
            type="kubernetes",
            config={"namespace": "apps", "in_cluster": True},
            secrets={},
        ),
        current_chart_version="0.7.0",
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert outcome.from_version == "0.7.0"
    assert outcome.to_version == "0.8.0"
    assert outcome.message == "fake helm upgraded"
    assert adapter.update_calls == [
        ({**target_ref, "workloads": []}, "certd", "0.8.0", "https://charts.example")
    ]
    snapshot = await storage.get_executor_snapshot(executor_id)
    assert snapshot is not None
    assert snapshot.snapshot_data["chart_version"] == "0.7.0"
    updated_config = await storage.get_executor_config(executor_id)
    assert updated_config is not None
    assert updated_config.target_ref["chart_name"] == "certd"
    assert updated_config.target_ref["chart_version"] == "0.8.0"


@pytest.mark.asyncio
async def test_helm_release_executor_skips_when_chart_version_is_current(storage):
    runtime_id = await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name="k8s-prod",
            type="kubernetes",
            enabled=True,
            config={"namespace": "apps", "in_cluster": True},
            secrets={},
            description="kubernetes runtime",
        )
    )
    await storage.save_tracker_config(
        TrackerConfig(
            name="aether-chart",
            type="helm",
            enabled=True,
            repo="https://charts.example",
            chart="aether",
            channels=[config_module.Channel(name="stable", enabled=True, type="release")],
        )
    )
    await _create_bound_helm_release(
        storage,
        "aether-chart",
        app_version="3.0.0",
        chart_version="1.2.3",
        published_at=datetime(2026, 3, 25, tzinfo=timezone.utc),
    )
    source_id = await _get_tracker_source_id(storage, "aether-chart")
    target_ref = {
        "mode": "helm_release",
        "namespace": "apps",
        "release_name": "aether",
        "chart_name": "aether",
    }
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="aether-release-executor",
            runtime_type="kubernetes",
            runtime_connection_id=runtime_id,
            tracker_name="aether-chart",
            tracker_source_id=source_id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref=target_ref,
        )
    )

    scheduler = ExecutorScheduler(storage)
    adapter = FakeHelmReleaseAdapter(
        RuntimeConnectionConfig(
            name="k8s-prod",
            type="kubernetes",
            config={"namespace": "apps", "in_cluster": True},
            secrets={},
        ),
        current_chart_version="1.2.3",
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "skipped"
    assert outcome.from_version == "1.2.3"
    assert outcome.to_version == "1.2.3"
    assert outcome.message == "Helm release already at target chart version"
    assert adapter.update_calls == []
    assert adapter.snapshot_calls == []


@pytest.mark.asyncio
async def test_kubernetes_workload_executor_updates_bound_containers_via_single_group_patch(
    storage,
):
    runtime_id = await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name="k8s-prod",
            type="kubernetes",
            enabled=True,
            config={"namespace": "apps", "in_cluster": True},
            secrets={},
            description="kubernetes runtime",
        )
    )

    await save_docker_tracker_config(
        storage,
        name="k8s-api",
        enabled=True,
        image="ghcr.io/acme/api",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "k8s-api", "1.1.0")
    api_source_id = await _get_tracker_source_id(storage, "k8s-api")

    await save_docker_tracker_config(
        storage,
        name="k8s-sidecar",
        enabled=True,
        image="ghcr.io/acme/sidecar",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "k8s-sidecar", "2.0.0")
    sidecar_source_id = await _get_tracker_source_id(storage, "k8s-sidecar")

    target_ref = {
        "mode": "kubernetes_workload",
        "namespace": "apps",
        "kind": "Deployment",
        "name": "worker",
        "services": [
            {"service": "api", "image": "ghcr.io/acme/api:1.0.0"},
            {"service": "sidecar", "image": "ghcr.io/acme/sidecar:1.0.0"},
            {"service": "metrics", "image": "ghcr.io/acme/metrics:1.0.0"},
        ],
    }
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="k8s-workload-executor",
            runtime_type="kubernetes",
            runtime_connection_id=runtime_id,
            tracker_name="k8s-api",
            tracker_source_id=api_source_id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref=target_ref,
            service_bindings=[
                config_module.ExecutorServiceBinding(
                    service="sidecar",
                    tracker_source_id=sidecar_source_id,
                    channel_name="stable",
                ),
                config_module.ExecutorServiceBinding(
                    service="api",
                    tracker_source_id=api_source_id,
                    channel_name="stable",
                ),
            ],
        )
    )

    scheduler = ExecutorScheduler(storage)
    scheduler._resolve_tracker_latest_target = AsyncMock(
        side_effect=[("1.1.0", None), ("2.0.0", None)]
    )
    adapter = FakeKubernetesWorkloadAdapter(
        RuntimeConnectionConfig(
            name="k8s-prod",
            type="kubernetes",
            config={"namespace": "apps", "in_cluster": True},
            secrets={},
        ),
        current_images={
            "api": "ghcr.io/acme/api:1.0.0",
            "sidecar": "ghcr.io/acme/sidecar:1.0.0",
            "metrics": "ghcr.io/acme/metrics:1.0.0",
        },
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success", outcome.message
    assert outcome.from_version == "api=ghcr.io/acme/api:1.0.0; sidecar=ghcr.io/acme/sidecar:1.0.0"
    assert outcome.to_version == "api=ghcr.io/acme/api:1.1.0; sidecar=ghcr.io/acme/sidecar:2.0.0"
    assert outcome.message is not None
    assert "kubernetes-workload run finished: 2 updated, 0 skipped, 0 failed" in outcome.message
    assert adapter.update_calls == [
        (
            target_ref,
            {
                "api": "ghcr.io/acme/api:1.1.0",
                "sidecar": "ghcr.io/acme/sidecar:2.0.0",
            },
        )
    ]

    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].diagnostics == {
        "kind": "kubernetes_workload",
        "summary": {
            "updated_count": 2,
            "skipped_count": 0,
            "failed_count": 0,
            "group_message": "fake kubernetes workload updated",
        },
        "services": [
            {
                "service": "api",
                "status": "success",
                "from_version": "ghcr.io/acme/api:1.0.0",
                "to_version": "ghcr.io/acme/api:1.1.0",
                "message": "updated",
            },
            {
                "service": "sidecar",
                "status": "success",
                "from_version": "ghcr.io/acme/sidecar:1.0.0",
                "to_version": "ghcr.io/acme/sidecar:2.0.0",
                "message": "updated",
            },
        ],
    }


@pytest.mark.asyncio
async def test_docker_compose_executor_ignores_non_bound_dependency_services_in_group_update(
    storage,
):
    runtime_id = await _create_runtime_connection(storage)

    await save_docker_tracker_config(
        storage,
        name="compose-api-only",
        enabled=True,
        image="ghcr.io/acme/api",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "compose-api-only", "1.1.0")
    api_source_id = await _get_tracker_source_id(storage, "compose-api-only")

    target_ref = {
        "mode": "docker_compose",
        "project": "release-stack",
        "services": [
            {"service": "api", "image": "ghcr.io/acme/api:1.0.0", "depends_on": ["db"]},
            {"service": "db", "image": "postgres:16"},
        ],
    }
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="docker-compose-executor-api-only",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name="compose-api-only",
            tracker_source_id=api_source_id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref=target_ref,
            service_bindings=[
                config_module.ExecutorServiceBinding(
                    service="api",
                    tracker_source_id=api_source_id,
                    channel_name="stable",
                ),
            ],
        )
    )

    scheduler = ExecutorScheduler(storage)
    scheduler._resolve_tracker_latest_target = AsyncMock(return_value=("1.1.0", None))
    adapter = FakeDockerComposeAdapter(
        RuntimeConnectionConfig(
            name="docker-prod",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_images={
            "api": "ghcr.io/acme/api:1.0.0",
            "db": "postgres:16",
        },
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert outcome.message is not None
    assert "docker-compose run finished: 1 updated, 0 skipped, 0 failed" in outcome.message
    assert "db:" not in outcome.message
    assert adapter.update_calls == [
        (
            {**target_ref, "config_files": []},
            {"api": "ghcr.io/acme/api:1.1.0"},
        )
    ]


@pytest.mark.asyncio
async def test_podman_compose_executor_reports_runtime_mutation_failure(storage):
    runtime_id = await create_runtime_connection(
        storage,
        name="prod-podman-compose",
        runtime_type="podman",
        description="podman compose runtime",
    )

    await save_docker_tracker_config(
        storage,
        name="podman-compose-api",
        enabled=True,
        image="ghcr.io/acme/api",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "podman-compose-api", "1.1.0")
    source_id = await _get_tracker_source_id(storage, "podman-compose-api")

    target_ref = {
        "mode": "docker_compose",
        "project": "podman-stack",
        "services": [{"service": "api", "image": "ghcr.io/acme/api:1.0.0"}],
    }
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="podman-compose-executor",
            runtime_type="podman",
            runtime_connection_id=runtime_id,
            tracker_name="podman-compose-api",
            tracker_source_id=source_id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref=target_ref,
            service_bindings=[
                config_module.ExecutorServiceBinding(
                    service="api", tracker_source_id=source_id, channel_name="stable"
                ),
            ],
        )
    )

    scheduler = ExecutorScheduler(storage)
    scheduler._resolve_tracker_latest_target = AsyncMock(return_value=("1.1.0", None))
    adapter = FakePodmanComposeAdapter(
        RuntimeConnectionConfig(
            name="podman-prod",
            type="podman",
            config={"socket": "unix:///run/podman/podman.sock"},
            secrets={},
        ),
        current_images={"api": "ghcr.io/acme/api:1.0.0"},
        fail_update=(
            "podman grouped compose update failed after destructive steps began "
            "and recovery succeeded best-effort"
        ),
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "failed"
    assert outcome.message is not None
    assert "podman-compose run finished: 0 updated, 0 skipped, 1 failed" in outcome.message
    assert "recovery succeeded best-effort" in outcome.message
    assert "grouped runtime recreate is not supported in phase 1" not in outcome.message
    assert adapter.update_calls == [
        (
            {**target_ref, "config_files": []},
            {"api": "ghcr.io/acme/api:1.1.0"},
        )
    ]

    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].status == "failed"
    assert history[0].message is not None
    assert "recovery succeeded best-effort" in history[0].message
    assert "grouped runtime recreate is not supported in phase 1" not in history[0].message

    status = await storage.get_executor_status(executor_id)
    assert status is not None
    assert status.last_result == "failed"
    assert status.last_error is not None
    assert "recovery succeeded best-effort" in status.last_error
    assert "grouped runtime recreate is not supported in phase 1" not in status.last_error


@pytest.mark.asyncio
async def test_podman_compose_executor_succeeds_with_pod_backed_grouped_update(storage):
    runtime_id = await create_runtime_connection(
        storage,
        name="prod-podman-compose-success",
        runtime_type="podman",
        description="podman compose runtime success",
    )

    await save_docker_tracker_config(
        storage,
        name="podman-compose-api-success",
        enabled=True,
        image="ghcr.io/acme/api",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "podman-compose-api-success", "1.1.0")
    api_source_id = await _get_tracker_source_id(storage, "podman-compose-api-success")
    await save_docker_tracker_config(
        storage,
        name="podman-compose-worker-success",
        enabled=True,
        image="ghcr.io/acme/worker",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "podman-compose-worker-success", "2.0.0")
    worker_source_id = await _get_tracker_source_id(storage, "podman-compose-worker-success")

    target_ref = {
        "mode": "docker_compose",
        "project": "podman-stack",
        "services": [
            {"service": "api", "image": "ghcr.io/acme/api:1.0.0"},
            {"service": "worker", "image": "ghcr.io/acme/worker:1.0.0"},
        ],
    }
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="podman-compose-executor-success",
            runtime_type="podman",
            runtime_connection_id=runtime_id,
            tracker_name="podman-compose-api-success",
            tracker_source_id=api_source_id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref=target_ref,
            service_bindings=[
                config_module.ExecutorServiceBinding(
                    service="api", tracker_source_id=api_source_id, channel_name="stable"
                ),
                config_module.ExecutorServiceBinding(
                    service="worker", tracker_source_id=worker_source_id, channel_name="stable"
                ),
            ],
        )
    )

    scheduler = ExecutorScheduler(storage)
    scheduler._resolve_tracker_latest_target = AsyncMock(
        side_effect=[("1.1.0", None), ("2.0.0", None)]
    )
    adapter = FakePodmanComposeAdapter(
        RuntimeConnectionConfig(
            name="podman-prod",
            type="podman",
            config={"socket": "unix:///run/podman/podman.sock"},
            secrets={},
        ),
        current_images={
            "api": "ghcr.io/acme/api:1.0.0",
            "worker": "ghcr.io/acme/worker:1.0.0",
        },
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "success"
    assert outcome.message is not None
    assert "podman-compose run finished: 2 updated, 0 skipped, 0 failed" in outcome.message
    assert "api: success (updated)" in outcome.message
    assert "worker: success (updated)" in outcome.message
    assert "pod-aware grouped compose update completed" in outcome.message
    assert outcome.message.count("pod-aware grouped compose update completed") == 1
    assert "grouped runtime recreate is not supported in phase 1" not in outcome.message

    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].status == "success"
    assert history[0].message is not None
    assert "podman-compose run finished: 2 updated, 0 skipped, 0 failed" in history[0].message
    assert "pod-aware grouped compose update completed" in history[0].message
    assert history[0].message.count("pod-aware grouped compose update completed") == 1
    assert "grouped runtime recreate is not supported in phase 1" not in history[0].message

    status = await storage.get_executor_status(executor_id)
    assert status is not None
    assert status.last_result == "success"
    assert status.last_error is None


@pytest.mark.asyncio
async def test_docker_compose_executor_fails_when_bound_service_missing(storage):
    runtime_id = await _create_runtime_connection(storage)
    await save_docker_tracker_config(
        storage,
        name="compose-missing",
        enabled=True,
        image="ghcr.io/acme/api",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "compose-missing", "1.1.0")
    source_id = await _get_tracker_source_id(storage, "compose-missing")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="docker-compose-missing-service",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name="compose-missing",
            tracker_source_id=source_id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "docker_compose",
                "project": "release-stack",
                "working_dir": "/srv/release-stack",
                "config_files": ["compose.yml"],
                "services": [{"service": "api", "image": "ghcr.io/acme/api:1.0.0"}],
            },
            service_bindings=[
                config_module.ExecutorServiceBinding(
                    service="api", tracker_source_id=source_id, channel_name="stable"
                ),
            ],
        )
    )

    scheduler = ExecutorScheduler(storage)
    scheduler._resolve_tracker_latest_target = AsyncMock(return_value=("1.1.0", None))
    scheduler._adapters[executor_id] = FakeDockerComposeAdapter(
        RuntimeConnectionConfig(
            name="docker-prod",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_images={"worker": "ghcr.io/acme/worker:1.0.0"},
    )

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "failed"
    assert outcome.message is not None
    assert "Docker Compose service image missing: api" in outcome.message
    fake_adapter = scheduler._adapters[executor_id]
    assert isinstance(fake_adapter, FakeDockerComposeAdapter)
    assert fake_adapter.update_calls == []

    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].status == "failed"
    assert history[0].message is not None
    assert "Docker Compose service image missing: api" in history[0].message

    status = await storage.get_executor_status(executor_id)
    assert status is not None
    assert status.last_result == "failed"
    assert status.last_error is not None
    assert "Docker Compose service image missing: api" in status.last_error


@pytest.mark.asyncio
async def test_docker_compose_executor_aborts_group_update_when_any_binding_fails(storage):
    runtime_id = await _create_runtime_connection(storage)

    await save_docker_tracker_config(
        storage,
        name="compose-valid",
        enabled=True,
        image="ghcr.io/acme/api",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "compose-valid", "1.1.0")
    valid_source_id = await _get_tracker_source_id(storage, "compose-valid")
    missing_source_id = valid_source_id + 10000

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="docker-compose-abort-on-binding-failure",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name="compose-valid",
            tracker_source_id=valid_source_id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "docker_compose",
                "project": "release-stack",
                "working_dir": "/srv/release-stack",
                "config_files": ["compose.yml"],
                "services": [
                    {"service": "api", "image": "ghcr.io/acme/api:1.0.0"},
                    {"service": "worker", "image": "ghcr.io/acme/worker:1.0.0"},
                ],
            },
            service_bindings=[
                config_module.ExecutorServiceBinding(
                    service="api", tracker_source_id=valid_source_id, channel_name="stable"
                ),
                config_module.ExecutorServiceBinding(
                    service="worker", tracker_source_id=missing_source_id, channel_name="stable"
                ),
            ],
        )
    )

    scheduler = ExecutorScheduler(storage)
    scheduler._resolve_tracker_latest_target = AsyncMock(return_value=("1.1.0", None))
    adapter = FakeDockerComposeAdapter(
        RuntimeConnectionConfig(
            name="docker-prod",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        ),
        current_images={
            "api": "ghcr.io/acme/api:1.0.0",
            "worker": "ghcr.io/acme/worker:1.0.0",
        },
    )
    scheduler._adapters[executor_id] = adapter

    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "failed"
    assert outcome.message is not None
    assert "worker: failed (tracker source binding missing)" in outcome.message
    assert "api: failed (Docker Compose group update aborted" in outcome.message
    assert adapter.update_calls == []


@pytest.mark.asyncio
async def test_portainer_stack_executor_fails_for_unsupported_git_backed_stack(storage):
    runtime_id = await _create_portainer_runtime_connection(storage)

    await save_docker_tracker_config(
        storage,
        name="portainer-unsupported",
        enabled=True,
        image="ghcr.io/acme/unsupported",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "portainer-unsupported", "1.1.0")
    source_id = await _get_tracker_source_id(storage, "portainer-unsupported")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="portainer-stack-executor-unsupported",
            runtime_type="portainer",
            runtime_connection_id=runtime_id,
            tracker_name="portainer-unsupported",
            tracker_source_id=source_id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={
                "mode": "portainer_stack",
                "endpoint_id": 2,
                "stack_id": 42,
                "stack_name": "unsupported-stack",
                "stack_type": "standalone",
            },
            service_bindings=[
                config_module.ExecutorServiceBinding(
                    service="api",
                    tracker_source_id=source_id,
                    channel_name="stable",
                )
            ],
        )
    )

    scheduler = ExecutorScheduler(storage)

    adapter_runtime = RuntimeConnectionConfig(
        name="portainer-prod",
        type="portainer",
        credential_id=1,
        config={"base_url": "https://portainer.example", "endpoint_id": 2},
        secrets={"api_key": "portainer-api-key"},
    )
    fake_client = FakePortainerHttpClient(
        {
            (
                "GET",
                "/api/stacks/42",
                2,
            ): FakePortainerHttpResponse(
                status_code=200,
                payload={
                    "Id": 42,
                    "EndpointId": 2,
                    "Name": "unsupported-stack",
                    "Type": 2,
                    "GitConfig": {"URL": "https://github.com/acme/unsupported-stack.git"},
                },
            )
        }
    )
    scheduler._adapters[executor_id] = PortainerRuntimeAdapter(adapter_runtime, client=fake_client)

    persisted_config = await storage.get_executor_config(executor_id)
    assert persisted_config is not None
    execute_config = persisted_config.model_copy(
        update={
            "service_bindings": [
                config_module.ExecutorServiceBinding(
                    service="api",
                    tracker_source_id=source_id,
                    channel_name="stable",
                )
            ]
        }
    )

    outcome = await scheduler._execute_executor(execute_config, manual=True)

    assert outcome.status == "failed"
    assert outcome.message is not None
    assert "git-backed standalone stacks are not supported" in outcome.message
    assert all(call[0] != "PUT" for call in fake_client.calls)


@pytest.mark.asyncio
async def test_portainer_stack_executor_fails_when_runtime_connection_disabled(storage):
    runtime_id = await _create_portainer_runtime_connection(storage)

    await save_docker_tracker_config(
        storage,
        name="portainer-runtime-disabled",
        enabled=True,
        image="ghcr.io/acme/runtime-disabled",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "portainer-runtime-disabled", "1.1.0")
    source_id = await _get_tracker_source_id(storage, "portainer-runtime-disabled")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="portainer-stack-runtime-disabled-executor",
            runtime_type="portainer",
            runtime_connection_id=runtime_id,
            tracker_name="portainer-runtime-disabled",
            tracker_source_id=source_id,
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
                config_module.ExecutorServiceBinding(
                    service="api",
                    tracker_source_id=source_id,
                    channel_name="stable",
                )
            ],
        )
    )

    runtime_connection = await storage.get_runtime_connection(runtime_id)
    assert runtime_connection is not None
    await storage.update_runtime_connection(
        runtime_id,
        runtime_connection.model_copy(update={"enabled": False}),
    )

    scheduler = ExecutorScheduler(storage)
    outcome = await scheduler.run_executor_now(executor_id)

    assert outcome.status == "failed"
    assert outcome.message == "runtime connection disabled"

    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].status == "failed"
    assert history[0].message == "runtime connection disabled"

    status = await storage.get_executor_status(executor_id)
    assert status is not None
    assert status.last_result == "failed"
    assert status.last_error == "runtime connection disabled"


@pytest.mark.asyncio
async def test_portainer_stack_executor_fails_with_explicit_auth_error_when_portainer_rejects_api_key(
    storage,
):
    runtime_id = await _create_portainer_runtime_connection(storage)

    await save_docker_tracker_config(
        storage,
        name="portainer-auth-fail",
        enabled=True,
        image="ghcr.io/acme/auth-fail",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "portainer-auth-fail", "1.1.0")
    source_id = await _get_tracker_source_id(storage, "portainer-auth-fail")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="portainer-stack-auth-fail-executor",
            runtime_type="portainer",
            runtime_connection_id=runtime_id,
            tracker_name="portainer-auth-fail",
            tracker_source_id=source_id,
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
                config_module.ExecutorServiceBinding(
                    service="api",
                    tracker_source_id=source_id,
                    channel_name="stable",
                )
            ],
        )
    )

    scheduler = ExecutorScheduler(storage)

    adapter_runtime = RuntimeConnectionConfig(
        name="portainer-prod",
        type="portainer",
        credential_id=1,
        config={"base_url": "https://portainer.example", "endpoint_id": 2},
        secrets={"api_key": "portainer-api-key"},
    )
    fake_client = FakePortainerHttpClient(
        {
            (
                "GET",
                "/api/stacks/11",
                2,
            ): FakePortainerHttpResponse(status_code=401, text="unauthorized"),
        }
    )
    scheduler._adapters[executor_id] = PortainerRuntimeAdapter(adapter_runtime, client=fake_client)

    persisted_config = await storage.get_executor_config(executor_id)
    assert persisted_config is not None
    execute_config = persisted_config.model_copy(
        update={
            "service_bindings": [
                config_module.ExecutorServiceBinding(
                    service="api",
                    tracker_source_id=source_id,
                    channel_name="stable",
                )
            ]
        }
    )

    outcome = await scheduler._execute_executor(execute_config, manual=True)

    assert outcome.status == "failed"
    assert outcome.message is not None
    assert "portainer target validation access failed" in outcome.message
    assert "invalid target ref" not in outcome.message
    assert "401" in outcome.message
    assert "unauthorized" in outcome.message

    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].status == "failed"
    assert history[0].message is not None
    assert "401" in history[0].message
    assert "unauthorized" in history[0].message

    status = await storage.get_executor_status(executor_id)
    assert status is not None
    assert status.last_result == "failed"
    assert status.last_error is not None
    assert "portainer target validation access failed" in status.last_error
    assert "401" in status.last_error


@pytest.mark.asyncio
async def test_portainer_stack_executor_classifies_read_timeout_during_update(storage):
    runtime_id = await _create_portainer_runtime_connection(storage)

    await save_docker_tracker_config(
        storage,
        name="portainer-timeout",
        enabled=True,
        image="ghcr.io/acme/timeout",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "portainer-timeout", "1.1.0")
    source_id = await _get_tracker_source_id(storage, "portainer-timeout")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="portainer-stack-timeout-executor",
            runtime_type="portainer",
            runtime_connection_id=runtime_id,
            tracker_name="portainer-timeout",
            tracker_source_id=source_id,
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
                config_module.ExecutorServiceBinding(
                    service="api",
                    tracker_source_id=source_id,
                    channel_name="stable",
                )
            ],
        )
    )

    scheduler = ExecutorScheduler(storage)
    scheduler._resolve_tracker_latest_target = AsyncMock(return_value=("1.1.0", None))

    adapter_runtime = RuntimeConnectionConfig(
        name="portainer-prod",
        type="portainer",
        credential_id=1,
        config={"base_url": "https://portainer.example", "endpoint_id": 2},
        secrets={"api_key": "portainer-api-key"},
    )
    fake_client = FakePortainerHttpClient(
        {
            (
                "GET",
                "/api/stacks/11",
                2,
            ): FakePortainerHttpResponse(
                status_code=200,
                payload={
                    "Id": 11,
                    "EndpointId": 2,
                    "Name": "release-stack",
                    "Type": 2,
                    "Env": [],
                },
            ),
            (
                "GET",
                "/api/stacks/11/file",
                2,
            ): FakePortainerHttpResponse(
                status_code=200,
                payload={
                    "StackFileContent": (
                        "services:\n" "  api:\n" "    image: ghcr.io/acme/timeout:1.0.0\n"
                    )
                },
            ),
            (
                "PUT",
                "/api/stacks/11",
                2,
            ): httpx.ReadTimeout("timed out"),
        }
    )
    scheduler._adapters[executor_id] = PortainerRuntimeAdapter(adapter_runtime, client=fake_client)

    persisted_config = await storage.get_executor_config(executor_id)
    assert persisted_config is not None
    execute_config = persisted_config.model_copy(
        update={
            "service_bindings": [
                config_module.ExecutorServiceBinding(
                    service="api",
                    tracker_source_id=source_id,
                    channel_name="stable",
                )
            ]
        }
    )

    outcome = await scheduler._execute_executor(execute_config, manual=True)

    assert outcome.status == "failed"
    assert outcome.message is not None
    assert "portainer request timeout" in outcome.message
    assert "timed out during stack update" in outcome.message

    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].status == "failed"
    assert history[0].message is not None
    assert "portainer request timeout" in history[0].message

    status = await storage.get_executor_status(executor_id)
    assert status is not None
    assert status.last_result == "failed"
    assert status.last_error is not None
    assert "portainer request timeout" in status.last_error


@pytest.mark.asyncio
async def test_portainer_stack_executor_fails_when_bound_service_missing_from_stack_file(storage):
    runtime_id = await _create_portainer_runtime_connection(storage)

    await save_docker_tracker_config(
        storage,
        name="portainer-missing-service",
        enabled=True,
        image="ghcr.io/acme/missing-service",
        registry="registry-1.docker.io",
        channels=[config_module.Channel(name="stable", enabled=True, type="release")],
    )
    await _create_tracker_release(storage, "portainer-missing-service", "1.1.0")
    source_id = await _get_tracker_source_id(storage, "portainer-missing-service")

    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="portainer-stack-missing-service-executor",
            runtime_type="portainer",
            runtime_connection_id=runtime_id,
            tracker_name="portainer-missing-service",
            tracker_source_id=source_id,
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
                config_module.ExecutorServiceBinding(
                    service="worker",
                    tracker_source_id=source_id,
                    channel_name="stable",
                )
            ],
        )
    )

    scheduler = ExecutorScheduler(storage)
    scheduler._resolve_tracker_latest_target = AsyncMock(return_value=("1.1.0", None))

    adapter_runtime = RuntimeConnectionConfig(
        name="portainer-prod",
        type="portainer",
        credential_id=1,
        config={"base_url": "https://portainer.example", "endpoint_id": 2},
        secrets={"api_key": "portainer-api-key"},
    )
    fake_client = FakePortainerHttpClient(
        {
            (
                "GET",
                "/api/stacks/11",
                2,
            ): FakePortainerHttpResponse(
                status_code=200,
                payload={
                    "Id": 11,
                    "EndpointId": 2,
                    "Name": "release-stack",
                    "Type": 2,
                    "Env": [],
                },
            ),
            (
                "GET",
                "/api/stacks/11/file",
                2,
            ): FakePortainerHttpResponse(
                status_code=200,
                payload={
                    "StackFileContent": (
                        "services:\n" "  api:\n" "    image: ghcr.io/acme/api:1.0.0\n"
                    )
                },
            ),
        }
    )
    scheduler._adapters[executor_id] = PortainerRuntimeAdapter(adapter_runtime, client=fake_client)

    persisted_config = await storage.get_executor_config(executor_id)
    assert persisted_config is not None
    execute_config = persisted_config.model_copy(
        update={
            "service_bindings": [
                config_module.ExecutorServiceBinding(
                    service="worker",
                    tracker_source_id=source_id,
                    channel_name="stable",
                )
            ]
        }
    )

    outcome = await scheduler._execute_executor(execute_config, manual=True)

    assert outcome.status == "failed"
    assert outcome.message is not None
    assert "0 updated, 0 skipped, 1 failed" in outcome.message
    assert "worker: failed (Portainer stack service image missing: worker)" in outcome.message
    assert all(call[0] != "PUT" for call in fake_client.calls)

    history = await storage.get_executor_run_history(executor_id, limit=1)
    assert history[0].status == "failed"
    assert history[0].message is not None
    assert "Portainer stack service image missing: worker" in history[0].message

    status = await storage.get_executor_status(executor_id)
    assert status is not None
    assert status.last_result == "failed"
    assert status.last_error is not None
    assert "Portainer stack service image missing: worker" in status.last_error
