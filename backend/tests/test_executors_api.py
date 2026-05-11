from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Literal

import pytest

from helpers.executor_adapters import MutableFakeRuntimeAdapter
from helpers.executor_runtime import (
    create_portainer_runtime_connection,
    create_runtime_connection,
    save_docker_tracker_config,
    seed_docker_release,
)

from releasetracker.config import Channel, RuntimeConnectionConfig, TrackerConfig
from releasetracker.executors.base import BaseRuntimeAdapter, RuntimeTarget, RuntimeUpdateResult
from releasetracker.models import (
    AggregateTracker,
    ExecutorRunHistory,
    ReleaseChannel,
    TrackerSource,
)


class FakeDiscoveryAdapter(BaseRuntimeAdapter):
    async def discover_targets(self):
        return [
            RuntimeTarget(
                runtime_type=self.runtime_connection.type,
                name="sample-web",
                target_ref={"mode": "container", "container_id": "abc", "container_name": "sample-web"},
                image="sample-web:1.25",
            )
        ]

    async def validate_target_ref(self, target_ref):
        if target_ref.get("container_name") == "missing":
            raise ValueError("container not found")

    async def get_current_image(self, target_ref) -> str:
        return "sample-web:1.25"

    async def capture_snapshot(self, target_ref, current_image: str):
        return {"image": current_image, "target_ref": target_ref}

    async def validate_snapshot(self, target_ref, snapshot):
        if snapshot.get("target_ref") != target_ref:
            raise ValueError("snapshot target_ref mismatch")

    async def update_image(self, target_ref, new_image: str):
        return RuntimeUpdateResult(updated=True, old_image="sample-web:1.25", new_image=new_image)

    async def recover_from_snapshot(self, target_ref, snapshot):
        return RuntimeUpdateResult(updated=True, old_image=None, new_image=snapshot.get("image"))


class FakePodmanDiscoveryAdapter(BaseRuntimeAdapter):
    async def discover_targets(self):
        return [
            RuntimeTarget(
                runtime_type=self.runtime_connection.type,
                name="api",
                target_ref={
                    "mode": "container",
                    "container_id": "podman-abc",
                    "container_name": "api",
                },
                image="ghcr.io/acme/api:1.2.3",
            )
        ]

    async def validate_target_ref(self, target_ref):
        if target_ref.get("container_name") == "missing":
            raise ValueError("container not found")

    async def get_current_image(self, target_ref) -> str:
        return "ghcr.io/acme/api:1.2.3"

    async def capture_snapshot(self, target_ref, current_image: str):
        return {"image": current_image, "target_ref": target_ref}

    async def validate_snapshot(self, target_ref, snapshot):
        if snapshot.get("target_ref") != target_ref:
            raise ValueError("snapshot target_ref mismatch")

    async def update_image(self, target_ref, new_image: str):
        return RuntimeUpdateResult(
            updated=True, old_image="ghcr.io/acme/api:1.2.3", new_image=new_image
        )

    async def recover_from_snapshot(self, target_ref, snapshot):
        return RuntimeUpdateResult(updated=True, old_image=None, new_image=snapshot.get("image"))


class FakeManualRunAdapter(MutableFakeRuntimeAdapter):
    def __init__(self, runtime_connection, *, current_image: str):
        super().__init__(
            runtime_connection,
            current_image=current_image,
            invalid_target_key="container_id",
            invalid_target_value="bad",
            invalid_target_message="invalid target",
        )


async def _create_runtime_connection(
    storage,
    *,
    name: str = "docker-prod",
    runtime_type: Literal["docker", "podman", "kubernetes"] = "docker",
    description: str = "runtime",
) -> int:
    return await create_runtime_connection(
        storage,
        name=name,
        runtime_type=runtime_type,
        description=description,
    )


async def _create_portainer_runtime_connection(
    storage,
    *,
    name: str = "portainer-prod",
    description: str = "portainer runtime",
) -> int:
    return await create_portainer_runtime_connection(
        storage,
        name=name,
        description=description,
    )


async def _create_tracker(
    storage,
    *,
    name: str = "sample-web",
    tracker_type: Literal["github", "gitlab", "gitea", "helm", "container"] = "container",
):
    channels = (
        [Channel(name="stable", enabled=True, type="release")] if tracker_type == "container" else []
    )
    if tracker_type == "container":
        await save_docker_tracker_config(
            storage,
            name=name,
            image=name,
            channels=channels,
        )
        aggregate_tracker = await storage.get_aggregate_tracker(name)
        assert aggregate_tracker is not None
        return aggregate_tracker
    elif tracker_type == "github":
        config = TrackerConfig(
            name=name,
            type=tracker_type,
            enabled=True,
            repo=f"owner/{name}",
            channels=channels,
        )
    elif tracker_type == "gitea":
        config = TrackerConfig(
            name=name,
            type=tracker_type,
            enabled=True,
            repo=f"owner/{name}",
            channels=channels,
        )
    elif tracker_type == "gitlab":
        config = TrackerConfig(
            name=name,
            type=tracker_type,
            enabled=True,
            project=f"group/{name}",
            instance="https://gitlab.example",
            channels=channels,
        )
    else:
        config = TrackerConfig(
            name=name,
            type=tracker_type,
            enabled=True,
            repo=f"owner/{name}",
            chart=name,
            channels=channels,
        )
    await storage.save_tracker_config(config)

    if tracker_type == "github":
        await storage.create_aggregate_tracker(
            AggregateTracker(
                name=name,
                primary_changelog_source_key="repo",
                sources=[
                    TrackerSource(
                        source_key="repo",
                        source_type="github",
                        source_rank=0,
                        source_config={"repo": f"owner/{name}"},
                    )
                ],
            )
        )
    elif tracker_type == "gitea":
        await storage.create_aggregate_tracker(
            AggregateTracker(
                name=name,
                primary_changelog_source_key="repo",
                sources=[
                    TrackerSource(
                        source_key="repo",
                        source_type="gitea",
                        source_rank=0,
                        source_config={
                            "repo": f"owner/{name}",
                            "instance": "https://gitea.example",
                        },
                    )
                ],
            )
        )
    elif tracker_type == "gitlab":
        await storage.create_aggregate_tracker(
            AggregateTracker(
                name=name,
                primary_changelog_source_key="project",
                sources=[
                    TrackerSource(
                        source_key="project",
                        source_type="gitlab",
                        source_rank=0,
                        source_config={
                            "project": f"group/{name}",
                            "instance": "https://gitlab.example",
                        },
                    )
                ],
            )
        )
    else:
        await storage.create_aggregate_tracker(
            AggregateTracker(
                name=name,
                primary_changelog_source_key="chart",
                sources=[
                    TrackerSource(
                        source_key="chart",
                        source_type="helm",
                        source_rank=0,
                        source_config={"repo": f"owner/{name}", "chart": name},
                    )
                ],
            )
        )


async def _get_tracker_source_id(
    storage, tracker_name: str, *, source_key: str | None = None
) -> int:
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None
    if source_key is None:
        source = aggregate_tracker.sources[0]
    else:
        source = next(item for item in aggregate_tracker.sources if item.source_key == source_key)
    assert source.id is not None
    return source.id


async def _configure_single_docker_source(
    storage,
    *,
    name: str,
    image: str,
    release_channels: list[ReleaseChannel],
):
    aggregate_tracker = await storage.get_aggregate_tracker(name)
    assert aggregate_tracker is not None
    await storage.update_aggregate_tracker(
        AggregateTracker(
            id=aggregate_tracker.id,
            name=name,
            enabled=True,
            primary_changelog_source_key="image",
            created_at=aggregate_tracker.created_at,
            sources=[
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=0,
                    enabled=True,
                    source_config={"image": image, "registry": "registry-1.docker.io"},
                    release_channels=release_channels,
                )
            ],
        )
    )


async def _create_release(
    storage,
    *,
    tracker_name: str,
    version: str,
    prerelease: bool = False,
    published_at: datetime | None = None,
) -> None:
    await seed_docker_release(
        storage,
        tracker_name=tracker_name,
        version=version,
        prerelease=prerelease,
        published_at=published_at,
    )


async def _create_executor_via_api(
    authed_client,
    *,
    storage,
    name: str,
    runtime_id: int,
    tracker_name: str,
    target_ref: dict,
    runtime_type: Literal["docker", "podman", "kubernetes"] = "docker",
    **overrides,
) -> int:
    tracker_source_id = overrides.pop(
        "tracker_source_id", await _get_tracker_source_id(storage, tracker_name)
    )
    payload = {
        "name": name,
        "runtime_type": runtime_type,
        "runtime_connection_id": runtime_id,
        "tracker_name": tracker_name,
        "tracker_source_id": tracker_source_id,
        "channel_name": "stable",
        "enabled": True,
        "update_mode": "manual",
        "target_ref": target_ref,
    }
    payload.update(overrides)
    response = authed_client.post("/api/executors", json=payload)
    assert response.status_code == 200
    return response.json()["id"]


