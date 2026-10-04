"""Draft previews never authorize a deployment or bypass recovery identity checks."""

from copy import deepcopy
from unittest.mock import Mock

import httpx
import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.docker import DockerRuntimeAdapter
from releasetracker.executors.portainer import PortainerRuntimeAdapter
from releasetracker.models import Credential
from releasetracker.routers import executors as routes
from releasetracker.services.deployment_diff import (
    INSPECTED_UPDATE_STATE,
    frozen_targets,
    native_review,
)
from releasetracker.services.deployment_targets import resolve_deployment_targets
from test_deployment_configuration_diff import setup
from test_docker_engine_identity import OTHER, runtime as runtime
from test_executor_adapters import DOCKER_ENGINE_ID

pytestmark = pytest.mark.asyncio


async def preview(storage, scheduler, executor, draft):
    tasks_before = await storage.tasks.list()
    config_before = await storage.get_executor_config(executor.id)
    update_state_before = INSPECTED_UPDATE_STATE.get()
    result = await routes.preview_executor_configuration(
        routes.ExecutorConfigurationPreviewRequest(executor_id=executor.id, configuration=draft),
        storage,
        scheduler,
    )
    assert await storage.tasks.list() == tasks_before
    assert await storage.get_executor_config(executor.id) == config_before
    assert INSPECTED_UPDATE_STATE.get() is update_state_before
    return result


async def strict_review(storage, scheduler, draft, adapter, *, inspection_only=False):
    executor = await routes._validate_executor_payload(storage, draft, validate_runtime=False)
    targets = await resolve_deployment_targets(storage, executor, manual=True)
    assert targets
    task = {"payload": {"targets": targets}}
    with frozen_targets(task):
        return await native_review(
            storage, scheduler, executor, adapter, task, inspection_only=inspection_only
        )


@pytest.mark.parametrize("group", [False, True])
async def test_docker_preview_reads_config_despite_random_daemon_id(
    storage, monkeypatch, runtime, group
):
    executor, scheduler, _, _, _, _ = await setup(storage)
    try:
        draft = executor.model_dump(mode="json")
        draft["target_ref"] = (
            {"mode": "docker_compose", "project": "example-project"}
            if group
            else {"mode": "container", "container_name": "service-a"}
        )
        if group:
            draft["service_bindings"] = [
                {
                    "service": "service-a",
                    "tracker_source_id": executor.tracker_source_id,
                    "channel_name": executor.channel_name,
                }
            ]
            draft.update(tracker_name=None, tracker_source_id=None, channel_name=None)
        runtime.client.info = Mock(side_effect=[{"ID": DOCKER_ENGINE_ID}, {"ID": OTHER}])
        monkeypatch.setattr(
            routes,
            "_get_runtime_adapter",
            lambda _: DockerRuntimeAdapter(
                runtime.adapter.runtime_connection, client=runtime.client
            ),
        )
        result = await preview(storage, scheduler, executor, draft)
        assert result["comparison_error"] is None
        assert result["configuration_diff"]["lines"]
        assert result["mutation_performed"] is False
        runtime.client.info.assert_not_called()
        assert await storage.get_executor_config(executor.id) == executor
        with pytest.raises(ValueError, match="identity changed"):
            await strict_review(storage, scheduler, draft, runtime.adapter)
        assert runtime.existing.remove_calls == []
        assert runtime.client.containers.create_calls == []
        assert runtime.client.images.pull_calls == []
    finally:
        await scheduler.shutdown()


