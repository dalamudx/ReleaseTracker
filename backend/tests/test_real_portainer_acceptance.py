"""Opt-in Portainer 2.45.1 Stack deploy/recovery, wholly inside an owned DinD."""

from copy import deepcopy
import os
import secrets

import pytest

from releasetracker.config import ExecutorConfig, ExecutorServiceBinding, RuntimeConnectionConfig
from releasetracker.executors.portainer import PortainerRuntimeAdapter
from releasetracker.models import Credential
from releasetracker.routers import executors
from real_portainer_fixture import isolated_portainer, wait_native_stack, OLD_IMAGE, NEW_IMAGE

pytestmark = pytest.mark.skipif(
    os.environ.get("RT_RUN_REAL_PORTAINER_TESTS") != "1",
    reason="Real privileged Portainer acceptance is explicitly opt-in",
)


@pytest.mark.timeout(420)
async def test_real_portainer_grouped_deploy_and_immutable_recovery():
    async with isolated_portainer() as fixture:
        api = fixture.api
        password = secrets.token_urlsafe(32)
        response = await api.post(
            "/api/users/admin/init", json={"Username": "rt-admin", "Password": password}
        )
        response.raise_for_status()
        user_id = response.json()["Id"]
        response = await api.post("/api/auth", json={"Username": "rt-admin", "Password": password})
        response.raise_for_status()
        api.headers["Authorization"] = "Bearer " + response.json()["jwt"]
        response = await api.post(
            f"/api/users/{user_id}/tokens",
            json={"Description": "owned-fixture", "Password": password},
        )
        response.raise_for_status()
        api_key = response.json().get("rawAPIKey") or response.json().get("RawAPIKey")
        assert api_key
        response = await api.post(
            "/api/endpoints",
            files={
                "Name": (None, "rt-owned-dind"),
                "EndpointCreationType": (None, "1"),
                "URL": (None, "unix:///var/run/docker.sock"),
            },
        )
        response.raise_for_status()
        endpoint_id = response.json()["Id"]
        original = {"api": OLD_IMAGE, "worker": NEW_IMAGE}
        compose = "services:\n" + "".join(
            f'  {service}:\n    image: {image}\n    command: ["sleep", "infinity"]\n    stop_grace_period: 1s\n'
            for service, image in original.items()
        )
        response = await api.post(
            "/api/stacks/create/standalone/string",
            params={"endpointId": endpoint_id},
            json={
                "Name": "rt-owned-stack",
                "StackFileContent": compose,
                "Env": [],
            },
        )
        response.raise_for_status()
        stack_id = response.json()["Id"]
        runtime = RuntimeConnectionConfig(
            id=1,
            name="synthetic-portainer",
            type="portainer",
            credential_id=1,
            config={"base_url": fixture.base_url, "endpoint_id": endpoint_id},
            secrets={"api_key": api_key},
        )
        credential = Credential(
            id=1, name="synthetic-portainer", type="portainer_runtime", secrets={"api_key": api_key}
        )
        adapter = PortainerRuntimeAdapter(runtime)
        try:
            endpoints = await adapter.discover_endpoints()
            assert [entry.id for entry in endpoints] == [endpoint_id]
            target = next(t for t in await adapter.discover_targets() if t.name == "rt-owned-stack")
            ref = target.target_ref
            await adapter.validate_target_ref(ref)
            await wait_native_stack(adapter, ref, original)
            assert await adapter.fetch_stack_service_images(ref) == original
            stored_ref = deepcopy(ref)
            for service in stored_ref["services"]:
                service["image"] = "sentinel:stale"
            executor = ExecutorConfig(
                id=1,
                name="owned-stack",
                runtime_type="portainer",
                runtime_connection_id=1,
                tracker_name="synthetic-source",
                target_ref=stored_ref,
                service_bindings=[
                    ExecutorServiceBinding(service=name, tracker_source_id=1, channel_name="stable")
                    for name in original
                ],
            )

            class TestStorage:
                async def get_executor_config(self, key):
                    return executor

                async def get_runtime_connection(self, key):
                    return runtime

                async def get_credential(self, key):
                    return credential

                async def get_credential_by_id(self, key):
                    return credential

            stored_ref = deepcopy(executor.target_ref)
            detail = await executors.get_executor_config_detail(1, TestStorage())
            assert detail["current_images"] == original
            assert detail["current_image"] is None
            assert detail["target_ref"] == stored_ref
            assert executor.target_ref == stored_ref
            snapshot = await adapter.capture_snapshot(ref, "")
            await adapter.validate_snapshot(ref, snapshot)
            evidence = snapshot["recovery_evidence"]["services"]
            swapped = {"api": NEW_IMAGE, "worker": OLD_IMAGE}
            changed = await adapter.update_stack_services(ref, swapped)
            assert changed.updated_services == ["api", "worker"]
            await wait_native_stack(
                adapter,
                ref,
                swapped,
                {
                    "api": evidence["worker"]["image_id"],
                    "worker": evidence["api"]["image_id"],
                },
            )
            assert await adapter.fetch_stack_service_images(ref) == swapped
            recovered = await adapter.recover_from_snapshot(ref, snapshot)
            assert recovered.updated
            await wait_native_stack(
                adapter,
                ref,
                original,
                {name: value["image_id"] for name, value in evidence.items()},
            )
            assert (
                await adapter.fetch_stack_file(endpoint_id=endpoint_id, stack_id=stack_id)
                == snapshot["stack_file"]
            )
            assert await adapter.fetch_stack_service_images(ref) == original
        finally:
            await adapter.close()