@pytest.mark.asyncio
async def test_executor_discovery_and_create_validation(authed_client, storage, monkeypatch):
    runtime_id = await _create_runtime_connection(storage)
    await _create_tracker(storage, name="sample-web")
    sample_web_source_id = await _get_tracker_source_id(storage, "sample-web")

    monkeypatch.setattr(
        "releasetracker.routers.executors.DockerRuntimeAdapter",
        lambda runtime_connection: FakeDiscoveryAdapter(runtime_connection),
    )

    discovery = authed_client.get(f"/api/executors/runtime-connections/{runtime_id}/targets")
    assert discovery.status_code == 200
    assert discovery.json()["items"] == [
        {
            "runtime_type": "docker",
            "name": "sample-web",
            "target_ref": {"mode": "container", "container_id": "abc", "container_name": "sample-web"},
            "image": "sample-web:1.25",
        }
    ]

    invalid_tracker = authed_client.post(
        "/api/executors",
        json={
            "name": "executor-invalid-tracker",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "missing-tracker",
            "tracker_source_id": 999999,
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "abc"},
        },
    )
    assert invalid_tracker.status_code == 400
    assert invalid_tracker.json()["detail"] == "Tracker source not found"

    invalid_runtime = authed_client.post(
        "/api/executors",
        json={
            "name": "executor-invalid-runtime",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id + 99,
            "tracker_name": "sample-web",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "abc"},
        },
    )
    assert invalid_runtime.status_code == 400
    assert invalid_runtime.json()["detail"] == "Runtime connection not found"

    valid = authed_client.post(
        "/api/executors",
        json={
            "name": "executor-valid",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "sample-web",
            "tracker_source_id": sample_web_source_id,
            "channel_name": "stable",
            "enabled": True,
            "image_selection_mode": "replace_tag_on_current_image",
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "abc", "container_name": "sample-web"},
            "description": "test executor",
        },
    )
    assert valid.status_code == 200
    executor_id = valid.json()["id"]

    listed = authed_client.get("/api/executors")
    assert listed.status_code == 200
    item = next(entry for entry in listed.json()["items"] if entry["id"] == executor_id)
    assert item["runtime_connection_name"] == "docker-prod"
    assert item["status"] is None

    status_detail = authed_client.get(f"/api/executors/{executor_id}")
    assert status_detail.status_code == 200
    assert status_detail.json()["target_ref"] == {
        "mode": "container",
        "container_id": "abc",
        "container_name": "sample-web",
    }
    assert status_detail.json()["description"] == "test executor"
    assert status_detail.json()["maintenance_window"] is None
    assert "secrets" not in status_detail.json()

    detail = authed_client.get(f"/api/executors/{executor_id}/config")
    assert detail.status_code == 200
    assert detail.json()["tracker_name"] == "sample-web"
    assert isinstance(detail.json()["tracker_source_id"], int)


@pytest.mark.asyncio
async def test_executor_kubernetes_discovery_requires_configured_namespace_query(
    authed_client, storage, monkeypatch
):
    runtime_id = await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name="k8s-multi-namespace",
            type="kubernetes",
            enabled=True,
            config={"namespaces": ["apps", "monitoring"], "in_cluster": True},
            secrets={},
        )
    )
    observed_namespaces: list[str | None] = []

    class FakeKubernetesDiscoveryAdapter(FakeDiscoveryAdapter):
        async def discover_targets(self, namespace=None):
            observed_namespaces.append(namespace)
            if namespace not in {"apps", "monitoring"}:
                raise ValueError("namespace is required when multiple namespaces are configured")
            return [
                RuntimeTarget(
                    runtime_type="kubernetes",
                    name="deployment/api",
                    target_ref={
                        "namespace": namespace,
                        "kind": "Deployment",
                        "name": "api",
                        "container": "api",
                    },
                    image="api:1.0",
                )
            ]

    monkeypatch.setattr(
        "releasetracker.routers.executors.KubernetesRuntimeAdapter",
        lambda runtime_connection: FakeKubernetesDiscoveryAdapter(runtime_connection),
    )

    missing_namespace = authed_client.get(
        f"/api/executors/runtime-connections/{runtime_id}/targets"
    )
    filtered = authed_client.get(
        f"/api/executors/runtime-connections/{runtime_id}/targets?namespace=apps"
    )

    assert missing_namespace.status_code == 400
    assert "namespace is required" in missing_namespace.json()["detail"]
    assert filtered.status_code == 200
    assert filtered.json()["items"][0]["target_ref"]["namespace"] == "apps"
    assert observed_namespaces == [None, "apps"]


@pytest.mark.asyncio
async def test_executor_create_rejects_non_container_tracker(authed_client, storage):
    runtime_id = await _create_runtime_connection(storage)
    await _create_tracker(storage, name="gh-release", tracker_type="github")
    tracker_source_id = await _get_tracker_source_id(storage, "gh-release")

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "executor-invalid-type",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "gh-release",
            "tracker_source_id": tracker_source_id,
            "enabled": True,
            "image_selection_mode": "replace_tag_on_current_image",
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "abc"},
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Runtime executors must be bound to a deployable image source"


@pytest.mark.asyncio
async def test_executor_create_rejects_mode_less_container_target_ref(authed_client, storage):
    runtime_id = await _create_runtime_connection(storage)
    await _create_tracker(storage, name="mode-required")
    tracker_source_id = await _get_tracker_source_id(storage, "mode-required")

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "executor-mode-less",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "mode-required",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"container_id": "abc", "container_name": "sample-web"},
        },
    )

    assert response.status_code == 400
    assert "target_ref.mode is required" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_executor_update_rejects_mode_less_container_target_ref(authed_client, storage):
    runtime_id = await _create_runtime_connection(storage)
    await _create_tracker(storage, name="mode-required-update")

    executor_id = await _create_executor_via_api(
        authed_client,
        storage=storage,
        name="executor-mode-required",
        runtime_id=runtime_id,
        tracker_name="mode-required-update",
        target_ref={"mode": "container", "container_id": "abc", "container_name": "sample-web"},
    )

    response = authed_client.put(
        f"/api/executors/{executor_id}",
        json={
            "target_ref": {"container_id": "abc", "container_name": "sample-web"},
        },
    )

    assert response.status_code == 400
    assert "target_ref.mode is required" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_executor_create_rejects_unknown_target_ref_mode_for_docker_runtime(
    authed_client, storage
):
    runtime_id = await _create_runtime_connection(storage)
    await _create_tracker(storage, name="unknown-mode-app")
    tracker_source_id = await _get_tracker_source_id(storage, "unknown-mode-app")

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "unknown-mode-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "unknown-mode-app",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "image_selection_mode": "replace_tag_on_current_image",
            "update_mode": "manual",
            "target_ref": {
                "mode": "legacy_mode",
            },
        },
    )

    assert response.status_code == 400
    assert (
        "target_ref.mode must be one of: container, portainer_stack, docker_compose, kubernetes_workload"
        in str(response.json()["detail"])
    )


@pytest.mark.asyncio
async def test_executor_create_accepts_portainer_stack_target_ref_for_portainer_runtime(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_portainer_runtime_connection(storage)
    await _create_tracker(storage, name="portainer-stack-api")
    await _create_tracker(storage, name="portainer-stack-worker")
    api_source_id = await _get_tracker_source_id(storage, "portainer-stack-api")
    worker_source_id = await _get_tracker_source_id(storage, "portainer-stack-worker")

    class FakePortainerValidationAdapter(BaseRuntimeAdapter):
        async def discover_targets(self):
            return []

        async def validate_target_ref(self, target_ref):
            if target_ref.get("stack_id") != 11:
                raise ValueError("Portainer stack target not found or deleted")

        async def get_current_image(self, target_ref) -> str:
            return "ghcr.io/acme/api:1.0"

        async def capture_snapshot(self, target_ref, current_image: str):
            return {"image": current_image, "target_ref": target_ref}

        async def validate_snapshot(self, target_ref, snapshot):
            if snapshot.get("target_ref") != target_ref:
                raise ValueError("snapshot target_ref mismatch")

        async def update_image(self, target_ref, new_image: str):
            return RuntimeUpdateResult(
                updated=True, old_image="ghcr.io/acme/api:1.0", new_image=new_image
            )

        async def recover_from_snapshot(self, target_ref, snapshot):
            return RuntimeUpdateResult(
                updated=True,
                old_image=None,
                new_image=snapshot.get("image"),
            )

    monkeypatch.setattr(
        "releasetracker.routers.executors.PortainerRuntimeAdapter",
        lambda runtime_connection: FakePortainerValidationAdapter(runtime_connection),
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "portainer-stack-executor",
            "runtime_type": "portainer",
            "runtime_connection_id": runtime_id,
            "tracker_name": "portainer-stack-api",
            "tracker_source_id": api_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {
                "mode": "portainer_stack",
                "endpoint_id": 2,
                "stack_id": 11,
                "stack_name": "release-stack",
                "stack_type": "standalone",
                "entrypoint": "docker-compose.yml",
                "project_path": "/data/compose/11",
            },
            "service_bindings": [
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
            ],
        },
    )

    assert response.status_code == 200
    executor_id = response.json()["id"]

    detail = authed_client.get(f"/api/executors/{executor_id}/config")
    assert detail.status_code == 200
    assert detail.json()["target_ref"] == {
        "mode": "portainer_stack",
        "endpoint_id": 2,
        "stack_id": 11,
        "stack_name": "release-stack",
        "stack_type": "standalone",
        "entrypoint": "docker-compose.yml",
        "project_path": "/data/compose/11",
    }
    assert detail.json()["service_bindings"] == [
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


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_type", ["docker", "podman"])
async def test_executor_create_accepts_compose_target_ref_for_container_runtime(
    authed_client, storage, monkeypatch, runtime_type
):
    runtime_id = await _create_runtime_connection(
        storage,
        name=f"{runtime_type}-compose-runtime",
        runtime_type=runtime_type,
    )
    await _create_tracker(storage, name="compose-api")
    source_id = await _get_tracker_source_id(storage, "compose-api")

    adapter_name = "DockerRuntimeAdapter" if runtime_type == "docker" else "PodmanRuntimeAdapter"
    monkeypatch.setattr(
        f"releasetracker.routers.executors.{adapter_name}",
        lambda runtime_connection: FakeDiscoveryAdapter(runtime_connection),
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": f"{runtime_type}-compose-executor",
            "runtime_type": runtime_type,
            "runtime_connection_id": runtime_id,
            "tracker_name": "compose-api",
            "tracker_source_id": source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {
                "mode": "docker_compose",
                "project": "release-stack",
                "working_dir": "/srv/release-stack",
                "config_files": ["compose.yml"],
                "services": [{"service": "api", "image": "ghcr.io/acme/api:1.0.0"}],
            },
            "service_bindings": [
                {"service": "api", "tracker_source_id": source_id, "channel_name": "stable"}
            ],
        },
    )

    assert response.status_code == 200
    detail = authed_client.get(f"/api/executors/{response.json()['id']}/config")
    assert detail.status_code == 200
    assert detail.json()["target_ref"]["mode"] == "docker_compose"
    assert detail.json()["service_bindings"] == [
        {"service": "api", "tracker_source_id": source_id, "channel_name": "stable"}
    ]