def portainer_transport(*, fault="random_id"):
    calls = []
    stack = {
        "Id": 1,
        "Name": "preview-test",
        "EndpointId": 1,
        "Type": 2,
        "Env": [{"name": "PASSWORD", "value": "private-preview-value"}],
    }
    image = "docker.io/library/nginx:1.25"
    file = f"services:\n  api:\n    image: {image}\n    environment:\n      PASSWORD: private-preview-value\n"
    if fault == "pull_always":
        file += "    pull_policy: always\n"
    if fault == "interpolation":
        file = file.replace(image, "docker.io/library/nginx:${IMAGE_TAG}")
    container = {
        "Id": "preview-container",
        "Image": "sha256:" + "b" * 64,
        "Config": {
            "Image": image,
            "Labels": {
                "com.docker.compose.project": "preview-test",
                "com.docker.compose.service": "api",
            },
        },
        "State": {"Status": "running", "Running": True},
        "HostConfig": {},
    }

    def respond(request):
        assert request.method == "GET", "draft inspection must never mutate anything"
        calls.append(request.url.path)
        path = request.url.path
        if path == "/api/stacks/1":
            if fault == "auth":
                return httpx.Response(403, json={"message": "private-preview-value"})
            result = deepcopy(stack)
            if fault == "wrong_stack":
                result["Name"] = "another-stack"
            return httpx.Response(200, json=result)
        if path == "/api/stacks/1/file":
            return httpx.Response(200, json={"StackFileContent": file})
        if path == "/api/endpoints/1/docker/info":
            return httpx.Response(200, json={"ID": f"engine-{calls.count(path)}"})
        if path == "/api/endpoints/1/docker/containers/json":
            return httpx.Response(200, json=[{"Id": "preview-container"}])
        if path == "/api/endpoints/1/docker/containers/preview-container/json":
            return httpx.Response(200, json=container)
        raise AssertionError(f"unexpected request: {path}")

    return httpx.MockTransport(respond), calls


async def portainer_draft(storage, executor):
    credential_id = await storage.create_credential(
        Credential(
            name="preview-api-key", type="portainer_runtime", secrets={"api_key": "synthetic-key"}
        )
    )
    connection = RuntimeConnectionConfig(
        name="preview-portainer",
        type="portainer",
        credential_id=credential_id,
        config={"base_url": "http://portainer.test", "endpoint_id": 1},
        secrets={"api_key": "synthetic-key"},
    )
    connection.id = await storage.create_runtime_connection(connection)
    draft = executor.model_dump(mode="json")
    draft.update(
        runtime_type="portainer",
        runtime_connection_id=connection.id,
        target_ref={
            "mode": "portainer_stack",
            "endpoint_id": 1,
            "stack_id": 1,
            "stack_name": "preview-test",
            "stack_type": "standalone",
        },
        service_bindings=[
            {
                "service": "api",
                "tracker_source_id": executor.tracker_source_id,
                "channel_name": executor.channel_name,
            }
        ],
        tracker_name=None,
        tracker_source_id=None,
        channel_name=None,
    )
    return connection, draft


@pytest.mark.parametrize("fault", ["random_id", "pull_always", "interpolation"])
async def test_portainer_preview_is_not_a_recovery_snapshot(storage, monkeypatch, fault):
    executor, scheduler, _, _, _, _ = await setup(storage)
    try:
        connection, draft = await portainer_draft(storage, executor)
        transport, calls = portainer_transport(fault=fault)
        async with httpx.AsyncClient(
            base_url="http://portainer.test", transport=transport
        ) as client:
            adapter = PortainerRuntimeAdapter(connection, client=client)
            await strict_review(storage, scheduler, draft, adapter, inspection_only=True)
            monkeypatch.setattr(
                routes,
                "_get_runtime_adapter",
                lambda _: PortainerRuntimeAdapter(connection, client=client),
            )
            result = await preview(storage, scheduler, executor, draft)
            assert result["comparison_error"] is None, calls
            assert result["configuration_diff"]["scope"] == "stack_configuration"
            assert result["configuration_diff"]["lines"]
            assert "private-preview-value" not in str(result)
            assert result["mutation_performed"] is False
            assert "/api/endpoints/1/docker/info" not in calls
            assert await storage.get_executor_config(executor.id) == executor
            with pytest.raises(ValueError):
                await strict_review(storage, scheduler, draft, adapter)
    finally:
        await scheduler.shutdown()


@pytest.mark.parametrize("fault", ["auth", "wrong_stack"])
async def test_portainer_preview_still_rejects_auth_and_target_mismatch(
    storage, monkeypatch, fault
):
    executor, scheduler, _, _, _, _ = await setup(storage)
    try:
        connection, draft = await portainer_draft(storage, executor)
        transport, _ = portainer_transport(fault=fault)
        async with httpx.AsyncClient(
            base_url="http://portainer.test", transport=transport
        ) as client:
            adapter = PortainerRuntimeAdapter(connection, client=client)
            monkeypatch.setattr(routes, "_get_runtime_adapter", lambda _: adapter)
            result = await preview(storage, scheduler, executor, draft)
            assert result["comparison_error"] == "runtime_inspection_failed"
            assert result["configuration_diff"] is None
            assert "private-preview-value" not in str(result)
    finally:
        await scheduler.shutdown()
