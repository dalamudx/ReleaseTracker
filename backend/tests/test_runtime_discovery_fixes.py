"""Draft discovery and live grouped image responses preserve configuration identity."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.config import ExecutorConfig, RuntimeConnectionConfig, ExecutorServiceBinding
from releasetracker.routers import executors, runtime_connections


@pytest.mark.parametrize("name", [None, "", "   "])
async def test_kubernetes_draft_can_discover_before_name_is_entered(storage, name):
    request = {"type": "kubernetes", "name": name, "config": {"in_cluster": True}}
    config = await runtime_connections._build_runtime_connection_config(storage, request)
    assert config.name == "draft-kubernetes"
    assert config.type == "kubernetes"


@pytest.mark.parametrize(
    "kind,mode,method",
    [
        ("kubernetes", "kubernetes_workload", "fetch_workload_service_images"),
        ("portainer", "portainer_stack", "fetch_stack_service_images"),
    ],
)
@pytest.mark.parametrize("multiple", [True, False])
async def test_config_contains_live_images_without_changing_target_ref(
    monkeypatch, kind, mode, method, multiple
):
    target = {"mode": mode, "services": [{"service": "api", "image": "app:stale"}]}
    if kind == "kubernetes":
        target.update(namespace="apps", kind="Deployment", name="app")
    else:
        target.update(endpoint_id=1, stack_id=1, stack_name="app", stack_type="standalone")
    executor = ExecutorConfig(
        id=7,
        name="app",
        runtime_type=kind,
        runtime_connection_id=1,
        tracker_name="release-tracker",
        target_ref=target,
        service_bindings=[
            ExecutorServiceBinding(service="api", tracker_source_id=1, channel_name="stable")
        ],
    )
    runtime = RuntimeConnectionConfig(
        id=1,
        name="runtime",
        type=kind,
        credential_id=1,
        config=(
            {"in_cluster": True}
            if kind == "kubernetes"
            else {"base_url": "https://portainer.test", "endpoint_id": 1}
        ),
    )
    image = "app@sha256:" + "a" * 64
    images = {"api": image, **({"sidecar": "sidecar:2"} if multiple else {})}
    adapter = SimpleNamespace(close=AsyncMock(), **{method: AsyncMock(return_value=images)})
    storage = SimpleNamespace(
        get_executor_config=AsyncMock(return_value=executor),
        get_runtime_connection=AsyncMock(return_value=runtime),
    )
    monkeypatch.setattr(executors, "_get_runtime_adapter", lambda config: adapter)
    monkeypatch.setattr(
        executors, "materialize_runtime_connection_credentials", AsyncMock(return_value=runtime)
    )
    original_target = dict(executor.target_ref)
    payload = await executors.get_executor_config_detail(7, storage)
    assert payload["current_images"] == images
    assert payload["current_image"] == (None if multiple else image)
    assert payload["target_ref"] == original_target
    assert executor.target_ref == original_target
    adapter.close.assert_awaited_once()
    getattr(adapter, method).assert_awaited_once_with(original_target)


async def test_discover_namespaces_without_draft_name(authed_client, monkeypatch):
    from releasetracker.executors.kubernetes import KubernetesRuntimeAdapter

    api = SimpleNamespace(
        list_namespace=lambda: SimpleNamespace(
            items=[
                SimpleNamespace(metadata=SimpleNamespace(name="apps")),
                SimpleNamespace(metadata=SimpleNamespace(name="default")),
            ]
        )
    )
    monkeypatch.setattr(KubernetesRuntimeAdapter, "_get_core_api", lambda self: api)
    response = authed_client.post(
        "/api/runtime-connections/discover-kubernetes-namespaces",
        json={"type": "kubernetes", "config": {"in_cluster": True}},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"items": ["apps", "default"]}


@pytest.mark.parametrize("status", [403, 404])
async def test_podman_docker_api_proxy_error_has_actionable_hint(status):
    from releasetracker.executors.podman import PodmanRuntimeAdapter

    class ProxyRejected(Exception):
        response = SimpleNamespace(status_code=status)

    def containers_list(**kwargs):
        raise ProxyRejected(str(status))

    runtime = RuntimeConnectionConfig(
        name="proxy", type="podman", config={"socket": "tcp://proxy.test:2375"}
    )
    client = SimpleNamespace(containers=SimpleNamespace(list=containers_list))
    adapter = PodmanRuntimeAdapter(runtime, client=client)
    with pytest.raises(ValueError, match="select Docker mode"):
        await adapter.discover_targets()
    assert runtime.type == "podman"