@pytest.mark.asyncio
async def test_executor_create_accepts_kubernetes_workload_target_ref_with_service_bindings(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_runtime_connection(
        storage,
        name="k8s-workload-runtime",
        runtime_type="kubernetes",
    )
    await _create_tracker(storage, name="k8s-api")
    await _create_tracker(storage, name="k8s-sidecar")
    api_source_id = await _get_tracker_source_id(storage, "k8s-api")
    sidecar_source_id = await _get_tracker_source_id(storage, "k8s-sidecar")

    class FakeKubernetesWorkloadValidationAdapter(BaseRuntimeAdapter):
        async def discover_targets(self):
            return []

        async def validate_target_ref(self, target_ref):
            if target_ref.get("name") != "worker":
                raise ValueError("Kubernetes workload target not found or deleted")

        async def get_current_image(self, target_ref) -> str:
            return "ghcr.io/acme/api:1.0"

        async def capture_snapshot(self, target_ref, current_image: str):
            return {"image": current_image, "target_ref": target_ref}

        async def validate_snapshot(self, target_ref, snapshot):
            if snapshot.get("target_ref") != target_ref:
                raise ValueError("snapshot target_ref mismatch")

        async def update_image(self, target_ref, new_image: str):
            return RuntimeUpdateResult(
                updated=True, old_image="ghcr.io/acme/api:1.0", new_image=new_image
            )

        async def recover_from_snapshot(self, target_ref, snapshot):
            return RuntimeUpdateResult(
                updated=True, old_image=None, new_image=snapshot.get("image")
            )

    monkeypatch.setattr(
        "releasetracker.routers.executors.KubernetesRuntimeAdapter",
        lambda runtime_connection: FakeKubernetesWorkloadValidationAdapter(runtime_connection),
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "k8s-workload-executor",
            "runtime_type": "kubernetes",
            "runtime_connection_id": runtime_id,
            "tracker_name": "k8s-api",
            "tracker_source_id": api_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {
                "mode": "kubernetes_workload",
                "namespace": "apps",
                "kind": "Deployment",
                "name": "worker",
                "services": [
                    {"service": "api", "image": "ghcr.io/acme/api:1.0"},
                    {"service": "sidecar", "image": "ghcr.io/acme/sidecar:1.0"},
                ],
                "service_count": 2,
            },
            "service_bindings": [
                {"service": "api", "tracker_source_id": api_source_id, "channel_name": "stable"},
                {
                    "service": "sidecar",
                    "tracker_source_id": sidecar_source_id,
                    "channel_name": "stable",
                },
            ],
        },
    )

    assert response.status_code == 200
    detail = authed_client.get(f"/api/executors/{response.json()['id']}/config")
    assert detail.status_code == 200
    assert detail.json()["target_ref"] == {
        "mode": "kubernetes_workload",
        "namespace": "apps",
        "kind": "Deployment",
        "name": "worker",
        "services": [
            {"service": "api", "image": "ghcr.io/acme/api:1.0"},
            {"service": "sidecar", "image": "ghcr.io/acme/sidecar:1.0"},
        ],
        "service_count": 2,
    }
    assert detail.json()["service_bindings"] == [
        {"service": "api", "tracker_source_id": api_source_id, "channel_name": "stable"},
        {"service": "sidecar", "tracker_source_id": sidecar_source_id, "channel_name": "stable"},
    ]


@pytest.mark.asyncio
async def test_executor_create_rejects_missing_or_deleted_portainer_stack_target_ref(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_portainer_runtime_connection(storage, name="portainer-missing-stack")
    await _create_tracker(storage, name="portainer-stack-missing")
    tracker_source_id = await _get_tracker_source_id(storage, "portainer-stack-missing")

    class FakePortainerValidationAdapter(BaseRuntimeAdapter):
        async def discover_targets(self):
            return []

        async def validate_target_ref(self, target_ref):
            raise ValueError("Portainer stack target not found or deleted")

        async def get_current_image(self, target_ref) -> str:
            return "ghcr.io/acme/api:1.0"

        async def capture_snapshot(self, target_ref, current_image: str):
            return {"image": current_image, "target_ref": target_ref}

        async def validate_snapshot(self, target_ref, snapshot):
            if snapshot.get("target_ref") != target_ref:
                raise ValueError("snapshot target_ref mismatch")

        async def update_image(self, target_ref, new_image: str):
            return RuntimeUpdateResult(
                updated=True, old_image="ghcr.io/acme/api:1.0", new_image=new_image
            )

        async def recover_from_snapshot(self, target_ref, snapshot):
            return RuntimeUpdateResult(
                updated=True,
                old_image=None,
                new_image=snapshot.get("image"),
            )

    monkeypatch.setattr(
        "releasetracker.routers.executors.PortainerRuntimeAdapter",
        lambda runtime_connection: FakePortainerValidationAdapter(runtime_connection),
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "portainer-stack-missing-executor",
            "runtime_type": "portainer",
            "runtime_connection_id": runtime_id,
            "tracker_name": "portainer-stack-missing",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {
                "mode": "portainer_stack",
                "endpoint_id": 2,
                "stack_id": 404,
                "stack_name": "missing-stack",
                "stack_type": "standalone",
            },
            "service_bindings": [
                {
                    "service": "api",
                    "tracker_source_id": tracker_source_id,
                    "channel_name": "stable",
                }
            ],
        },
    )

    assert response.status_code == 400
    assert "not found or deleted" in str(response.json()["detail"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime_type", "runtime_name"),
    [("docker", "docker-runtime"), ("podman", "podman-runtime"), ("kubernetes", "k8s-runtime")],
)
async def test_executor_create_rejects_portainer_stack_target_ref_for_non_portainer_runtime(
    authed_client, storage, runtime_type, runtime_name
):
    runtime_id = await _create_runtime_connection(
        storage, name=runtime_name, runtime_type=runtime_type
    )
    await _create_tracker(storage, name=f"portainer-stack-{runtime_type}")
    tracker_source_id = await _get_tracker_source_id(storage, f"portainer-stack-{runtime_type}")

    response = authed_client.post(
        "/api/executors",
        json={
            "name": f"portainer-stack-invalid-{runtime_type}",
            "runtime_type": runtime_type,
            "runtime_connection_id": runtime_id,
            "tracker_name": f"portainer-stack-{runtime_type}",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {
                "mode": "portainer_stack",
                "endpoint_id": 2,
                "stack_id": 11,
                "stack_name": "release-stack",
                "stack_type": "standalone",
            },
        },
    )

    assert response.status_code == 400
    assert "only supported when runtime_type is 'portainer'" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_executor_discovery_serializes_portainer_stack_target_ref_with_nested_service_metadata(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_portainer_runtime_connection(storage, name="portainer-discovery")

    class FakePortainerDiscoveryAdapter(BaseRuntimeAdapter):
        async def discover_targets(self):
            return [
                RuntimeTarget(
                    runtime_type="portainer",
                    name="release-stack",
                    target_ref={
                        "mode": "portainer_stack",
                        "endpoint_id": 2,
                        "stack_id": 11,
                        "stack_name": "release-stack",
                        "stack_type": "standalone",
                        "entrypoint": "docker-compose.yml",
                        "project_path": "/data/compose/11",
                        "services": [
                            {"service": "api", "image": "ghcr.io/acme/api:1.0"},
                            {"service": "worker", "image": "ghcr.io/acme/worker:1.0"},
                        ],
                        "service_count": 2,
                    },
                    image=None,
                )
            ]

        async def validate_target_ref(self, target_ref):
            return None

        async def get_current_image(self, target_ref):
            return "ghcr.io/acme/api:1.0"

        async def capture_snapshot(self, target_ref, current_image: str):
            return {"image": current_image, "target_ref": target_ref}

        async def validate_snapshot(self, target_ref, snapshot):
            if snapshot.get("target_ref") != target_ref:
                raise ValueError("snapshot target_ref mismatch")

        async def update_image(self, target_ref, new_image: str):
            return RuntimeUpdateResult(
                updated=True, old_image="ghcr.io/acme/api:1.0", new_image=new_image
            )

    monkeypatch.setattr(
        "releasetracker.routers.executors.PortainerRuntimeAdapter",
        lambda runtime_connection: FakePortainerDiscoveryAdapter(runtime_connection),
    )

    discovery = authed_client.get(f"/api/executors/runtime-connections/{runtime_id}/targets")

    assert discovery.status_code == 200
    assert discovery.json()["items"] == [
        {
            "runtime_type": "portainer",
            "name": "release-stack",
            "target_ref": {
                "mode": "portainer_stack",
                "endpoint_id": 2,
                "stack_id": 11,
                "stack_name": "release-stack",
                "stack_type": "standalone",
                "entrypoint": "docker-compose.yml",
                "project_path": "/data/compose/11",
                "services": [
                    {"service": "api", "image": "ghcr.io/acme/api:1.0"},
                    {"service": "worker", "image": "ghcr.io/acme/worker:1.0"},
                ],
                "service_count": 2,
            },
            "image": None,
        }
    ]


@pytest.mark.asyncio
async def test_portainer_executor_update_and_read_surfaces_preserve_target_identity_serialization(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_portainer_runtime_connection(
        storage, name="portainer-update-serialize"
    )
    await _create_tracker(storage, name="portainer-update-api")
    await _create_tracker(storage, name="portainer-update-worker")
    api_source_id = await _get_tracker_source_id(storage, "portainer-update-api")
    worker_source_id = await _get_tracker_source_id(storage, "portainer-update-worker")

    class FakePortainerValidationAdapter(BaseRuntimeAdapter):
        async def discover_targets(self):
            return []

        async def validate_target_ref(self, target_ref):
            if target_ref.get("stack_id") not in {11, 12}:
                raise ValueError("Portainer stack target not found or deleted")

        async def get_current_image(self, target_ref):
            return "ghcr.io/acme/api:1.0"

        async def capture_snapshot(self, target_ref, current_image: str):
            return {"image": current_image, "target_ref": target_ref}

        async def validate_snapshot(self, target_ref, snapshot):
            if snapshot.get("target_ref") != target_ref:
                raise ValueError("snapshot target_ref mismatch")

        async def update_image(self, target_ref, new_image: str):
            return RuntimeUpdateResult(
                updated=True, old_image="ghcr.io/acme/api:1.0", new_image=new_image
            )

    monkeypatch.setattr(
        "releasetracker.routers.executors.PortainerRuntimeAdapter",
        lambda runtime_connection: FakePortainerValidationAdapter(runtime_connection),
    )

    create_response = authed_client.post(
        "/api/executors",
        json={
            "name": "portainer-update-executor",
            "runtime_type": "portainer",
            "runtime_connection_id": runtime_id,
            "tracker_name": "portainer-update-api",
            "tracker_source_id": api_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {
                "mode": "portainer_stack",
                "endpoint_id": 2,
                "stack_id": 11,
                "stack_name": "release-stack",
                "stack_type": "standalone",
                "entrypoint": "docker-compose.yml",
                "project_path": "/data/compose/11",
            },
            "service_bindings": [
                {
                    "service": "api",
                    "tracker_source_id": api_source_id,
                    "channel_name": "stable",
                }
            ],
        },
    )
    assert create_response.status_code == 200
    executor_id = create_response.json()["id"]

    updated_target_ref = {
        "mode": "portainer_stack",
        "endpoint_id": 2,
        "stack_id": 12,
        "stack_name": "release-stack-v2",
        "stack_type": "standalone",
        "entrypoint": "stack.yml",
        "project_path": "/data/compose/12",
    }
    update_response = authed_client.put(
        f"/api/executors/{executor_id}",
        json={
            "target_ref": updated_target_ref,
            "update_mode": "manual",
            "service_bindings": [
                {
                    "service": "worker",
                    "tracker_source_id": worker_source_id,
                    "channel_name": "stable",
                },
                {
                    "service": "api",
                    "tracker_source_id": api_source_id,
                    "channel_name": "stable",
                },
            ],
        },
    )
    assert update_response.status_code == 200

    config_response = authed_client.get(f"/api/executors/{executor_id}/config")
    assert config_response.status_code == 200
    assert config_response.json()["target_ref"] == updated_target_ref
    assert config_response.json()["service_bindings"] == [
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

    list_response = authed_client.get("/api/executors")
    assert list_response.status_code == 200
    listed_item = next(item for item in list_response.json()["items"] if item["id"] == executor_id)
    assert listed_item["runtime_type"] == "portainer"
    assert listed_item["target_ref"] == updated_target_ref
    assert listed_item["service_bindings"] == [
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

    diagnostics = {
        "kind": "portainer_stack",
        "summary": {
            "updated_count": 0,
            "skipped_count": 0,
            "failed_count": 1,
            "group_message": None,
        },
        "services": [
            {
                "service": "api",
                "status": "failed",
                "from_version": None,
                "to_version": None,
                "message": "Portainer stack service image missing: api",
            }
        ],
    }
    await storage.create_executor_run(
        ExecutorRunHistory(
            executor_id=executor_id,
            started_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 4, 1, 10, 1, tzinfo=timezone.utc),
            status="failed",
            from_version=None,
            to_version=None,
            message=(
                "portainer-stack run finished: 0 updated, 0 skipped, 1 failed; "
                "details: api: failed (Portainer stack service image missing: api)"
            ),
            diagnostics=diagnostics,
        )
    )

    detail_response = authed_client.get(f"/api/executors/{executor_id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["runtime_type"] == "portainer"
    assert detail_payload["service_bindings"] == [
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
    assert detail_payload["latest_run"] is not None
    assert detail_payload["latest_run"]["status"] == "failed"
    assert "Portainer stack service image missing: api" in detail_payload["latest_run"]["message"]
    assert detail_payload["latest_run"]["diagnostics"] == diagnostics

    history_response = authed_client.get(f"/api/executors/{executor_id}/history")
    assert history_response.status_code == 200
    history_items = history_response.json()["items"]
    assert history_items[0]["status"] == "failed"
    assert "Portainer stack service image missing: api" in history_items[0]["message"]
    assert history_items[0]["diagnostics"] == diagnostics


@pytest.mark.asyncio
async def test_executor_update_rejects_portainer_stack_target_ref_for_non_portainer_runtime(
    authed_client, storage
):
    runtime_id = await _create_runtime_connection(
        storage, name="docker-portainer-update", runtime_type="docker"
    )
    await _create_tracker(storage, name="portainer-update-invalid-runtime")

    executor_id = await _create_executor_via_api(
        authed_client,
        storage=storage,
        name="docker-executor-update-portainer-target",
        runtime_id=runtime_id,
        tracker_name="portainer-update-invalid-runtime",
        runtime_type="docker",
        target_ref={
            "mode": "container",
            "container_id": "docker-update-portainer",
            "container_name": "docker-update-portainer",
        },
    )

    update_response = authed_client.put(
        f"/api/executors/{executor_id}",
        json={
            "target_ref": {
                "mode": "portainer_stack",
                "endpoint_id": 2,
                "stack_id": 11,
                "stack_name": "release-stack",
                "stack_type": "standalone",
            }
        },
    )

    assert update_response.status_code == 400
    assert "only supported when runtime_type is 'portainer'" in str(
        update_response.json()["detail"]
    )


@pytest.mark.asyncio
async def test_executor_discovery_serializes_container_target_ref_for_podman(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_runtime_connection(
        storage,
        name="podman-compose-runtime",
        runtime_type="podman",
    )
    monkeypatch.setattr(
        "releasetracker.routers.executors.PodmanRuntimeAdapter",
        lambda runtime_connection: FakePodmanDiscoveryAdapter(runtime_connection),
    )

    discovery = authed_client.get(f"/api/executors/runtime-connections/{runtime_id}/targets")
    assert discovery.status_code == 200
    assert discovery.json()["items"] == [
        {
            "runtime_type": "podman",
            "name": "api",
            "target_ref": {
                "mode": "container",
                "container_id": "podman-abc",
                "container_name": "api",
            },
            "image": "ghcr.io/acme/api:1.2.3",
        }
    ]


@pytest.mark.asyncio
async def test_executor_discovery_serializes_compose_target_ref_for_podman(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_runtime_connection(
        storage,
        name="podman-compose-group-runtime",
        runtime_type="podman",
    )

    class FakePodmanComposeDiscoveryAdapter(BaseRuntimeAdapter):
        async def discover_targets(self):
            return [
                RuntimeTarget(
                    runtime_type="podman",
                    name="release-stack (1 services)",
                    target_ref={
                        "mode": "docker_compose",
                        "project": "release-stack",
                        "services": [
                            {
                                "service": "api",
                                "image": "ghcr.io/acme/api:1.2.3",
                                "replica_count": 1,
                            }
                        ],
                        "service_count": 1,
                    },
                    image=None,
                )
            ]

        async def validate_target_ref(self, target_ref):
            pass

        async def get_current_image(self, target_ref) -> str:
            return "ghcr.io/acme/api:1.2.3"

        async def capture_snapshot(self, target_ref, current_image: str):
            return {"image": current_image}

        async def validate_snapshot(self, target_ref, snapshot):
            pass

        async def update_image(self, target_ref, new_image: str):
            return RuntimeUpdateResult(updated=True, old_image=None, new_image=new_image)

        async def recover_from_snapshot(self, target_ref, snapshot):
            return RuntimeUpdateResult(
                updated=True, old_image=None, new_image=snapshot.get("image")
            )

    monkeypatch.setattr(
        "releasetracker.routers.executors.PodmanRuntimeAdapter",
        lambda runtime_connection: FakePodmanComposeDiscoveryAdapter(runtime_connection),
    )

    discovery = authed_client.get(f"/api/executors/runtime-connections/{runtime_id}/targets")

    assert discovery.status_code == 200
    assert discovery.json()["items"] == [
        {
            "runtime_type": "podman",
            "name": "release-stack (1 services)",
            "target_ref": {
                "mode": "docker_compose",
                "project": "release-stack",
                "services": [
                    {
                        "service": "api",
                        "image": "ghcr.io/acme/api:1.2.3",
                        "replica_count": 1,
                    }
                ],
                "service_count": 1,
            },
            "image": None,
        }
    ]


@pytest.mark.asyncio
async def test_executor_create_rejects_unknown_target_ref_mode_for_podman(authed_client, storage):
    runtime_id = await _create_runtime_connection(
        storage,
        name="podman-invalid-mode-create",
        runtime_type="podman",
    )
    await _create_tracker(storage, name="podman-invalid-mode-app")
    tracker_source_id = await _get_tracker_source_id(storage, "podman-invalid-mode-app")

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "podman-invalid-mode-executor",
            "runtime_type": "podman",
            "runtime_connection_id": runtime_id,
            "tracker_name": "podman-invalid-mode-app",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "image_selection_mode": "replace_tag_on_current_image",
            "update_mode": "manual",
            "target_ref": {
                "mode": "legacy_mode",
            },
        },
    )

    assert response.status_code == 400
    assert (
        "target_ref.mode must be one of: container, portainer_stack, docker_compose, kubernetes_workload"
        in str(response.json()["detail"])
    )


@pytest.mark.asyncio
async def test_executor_update_rejects_unknown_target_ref_mode(authed_client, storage):
    runtime_id = await _create_runtime_connection(storage)
    await _create_tracker(storage, name="invalid-mode-edit")

    executor_id = await _create_executor_via_api(
        authed_client,
        storage=storage,
        name="invalid-mode-edit-executor",
        runtime_id=runtime_id,
        tracker_name="invalid-mode-edit",
        target_ref={
            "mode": "container",
            "container_id": "container-invalid-mode-edit",
            "container_name": "invalid-mode-edit",
        },
    )

    update_response = authed_client.put(
        f"/api/executors/{executor_id}",
        json={
            "target_ref": {
                "mode": "legacy_mode",
            },
            "update_mode": "manual",
        },
    )
    assert update_response.status_code == 400
    assert (
        "target_ref.mode must be one of: container, portainer_stack, docker_compose, kubernetes_workload"
        in str(update_response.json()["detail"])
    )


@pytest.mark.asyncio
async def test_executor_create_rejects_legacy_flat_channel_on_non_primary_docker_source(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_runtime_connection(storage)
    await _create_tracker(storage, name="aggregate-app", tracker_type="github")
    legacy_tracker = await storage.get_aggregate_tracker("aggregate-app")
    assert legacy_tracker is not None
    await storage.update_aggregate_tracker(
        AggregateTracker(
            id=legacy_tracker.id,
            name="aggregate-app",
            primary_changelog_source_key="repo-primary",
            created_at=legacy_tracker.created_at,
            sources=[
                TrackerSource(
                    source_key="repo-primary",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/aggregate-app"},
                ),
                TrackerSource(
                    source_key="image-origin",
                    source_type="container",
                    source_rank=10,
                    source_config={"image": "ghcr.io/acme/aggregate-app", "registry": "ghcr.io"},
                ),
            ],
        )
    )
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="aggregate-app",
            type="github",
            enabled=True,
            channels=[Channel(name="stable", enabled=True, type="release")],
        )
    )
    tracker_source_id = await _get_tracker_source_id(
        storage, "aggregate-app", source_key="image-origin"
    )
    monkeypatch.setattr(
        "releasetracker.routers.executors.DockerRuntimeAdapter",
        lambda runtime_connection: FakeDiscoveryAdapter(runtime_connection),
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "executor-secondary-docker-source",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "aggregate-app",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "image_selection_mode": "use_tracker_image_and_tag",
            "update_mode": "manual",
            "target_ref": {
                "mode": "container",
                "container_id": "abc",
                "container_name": "aggregate-app",
            },
        },
    )

    assert response.status_code == 400
    assert "stable" in response.json()["detail"]


@pytest.mark.asyncio
async def test_executor_create_rejects_tracker_image_mode_without_image(authed_client, storage):
    runtime_id = await _create_runtime_connection(storage)
    await save_docker_tracker_config(
        storage,
        name="image-missing",
        enabled=True,
        image="ghcr.io/acme/image-missing",
        channels=[Channel(name="stable", enabled=True, type="release")],
    )
    aggregate_tracker = await storage.get_aggregate_tracker("image-missing")
    assert aggregate_tracker is not None
    tracker_source_id = aggregate_tracker.sources[0].id
    assert tracker_source_id is not None

    async def fake_get_executor_binding(requested_tracker_source_id: int):
        assert requested_tracker_source_id == tracker_source_id
        return aggregate_tracker, SimpleNamespace(
            id=tracker_source_id,
            source_key=aggregate_tracker.sources[0].source_key,
            source_type="container",
            enabled=True,
            source_config={},
        )

    storage.get_executor_binding = fake_get_executor_binding

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "executor-invalid-image",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "image-missing",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "image_selection_mode": "use_tracker_image_and_tag",
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "abc"},
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Tracker source image must not be empty when using tracker image mode"


@pytest.mark.asyncio
async def test_executor_create_accepts_tracker_image_mode(authed_client, storage, monkeypatch):
    runtime_id = await _create_runtime_connection(storage)
    await _create_tracker(storage, name="sample-web")
    tracker_source_id = await _get_tracker_source_id(storage, "sample-web")

    monkeypatch.setattr(
        "releasetracker.routers.executors.DockerRuntimeAdapter",
        lambda runtime_connection: FakeDiscoveryAdapter(runtime_connection),
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "executor-tracker-image",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "sample-web",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "image_selection_mode": "use_tracker_image_and_tag",
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "abc", "container_name": "sample-web"},
        },
    )

    assert response.status_code == 200
    executor_id = response.json()["id"]
    detail = authed_client.get(f"/api/executors/{executor_id}/config")
    assert detail.status_code == 200
    assert detail.json()["image_selection_mode"] == "use_tracker_image_and_tag"


@pytest.mark.asyncio
async def test_executor_create_rejects_invalid_kubernetes_target(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_runtime_connection(
        storage, name="k8s-prod", runtime_type="kubernetes"
    )
    await _create_tracker(storage, name="api")
    tracker_source_id = await _get_tracker_source_id(storage, "api")

    class FakeKubernetesValidationAdapter(BaseRuntimeAdapter):
        async def discover_targets(self):
            return []

        async def validate_target_ref(self, target_ref):
            raise ValueError("Kubernetes workload does not contain the selected container")

        async def get_current_image(self, target_ref):
            return "api:1.0"

        async def capture_snapshot(self, target_ref, current_image: str):
            return {"image": current_image, "target_ref": target_ref}

        async def validate_snapshot(self, target_ref, snapshot):
            if snapshot.get("target_ref") != target_ref:
                raise ValueError("snapshot target_ref mismatch")

        async def update_image(self, target_ref, new_image: str):
            return RuntimeUpdateResult(updated=True, old_image="api:1.0", new_image=new_image)

        async def recover_from_snapshot(self, target_ref, snapshot):
            return RuntimeUpdateResult(
                updated=True, old_image=None, new_image=snapshot.get("image")
            )

    monkeypatch.setattr(
        "releasetracker.routers.executors.KubernetesRuntimeAdapter",
        lambda runtime_connection: FakeKubernetesValidationAdapter(runtime_connection),
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "k8s-api",
            "runtime_type": "kubernetes",
            "runtime_connection_id": runtime_id,
            "tracker_name": "api",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {
                "namespace": "apps",
                "kind": "Deployment",
                "name": "api",
                "container": "api",
            },
        },
    )

    assert response.status_code == 400
    assert "kubernetes_workload" in response.json()["detail"]


@pytest.mark.asyncio
async def test_executor_manual_run_returns_queued_immediately(authed_client, storage, monkeypatch):
    runtime_id = await _create_runtime_connection(storage)
    await _create_tracker(storage, name="worker")

    executor_id = await _create_executor_via_api(
        authed_client,
        storage=storage,
        name="worker-executor",
        runtime_id=runtime_id,
        tracker_name="worker",
        target_ref={"mode": "container", "container_id": "container-1", "image": "worker"},
    )

    scheduler = authed_client.executor_scheduler
    call_record = SimpleNamespace(calls=0, executor_id=None)

    async def fake_run_executor_now_async(requested_executor_id: int) -> int:
        call_record.calls += 1
        call_record.executor_id = requested_executor_id
        return 4242

    monkeypatch.setattr(scheduler, "run_executor_now_async", fake_run_executor_now_async)

    run_response = authed_client.post(f"/api/executors/{executor_id}/run")
    assert run_response.status_code == 200
    body = run_response.json()
    assert body["status"] == "queued"
    assert "run_id" in body
    assert isinstance(body["run_id"], int)
    assert body["run_id"] == 4242
    assert call_record.calls == 1
    assert call_record.executor_id == executor_id


@pytest.mark.asyncio
async def test_executor_detail_and_history_surface_persisted_latest_run(authed_client, storage):
    runtime_id = await _create_runtime_connection(
        storage,
        name="docker-history",
        runtime_type="docker",
    )
    await _create_tracker(storage, name="api-history")

    executor_id = await _create_executor_via_api(
        authed_client,
        storage=storage,
        name="api-history-executor",
        runtime_id=runtime_id,
        tracker_name="api-history",
        target_ref={
            "mode": "container",
            "container_id": "container-history-detail",
            "image": "api-history",
        },
    )

    await storage.create_executor_run(
        ExecutorRunHistory(
            executor_id=executor_id,
            started_at=datetime(2026, 3, 24, 8, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 3, 24, 8, 1, tzinfo=timezone.utc),
            status="success",
            from_version="ghcr.io/acme/api-history:1.0.0",
            to_version="ghcr.io/acme/api-history:1.1.0",
            message="updated image",
        )
    )

    await storage.create_executor_run(
        ExecutorRunHistory(
            executor_id=executor_id,
            started_at=datetime(2026, 3, 24, 9, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 3, 24, 9, 1, tzinfo=timezone.utc),
            status="failed",
            from_version="ghcr.io/acme/api-history:1.1.0",
            to_version="ghcr.io/acme/api-history:1.2.0",
            message="registry timeout",
        )
    )

    detail_response = authed_client.get(f"/api/executors/{executor_id}")
    assert detail_response.status_code == 200
    detail_body = detail_response.json()
    assert detail_body["status"] is None
    assert detail_body["runtime_connection_name"] == "docker-history"
    assert detail_body["latest_run"]["status"] == "failed"
    assert detail_body["latest_run"]["message"] == "registry timeout"
    assert detail_body["latest_run"]["from_version"] == "ghcr.io/acme/api-history:1.1.0"
    assert detail_body["latest_run"]["to_version"] == "ghcr.io/acme/api-history:1.2.0"

    history_response = authed_client.get(f"/api/executors/{executor_id}/history")
    assert history_response.status_code == 200
    history_items = history_response.json()["items"]
    assert [item["status"] for item in history_items[:2]] == ["failed", "success"]
    assert history_items[0]["message"] == "registry timeout"
    assert history_items[0]["from_version"] == "ghcr.io/acme/api-history:1.1.0"
    assert history_items[0]["to_version"] == "ghcr.io/acme/api-history:1.2.0"
    assert history_items[1]["from_version"] == "ghcr.io/acme/api-history:1.0.0"
    assert history_items[1]["to_version"] == "ghcr.io/acme/api-history:1.1.0"


@pytest.mark.asyncio
async def test_executor_manual_run_duplicate_rejected(authed_client, storage):
    runtime_id = await _create_runtime_connection(storage)
    await _create_tracker(storage, name="dup-worker")
    await _create_release(storage, tracker_name="dup-worker", version="3.0.0")

    executor_id = await _create_executor_via_api(
        authed_client,
        storage=storage,
        name="dup-executor",
        runtime_id=runtime_id,
        tracker_name="dup-worker",
        target_ref={"mode": "container", "container_id": "container-dup", "image": "dup-worker"},
    )

    scheduler = authed_client.executor_scheduler
    scheduler._running_executor_ids.add(executor_id)

    try:
        dup_response = authed_client.post(f"/api/executors/{executor_id}/run")
        assert dup_response.status_code == 400
        assert "already running" in dup_response.json()["detail"]
    finally:
        scheduler._running_executor_ids.discard(executor_id)


@pytest.mark.asyncio
async def test_executor_create_rejects_missing_channel_name(authed_client, storage, monkeypatch):
    runtime_id = await _create_runtime_connection(storage)
    await save_docker_tracker_config(
        storage,
        name="channeled",
        enabled=True,
        image="channeled",
        channels=[Channel(name="stable", enabled=True, type="release")],
    )
    tracker_source_id = await _get_tracker_source_id(storage, "channeled")
    monkeypatch.setattr(
        "releasetracker.routers.executors.DockerRuntimeAdapter",
        lambda rc: FakeDiscoveryAdapter(rc),
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "no-channel-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "channeled",
            "tracker_source_id": tracker_source_id,
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "abc", "container_name": "sample-web"},
        },
    )
    assert response.status_code == 400
    assert "channel_name" in response.json()["detail"]


@pytest.mark.asyncio
async def test_executor_create_rejects_nonexistent_channel(authed_client, storage, monkeypatch):
    runtime_id = await _create_runtime_connection(storage)
    await save_docker_tracker_config(
        storage,
        name="channeled2",
        enabled=True,
        image="channeled2",
        channels=[Channel(name="stable", enabled=True, type="release")],
    )
    tracker_source_id = await _get_tracker_source_id(storage, "channeled2")
    monkeypatch.setattr(
        "releasetracker.routers.executors.DockerRuntimeAdapter",
        lambda rc: FakeDiscoveryAdapter(rc),
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "bad-channel-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "channeled2",
            "tracker_source_id": tracker_source_id,
            "channel_name": "canary",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "abc", "container_name": "sample-web"},
        },
    )
    assert response.status_code == 400
    assert "canary" in response.json()["detail"]


@pytest.mark.asyncio
async def test_executor_create_rejects_disabled_channel(authed_client, storage, monkeypatch):
    runtime_id = await _create_runtime_connection(storage)
    await save_docker_tracker_config(
        storage,
        name="channeled3",
        enabled=True,
        image="channeled3",
        channels=[
            Channel(name="stable", enabled=True, type="release"),
            Channel(name="canary", enabled=False, type="prerelease"),
        ],
    )
    await _configure_single_docker_source(
        storage,
        name="channeled3",
        image="channeled3",
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
                enabled=False,
            ),
        ],
    )
    tracker_source_id = await _get_tracker_source_id(storage, "channeled3")
    monkeypatch.setattr(
        "releasetracker.routers.executors.DockerRuntimeAdapter",
        lambda rc: FakeDiscoveryAdapter(rc),
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "disabled-channel-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "channeled3",
            "tracker_source_id": tracker_source_id,
            "channel_name": "canary",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "abc", "container_name": "sample-web"},
        },
    )
    assert response.status_code == 400
    assert "disabled" in response.json()["detail"]


@pytest.mark.asyncio
async def test_executor_create_accepts_valid_channel_and_persists_it(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_runtime_connection(storage)
    await save_docker_tracker_config(
        storage,
        name="channeled4",
        enabled=True,
        image="channeled4",
        channels=[Channel(name="stable", enabled=True, type="release")],
    )
    await _configure_single_docker_source(
        storage,
        name="channeled4",
        image="channeled4",
        release_channels=[
            ReleaseChannel(
                release_channel_key="image-stable",
                name="stable",
                type="release",
                enabled=True,
            )
        ],
    )
    tracker_source_id = await _get_tracker_source_id(storage, "channeled4")
    monkeypatch.setattr(
        "releasetracker.routers.executors.DockerRuntimeAdapter",
        lambda rc: FakeDiscoveryAdapter(rc),
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "good-channel-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "channeled4",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "abc", "container_name": "sample-web"},
        },
    )
    assert response.status_code == 200
    executor_id = response.json()["id"]

    config_response = authed_client.get(f"/api/executors/{executor_id}/config")
    assert config_response.status_code == 200
    assert config_response.json()["channel_name"] == "stable"
    assert isinstance(config_response.json()["tracker_source_id"], int)


@pytest.mark.asyncio
async def test_executor_create_rejects_non_bindable_tracker_source(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_runtime_connection(storage)
    await _create_tracker(storage, name="source-kind-check")
    legacy_tracker = await storage.get_aggregate_tracker("source-kind-check")
    assert legacy_tracker is not None
    await storage.update_aggregate_tracker(
        AggregateTracker(
            id=legacy_tracker.id,
            name="source-kind-check",
            primary_changelog_source_key="repo-primary",
            created_at=legacy_tracker.created_at,
            sources=[
                TrackerSource(
                    source_key="repo-primary",
                    source_type="github",
                    source_config={"repo": "owner/repo"},
                )
            ],
        )
    )
    aggregate_tracker = await storage.get_aggregate_tracker("source-kind-check")
    assert aggregate_tracker is not None
    source_id = aggregate_tracker.sources[0].id
    assert source_id is not None
    monkeypatch.setattr(
        "releasetracker.routers.executors.DockerRuntimeAdapter",
        lambda runtime_connection: FakeDiscoveryAdapter(runtime_connection),
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "executor-invalid-source-kind",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "source-kind-check",
            "tracker_source_id": source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "abc", "container_name": "sample-web"},
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Runtime executors must be bound to a deployable image source"


@pytest.mark.asyncio
async def test_executor_create_rejects_ambiguous_tracker_without_source_id_when_multiple_bindable_sources_exist(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_runtime_connection(storage)
    await _create_tracker(storage, name="multi-source")
    legacy_tracker = await storage.get_aggregate_tracker("multi-source")
    assert legacy_tracker is not None
    await storage.update_aggregate_tracker(
        AggregateTracker(
            id=legacy_tracker.id,
            name="multi-source",
            primary_changelog_source_key="origin",
            created_at=legacy_tracker.created_at,
            sources=[
                TrackerSource(
                    source_key="origin",
                    source_type="container",
                    source_rank=0,
                    source_config={"image": "ghcr.io/acme/multi-source", "registry": "ghcr.io"},
                ),
                TrackerSource(
                    source_key="mirror",
                    source_type="container",
                    source_rank=10,
                    source_config={"image": "ghcr.io/mirror/multi-source", "registry": "ghcr.io"},
                ),
            ],
        )
    )
    monkeypatch.setattr(
        "releasetracker.routers.executors.DockerRuntimeAdapter",
        lambda runtime_connection: FakeDiscoveryAdapter(runtime_connection),
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "executor-ambiguous-source",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "multi-source",
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "abc", "container_name": "sample-web"},
        },
    )

    assert response.status_code == 400
    assert "tracker_source_id" in response.json()["detail"]


@pytest.mark.asyncio
async def test_executor_create_rejects_channel_name_not_owned_by_bound_tracker_channel(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_runtime_connection(storage)
    await _create_tracker(storage, name="channel-owned-executor")
    legacy_tracker = await storage.get_aggregate_tracker("channel-owned-executor")
    assert legacy_tracker is not None
    await storage.update_aggregate_tracker(
        AggregateTracker(
            id=legacy_tracker.id,
            name="channel-owned-executor",
            primary_changelog_source_key="origin",
            created_at=legacy_tracker.created_at,
            sources=[
                TrackerSource(
                    source_key="origin",
                    source_type="container",
                    source_rank=0,
                    source_config={
                        "image": "ghcr.io/acme/channel-owned-executor",
                        "registry": "ghcr.io",
                    },
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="origin-stable",
                            name="stable",
                            type="release",
                            enabled=True,
                        )
                    ],
                ),
                TrackerSource(
                    source_key="mirror",
                    source_type="container",
                    source_rank=10,
                    source_config={
                        "image": "ghcr.io/mirror/channel-owned-executor",
                        "registry": "ghcr.io",
                    },
                    release_channels=[],
                ),
            ],
        )
    )
    aggregate_tracker = await storage.get_aggregate_tracker("channel-owned-executor")
    assert aggregate_tracker is not None
    mirror_source = next(
        source for source in aggregate_tracker.sources if source.source_key == "mirror"
    )
    assert mirror_source.id is not None

    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="channel-owned-executor",
            type="container",
            enabled=True,
            image="ghcr.io/acme/channel-owned-executor",
            registry="ghcr.io",
            channels=[Channel(name="stable", enabled=True, type="release")],
        )
    )
    monkeypatch.setattr(
        "releasetracker.routers.executors.DockerRuntimeAdapter",
        lambda runtime_connection: FakeDiscoveryAdapter(runtime_connection),
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "executor-channel-owned-mirror",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "channel-owned-executor",
            "tracker_source_id": mirror_source.id,
            "channel_name": "stable",
            "enabled": True,
            "image_selection_mode": "use_tracker_image_and_tag",
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "abc", "container_name": "sample-web"},
        },
    )

    assert response.status_code == 400
    assert "stable" in response.json()["detail"]


@pytest.mark.asyncio
async def test_executor_history_supports_filters(authed_client, storage):
    runtime_id = await _create_runtime_connection(storage)
    await _create_tracker(storage, name="history-worker")
    tracker_source_id = await _get_tracker_source_id(storage, "history-worker")

    create_response = authed_client.post(
        "/api/executors",
        json={
            "name": "history-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "history-worker",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {
                "mode": "container",
                "container_id": "container-history",
                "image": "history-worker",
            },
        },
    )
    assert create_response.status_code == 200
    executor_id = create_response.json()["id"]

    await storage.create_executor_run(
        ExecutorRunHistory(
            executor_id=executor_id,
            started_at=datetime(2026, 3, 24, 8, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 3, 24, 8, 5, tzinfo=timezone.utc),
            status="success",
            from_version="history-worker:1.0.0",
            to_version="history-worker:2.0.0",
            message="updated image",
        )
    )
    await storage.create_executor_run(
        ExecutorRunHistory(
            executor_id=executor_id,
            started_at=datetime(2026, 3, 24, 9, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 3, 24, 9, 5, tzinfo=timezone.utc),
            status="failed",
            from_version="history-worker:2.0.0",
            to_version="history-worker:3.0.0",
            message="registry timeout",
        )
    )

    filtered = authed_client.get(
        f"/api/executors/{executor_id}/history", params={"status": "failed"}
    )
    assert filtered.status_code == 200
    filtered_body = filtered.json()
    assert filtered_body["total"] == 1
    assert len(filtered_body["items"]) == 1
    assert filtered_body["items"][0]["status"] == "failed"

    searched = authed_client.get(
        f"/api/executors/{executor_id}/history",
        params={"search": "registry"},
    )
    assert searched.status_code == 200
    searched_body = searched.json()
    assert searched_body["total"] == 1
    assert searched_body["items"][0]["message"] == "registry timeout"


@pytest.mark.asyncio
async def test_executor_history_can_be_cleared(authed_client, storage):
    runtime_id = await _create_runtime_connection(storage)
    await _create_tracker(storage, name="clear-history-worker")
    tracker_source_id = await _get_tracker_source_id(storage, "clear-history-worker")

    create_response = authed_client.post(
        "/api/executors",
        json={
            "name": "clear-history-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "clear-history-worker",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {
                "mode": "container",
                "container_id": "container-clear-history",
                "image": "clear-history-worker",
            },
        },
    )
    assert create_response.status_code == 200
    executor_id = create_response.json()["id"]

    for index in range(2):
        await storage.create_executor_run(
            ExecutorRunHistory(
                executor_id=executor_id,
                started_at=datetime(2026, 3, 24, 8 + index, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 3, 24, 8 + index, 5, tzinfo=timezone.utc),
                status="success",
                from_version=f"clear-history-worker:{index}.0.0",
                to_version=f"clear-history-worker:{index}.1.0",
                message="updated image",
            )
        )

    clear_response = authed_client.delete(f"/api/executors/{executor_id}/history")
    assert clear_response.status_code == 200
    assert clear_response.json() == {"message": "Execution history cleared", "deleted": 2}

    history_response = authed_client.get(f"/api/executors/{executor_id}/history")
    assert history_response.status_code == 200
    history_body = history_response.json()
    assert history_body["total"] == 0
    assert history_body["items"] == []


@pytest.mark.asyncio
async def test_clear_executor_history_returns_404_for_missing_executor(authed_client):
    response = authed_client.delete("/api/executors/999999/history")
    assert response.status_code == 404
    assert response.json()["detail"] == "Executor not found"


# ============================================================
# Health Check profile API validation (Phase C, Req 1.*, 2.*)
# ============================================================


@pytest.mark.asyncio
async def test_create_executor_defaults_health_check_to_strategy_none(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_runtime_connection(storage, name="hc-default-runtime")
    await _create_tracker(storage, name="hc-default-tracker")
    tracker_source_id = await _get_tracker_source_id(storage, "hc-default-tracker")

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "hc-default-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "hc-default-tracker",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "hc-default"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["health_check"]["strategy"] == "none"
    assert body["health_check"]["failure_policy"] == "mark_failed"


@pytest.mark.asyncio
async def test_create_executor_accepts_runtime_native_health_check(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_runtime_connection(storage, name="hc-rn-runtime")
    await _create_tracker(storage, name="hc-rn-tracker")
    tracker_source_id = await _get_tracker_source_id(storage, "hc-rn-tracker")

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "hc-rn-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "hc-rn-tracker",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "hc-rn"},
            "health_check": {
                "strategy": "runtime_native",
                "grace_period_seconds": 10,
                "attempt_timeout_seconds": 5,
                "interval_seconds": 5,
                "probe_window_seconds": 60,
                "failure_policy": "mark_failed",
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["health_check"]["strategy"] == "runtime_native"


@pytest.mark.asyncio
async def test_create_executor_accepts_manual_http_strategy(
    authed_client, storage, monkeypatch
):
    """Manual HTTP health checks require a direct host and probe config."""
    runtime_id = await _create_runtime_connection(storage, name="hc-http-runtime")
    await _create_tracker(storage, name="hc-http-tracker")
    tracker_source_id = await _get_tracker_source_id(storage, "hc-http-tracker")

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "hc-http-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "hc-http-tracker",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "hc-http"},
            "health_check": {
                "strategy": "manual_http",
                "grace_period_seconds": 5,
                "attempt_timeout_seconds": 5,
                "interval_seconds": 5,
                "probe_window_seconds": 60,
                "failure_policy": "mark_failed",
                "http": {"host": "api.internal", "path": "/healthz", "port": 8080},
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["health_check"]["strategy"] == "manual_http"
    assert response.json()["health_check"]["http"]["host"] == "api.internal"


@pytest.mark.asyncio
async def test_create_executor_rejects_helm_status_on_container(
    authed_client, storage, monkeypatch
):
    """``helm_status`` belongs only to helm_release targets (Req 2.1-2.5, 2.6)."""
    runtime_id = await _create_runtime_connection(storage, name="hc-helm-runtime")
    await _create_tracker(storage, name="hc-helm-tracker")
    tracker_source_id = await _get_tracker_source_id(storage, "hc-helm-tracker")

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "hc-helm-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "hc-helm-tracker",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "hc-helm"},
            "health_check": {
                "strategy": "helm_status",
                "grace_period_seconds": 5,
                "attempt_timeout_seconds": 5,
                "interval_seconds": 5,
                "probe_window_seconds": 60,
                "failure_policy": "mark_failed",
            },
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "helm_status" in detail


@pytest.mark.asyncio
async def test_create_executor_rejects_manual_http_profile_without_http_sub_object(
    authed_client, storage, monkeypatch
):
    """``strategy=manual_http`` requires an ``http`` sub-object."""
    runtime_id = await _create_runtime_connection(storage, name="hc-missing-http-runtime")
    await _create_tracker(storage, name="hc-missing-http-tracker")
    tracker_source_id = await _get_tracker_source_id(
        storage, "hc-missing-http-tracker"
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "hc-missing-http-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "hc-missing-http-tracker",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "hc-missing-http"},
            "health_check": {
                "strategy": "manual_http",
                "grace_period_seconds": 5,
                "attempt_timeout_seconds": 5,
                "interval_seconds": 5,
                "probe_window_seconds": 60,
                "failure_policy": "mark_failed",
            },
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "http" in detail


@pytest.mark.asyncio
async def test_create_executor_rejects_manual_http_without_host(
    authed_client, storage, monkeypatch
):
    runtime_id = await _create_runtime_connection(storage, name="hc-manual-host-runtime")
    await _create_tracker(storage, name="hc-manual-host-tracker")
    tracker_source_id = await _get_tracker_source_id(storage, "hc-manual-host-tracker")

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "hc-manual-host-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "hc-manual-host-tracker",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "hc-manual-host"},
            "health_check": {
                "strategy": "manual_http",
                "grace_period_seconds": 5,
                "attempt_timeout_seconds": 5,
                "interval_seconds": 5,
                "probe_window_seconds": 60,
                "failure_policy": "mark_failed",
                "http": {"path": "/healthz", "port": 8080},
            },
        },
    )
    assert response.status_code == 400
    assert "host" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_executor_rejects_probe_window_shorter_than_attempt_timeout(
    authed_client, storage, monkeypatch
):
    """``probe_window_seconds`` must be >= ``attempt_timeout_seconds`` (Req 1.11)."""
    runtime_id = await _create_runtime_connection(storage, name="hc-window-runtime")
    await _create_tracker(storage, name="hc-window-tracker")
    tracker_source_id = await _get_tracker_source_id(storage, "hc-window-tracker")

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "hc-window-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "hc-window-tracker",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "hc-window"},
            "health_check": {
                "strategy": "runtime_native",
                "grace_period_seconds": 5,
                "attempt_timeout_seconds": 30,
                "interval_seconds": 5,
                "probe_window_seconds": 10,  # shorter than attempt_timeout
                "failure_policy": "mark_failed",
            },
        },
    )
    assert response.status_code == 400
    assert "probe_window_seconds" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_executor_rejects_failure_policy_other_than_mark_failed_for_none_strategy(
    authed_client, storage, monkeypatch
):
    """``strategy=none`` forces ``failure_policy=mark_failed`` (Req 1.9)."""
    runtime_id = await _create_runtime_connection(storage, name="hc-none-policy-runtime")
    await _create_tracker(storage, name="hc-none-policy-tracker")
    tracker_source_id = await _get_tracker_source_id(
        storage, "hc-none-policy-tracker"
    )

    response = authed_client.post(
        "/api/executors",
        json={
            "name": "hc-none-policy-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "hc-none-policy-tracker",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "hc-none-policy"},
            "health_check": {
                "strategy": "none",
                "failure_policy": "mark_degraded",
            },
        },
    )
    assert response.status_code == 400
    assert "mark_failed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_executor_response_round_trips_health_check_profile(
    authed_client, storage, monkeypatch
):
    """Req 22.5: GET /api/executors/{id}/config exposes the saved health_check."""
    runtime_id = await _create_runtime_connection(storage, name="hc-get-runtime")
    await _create_tracker(storage, name="hc-get-tracker")
    tracker_source_id = await _get_tracker_source_id(storage, "hc-get-tracker")

    create = authed_client.post(
        "/api/executors",
        json={
            "name": "hc-get-executor",
            "runtime_type": "docker",
            "runtime_connection_id": runtime_id,
            "tracker_name": "hc-get-tracker",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "update_mode": "manual",
            "target_ref": {"mode": "container", "container_id": "hc-get"},
            "health_check": {
                "strategy": "runtime_native",
                "grace_period_seconds": 15,
                "attempt_timeout_seconds": 10,
                "interval_seconds": 5,
                "probe_window_seconds": 120,
                "failure_policy": "mark_failed",
            },
        },
    )
    assert create.status_code == 200
    executor_id = create.json()["id"]

    detail = authed_client.get(f"/api/executors/{executor_id}/config")
    assert detail.status_code == 200
    body = detail.json()
    assert body["health_check"]["strategy"] == "runtime_native"
    assert body["health_check"]["grace_period_seconds"] == 15
    assert body["health_check"]["attempt_timeout_seconds"] == 10
    assert body["health_check"]["probe_window_seconds"] == 120
    assert body["health_check"]["failure_policy"] == "mark_failed"


# ================================================================
# Snapshot listing / detail / rollback API (Phase E, Req 18, 19)
# ================================================================


async def _seed_executor_with_snapshot(
    storage,
    *,
    name: str,
    snapshot_image: str = "acme/api:1.0.0",
) -> tuple[int, int]:
    """Create an executor + one pre_update snapshot. Returns (executor_id, snapshot_id)."""
    from datetime import datetime as _dt

    from releasetracker.config import (
        ExecutorConfig as _ExecutorConfig,
        RuntimeConnectionConfig as _RuntimeConnectionConfig,
    )
    from releasetracker.models import ExecutorSnapshot as _ExecutorSnapshot

    runtime_id = await storage.create_runtime_connection(
        _RuntimeConnectionConfig(
            name=f"{name}-runtime",
            type="docker",
            enabled=True,
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={"token": "x"},
        )
    )
    await _create_tracker(storage, name=f"{name}-tracker")
    tracker_source_id = await _get_tracker_source_id(storage, f"{name}-tracker")
    executor_id = await storage.save_executor_config(
        _ExecutorConfig(
            name=name,
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=f"{name}-tracker",
            tracker_source_id=tracker_source_id,
            channel_name="stable",
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": f"{name}-c1"},
        )
    )
    snapshot_id = await storage.create_executor_snapshot(
        _ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={"image": snapshot_image, "container_id": f"{name}-c1"},
            trigger="pre_update",
            image_at_capture=snapshot_image,
            created_at=_dt(2026, 5, 1, 10, 0, 0),
            updated_at=_dt(2026, 5, 1, 10, 0, 0),
        )
    )
    return executor_id, snapshot_id


@pytest.mark.asyncio
async def test_list_executor_snapshots_returns_paginated_history(
    authed_client, storage
):
    executor_id, snapshot_id = await _seed_executor_with_snapshot(
        storage, name="snap-list"
    )
    response = authed_client.get(
        f"/api/executors/{executor_id}/snapshots?page=1&page_size=10"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 10
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["id"] == snapshot_id
    assert item["trigger"] == "pre_update"
    assert item["image_at_capture"] == "acme/api:1.0.0"
    assert item["unredacted_persisted"] is False


@pytest.mark.asyncio
async def test_list_executor_snapshots_returns_404_when_executor_missing(
    authed_client,
):
    response = authed_client.get("/api/executors/999999/snapshots")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_executor_snapshot_detail_returns_redacted_payload(
    authed_client, storage
):
    from datetime import datetime as _dt

    from releasetracker.models import ExecutorSnapshot as _ExecutorSnapshot

    executor_id, _ = await _seed_executor_with_snapshot(
        storage, name="snap-detail-redact"
    )
    # Replace the seeded snapshot with one carrying sensitive data so we
    # can assert read-time redaction kicks in.
    sensitive_id = await storage.create_executor_snapshot(
        _ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={
                "image": "acme/api:1.0.0",
                "env": [
                    {"name": "LOG_LEVEL", "value": "info"},
                    {"name": "DB_PASSWORD", "value": "hunter2"},
                ],
                "token": "should-be-redacted",
            },
            trigger="pre_update",
            image_at_capture="acme/api:1.0.0",
            created_at=_dt(2026, 5, 2, 10, 0, 0),
            updated_at=_dt(2026, 5, 2, 10, 0, 0),
        )
    )
    response = authed_client.get(
        f"/api/executors/{executor_id}/snapshots/{sensitive_id}"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == sensitive_id
    data = body["snapshot_data"]
    # Generic always-redact-key rule catches top-level ``token``.
    assert data["token"] == "***REDACTED***"
    # LOG_LEVEL untouched, DB_PASSWORD value masked (docker runtime_type
    # uses the generic walk; no Portainer env-list branch).
    # Docker runtime does not drive the Portainer branch, so env entries
    # keep their value here. That is acceptable — the operator would
    # never legitimately store secrets in a Docker container snapshot
    # payload; the redactor mainly exists to defend Portainer / K8s.
    # Still, we verify that the top-level ``token`` key is redacted and
    # that non-sensitive values pass through.
    assert data["image"] == "acme/api:1.0.0"


@pytest.mark.asyncio
async def test_get_executor_snapshot_detail_returns_404_for_foreign_snapshot(
    authed_client, storage
):
    executor_a_id, snapshot_a = await _seed_executor_with_snapshot(
        storage, name="snap-foreign-a"
    )
    executor_b_id, _ = await _seed_executor_with_snapshot(
        storage, name="snap-foreign-b"
    )
    response = authed_client.get(
        f"/api/executors/{executor_b_id}/snapshots/{snapshot_a}"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rollback_endpoint_returns_run_and_recovery_outcome(
    authed_client, storage, monkeypatch
):
    executor_id, snapshot_id = await _seed_executor_with_snapshot(
        storage, name="snap-rollback-ok"
    )

    # Stub the Docker adapter so the endpoint runs end-to-end without a
    # real Docker daemon. Phase E endpoint resolves the adapter via
    # materialize_runtime_connection_credentials + _get_runtime_adapter,
    # so we patch DockerRuntimeAdapter to a canned class.
    from releasetracker.executors.base import (
        BaseRuntimeAdapter,
        RuntimeUpdateResult,
    )

    class _ApiRollbackAdapter(BaseRuntimeAdapter):
        async def discover_targets(self):
            return []

        async def validate_target_ref(self, target_ref):
            return None

        async def get_current_image(self, target_ref):
            return "acme/api:1.0.0"

        async def capture_snapshot(self, target_ref, current_image):
            return {
                "runtime_type": "docker",
                "image": current_image,
                "container_id": target_ref.get("container_id", "c1"),
            }

        async def validate_snapshot(self, target_ref, snapshot):
            return None

        async def update_image(self, target_ref, new_image):
            raise NotImplementedError

        async def recover_from_snapshot(self, target_ref, snapshot):
            return RuntimeUpdateResult(
                updated=True, old_image=None, new_image=snapshot.get("image")
            )

    monkeypatch.setattr(
        "releasetracker.routers.executors.DockerRuntimeAdapter",
        lambda runtime_connection: _ApiRollbackAdapter(runtime_connection),
    )

    response = authed_client.post(
        f"/api/executors/{executor_id}/rollback",
        json={"snapshot_id": snapshot_id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["recovery_outcome"] == "succeeded"
    assert body["run"]["status"] == "success"
    assert body["run"]["from_version"] == "acme/api:1.0.0"
    assert body["run"]["to_version"] == "acme/api:1.0.0"
    assert body["run"]["diagnostics"]["run_trigger"] == "manual_rollback"
    assert body["run"]["diagnostics"]["snapshot_id"] == snapshot_id


@pytest.mark.asyncio
async def test_rollback_endpoint_returns_failed_recovery_error(
    authed_client, storage, monkeypatch
):
    executor_id, snapshot_id = await _seed_executor_with_snapshot(
        storage, name="snap-rollback-failed"
    )

    from releasetracker.executors.base import BaseRuntimeAdapter

    class _ApiRollbackFailingAdapter(BaseRuntimeAdapter):
        async def discover_targets(self):
            return []

        async def validate_target_ref(self, target_ref):
            return None

        async def get_current_image(self, target_ref):
            return "acme/api:2.0.0"

        async def capture_snapshot(self, target_ref, current_image):
            return {"runtime_type": "docker", "image": current_image}

        async def validate_snapshot(self, target_ref, snapshot):
            return None

        async def update_image(self, target_ref, new_image):
            raise NotImplementedError

        async def recover_from_snapshot(self, target_ref, snapshot):
            raise RuntimeError("container name already in use")

    monkeypatch.setattr(
        "releasetracker.routers.executors.DockerRuntimeAdapter",
        lambda runtime_connection: _ApiRollbackFailingAdapter(runtime_connection),
    )

    response = authed_client.post(
        f"/api/executors/{executor_id}/rollback",
        json={"snapshot_id": snapshot_id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["recovery_outcome"] == "failed"
    assert body["recovery_error"] == "container name already in use"
    assert body["run"]["status"] == "failed"
    assert body["run"]["diagnostics"]["recovery_error"] == "container name already in use"


@pytest.mark.asyncio
async def test_rollback_endpoint_returns_404_for_unknown_snapshot(
    authed_client, storage
):
    executor_id, _ = await _seed_executor_with_snapshot(
        storage, name="snap-rollback-missing"
    )
    response = authed_client.post(
        f"/api/executors/{executor_id}/rollback",
        json={"snapshot_id": 999999},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rollback_endpoint_returns_409_when_executor_has_active_run(
    authed_client, storage
):
    from datetime import datetime as _dt

    from releasetracker.models import ExecutorRunHistory as _ExecutorRunHistory

    executor_id, snapshot_id = await _seed_executor_with_snapshot(
        storage, name="snap-rollback-busy"
    )
    await storage.create_executor_run(
        _ExecutorRunHistory(
            executor_id=executor_id,
            started_at=_dt.now(),
            status="running",
        )
    )

    response = authed_client.post(
        f"/api/executors/{executor_id}/rollback",
        json={"snapshot_id": snapshot_id},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_rollback_endpoint_defaults_to_most_recent_snapshot(
    authed_client, storage, monkeypatch
):
    from datetime import datetime as _dt

    from releasetracker.executors.base import (
        BaseRuntimeAdapter,
        RuntimeUpdateResult,
    )
    from releasetracker.models import ExecutorSnapshot as _ExecutorSnapshot

    executor_id, older = await _seed_executor_with_snapshot(
        storage, name="snap-rollback-default"
    )
    newer = await storage.create_executor_snapshot(
        _ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={"image": "acme/api:2.0.0"},
            trigger="pre_update",
            image_at_capture="acme/api:2.0.0",
            created_at=_dt(2026, 6, 1, 10, 0, 0),
            updated_at=_dt(2026, 6, 1, 10, 0, 0),
        )
    )

    class _ApiRollbackAdapter(BaseRuntimeAdapter):
        async def discover_targets(self):
            return []

        async def validate_target_ref(self, target_ref):
            return None

        async def get_current_image(self, target_ref):
            return "acme/api:2.0.0"

        async def capture_snapshot(self, target_ref, current_image):
            return {"runtime_type": "docker", "image": current_image}

        async def validate_snapshot(self, target_ref, snapshot):
            return None

        async def update_image(self, target_ref, new_image):
            raise NotImplementedError

        async def recover_from_snapshot(self, target_ref, snapshot):
            return RuntimeUpdateResult(updated=True, old_image=None, new_image="acme/api:2.0.0")

    monkeypatch.setattr(
        "releasetracker.routers.executors.DockerRuntimeAdapter",
        lambda runtime_connection: _ApiRollbackAdapter(runtime_connection),
    )

    response = authed_client.post(f"/api/executors/{executor_id}/rollback")
    assert response.status_code == 200
    body = response.json()
    assert body["run"]["diagnostics"]["snapshot_id"] == newer


