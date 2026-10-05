"""Portainer/Podman identity never trusts the random Docker compatibility ID."""

from copy import deepcopy

import httpx
import pytest

from releasetracker.executors import portainer_recovery
from releasetracker.executors.portainer import PortainerResourceNotFoundError
from test_portainer_recovery_rollout_races import EVIDENCE, IMAGE, PREFIX, REF, adapter, attrs


class Engine:
    def __init__(self):
        self.calls = []
        self.reads = 0
        self.info = {
            "BuildahVersion": "1.39",
            "Rootless": False,
            "Name": "isolated-podman",
            "DockerRootDir": "/var/lib/containers/storage",
            "Driver": "overlay",
            "OSType": "linux",
            "Architecture": "amd64",
        }
        self.endpoint = {
            "Id": 1,
            "Type": 1,
            "URL": "tcp://isolated-engine:2375",
            "TLSConfig": {"TLS": False, "TLSSkipVerify": False},
        }
        self.version = {"Components": [{"Name": "Podman Engine", "Version": "5.4.2"}]}
        self.failures = {}
        self.row = attrs()
        self.row.update(HostConfig={}, Mounts=[], NetworkSettings={"Networks": {}})
        self.stack_file = f"services:\n  api:\n    image: {IMAGE}\n"
        self.on_info = None

    def respond(self, request):
        assert request.method == "GET", "inspection must never write or pull images"
        path = request.url.path
        self.calls.append(path)
        if path in self.failures:
            return httpx.Response(self.failures[path], json={"message": "synthetic failure"})
        if path == PREFIX + "/info":
            self.reads += 1
            if self.on_info:
                self.on_info(self)
            return httpx.Response(200, json={**self.info, "ID": f"random-{self.reads}"})
        if path == PREFIX + "/version":
            return httpx.Response(200, json=self.version)
        if path == "/api/endpoints/1":
            return httpx.Response(200, json=self.endpoint)
        if path == PREFIX + "/containers/json":
            return httpx.Response(200, json=[{"Id": self.row["Id"]}])
        if path == PREFIX + "/containers/replacement/json":
            return httpx.Response(200, json=self.row)
        if path.startswith(PREFIX + "/images/"):
            return httpx.Response(200, json={"Id": self.row["Image"]})
        if path == "/api/stacks/1/file":
            return httpx.Response(200, json={"StackFileContent": self.stack_file})
        if path == "/api/stacks/1":
            return httpx.Response(
                200,
                json={"Id": 1, "Name": REF["stack_name"], "EndpointId": 1, "Type": 2, "Env": []},
            )
        raise AssertionError(f"unexpected request: {path}")

    def client(self):
        return httpx.AsyncClient(
            base_url="http://portainer.test", transport=httpx.MockTransport(self.respond)
        )


async def test_rotating_id_has_stable_capture_preflight_and_readiness():
    engine = Engine()
    async with engine.client() as client:
        runtime = adapter(client)
        saved = await portainer_recovery.capture(runtime, REF, engine.stack_file)
        second = await portainer_recovery.capture(runtime, REF, engine.stack_file)
        assert saved == second and saved["engine_id"].startswith("podman-compat-v1:")
        snapshot = {"stack_file": engine.stack_file, "recovery_evidence": saved}
        assert portainer_recovery.validate(snapshot) == saved
        await portainer_recovery.preflight(runtime, REF, saved)
        assert (await portainer_recovery.probe(runtime, REF, saved))[0] is True
        assert engine.reads == 6
        assert not any("libpod" in path for path in engine.calls)


@pytest.mark.parametrize(
    "field,value",
    [
        ("Name", "foreign-host"),
        ("DockerRootDir", "/foreign/store"),
        ("Driver", "vfs"),
        ("Rootless", True),
        ("OSType", "foreign-os"),
        ("Architecture", "arm64"),
    ],
)
async def test_host_or_storage_drift_blocks_recovery(field, value):
    engine = Engine()
    async with engine.client() as client:
        runtime = adapter(client)
        saved = await portainer_recovery.capture(runtime, REF, engine.stack_file)
        engine.info[field] = value
        with pytest.raises(ValueError, match="engine identity changed"):
            await portainer_recovery.preflight(runtime, REF, saved)
        with pytest.raises(ValueError, match="engine identity changed after restore"):
            await portainer_recovery.probe(runtime, REF, saved)


@pytest.mark.parametrize(
    "field,value",
    [
        ("URL", "tcp://foreign-engine:2375"),
        ("Type", 2),
        ("EdgeID", "foreign-edge"),
        ("TLSConfig", {"TLS": True, "TLSSkipVerify": False}),
    ],
)
async def test_endpoint_retargeting_is_not_hidden_by_same_container_and_image(field, value):
    engine = Engine()
    async with engine.client() as client:
        runtime = adapter(client)
        saved = await portainer_recovery.capture(runtime, REF, engine.stack_file)
        engine.endpoint[field] = value
        with pytest.raises(ValueError, match="engine identity changed"):
            await portainer_recovery.preflight(runtime, REF, saved)


async def test_identity_is_reread_and_drift_during_capture_is_refused():
    engine = Engine()
    engine.on_info = lambda e: e.info.update(Name="first" if e.reads == 1 else "second")
    async with engine.client() as client:
        with pytest.raises(ValueError, match="engine changed during snapshot capture"):
            await portainer_recovery.capture(adapter(client), REF, engine.stack_file)


@pytest.mark.parametrize(
    "field", ["Name", "DockerRootDir", "Driver", "Rootless", "OSType", "Architecture"]
)
async def test_missing_host_storage_fields_never_fall_back_to_random_id(field):
    engine = Engine()
    engine.info.pop(field)
    async with engine.client() as client:
        with pytest.raises(ValueError, match="Podman.*identity unavailable"):
            await portainer_recovery.engine_id(adapter(client), REF)


@pytest.mark.parametrize(
    "field,value",
    [
        ("Rootless", "false"),
        ("DockerRootDir", "/"),
        ("DockerRootDir", "relative/store"),
        ("DockerRootDir", "/data/../foreign"),
        ("Name", "host\nspoof"),
    ],
)
async def test_malformed_host_identity_is_rejected(field, value):
    engine = Engine()
    engine.info[field] = value
    async with engine.client() as client:
        with pytest.raises(ValueError, match="Podman.*identity unavailable"):
            await portainer_recovery.engine_id(adapter(client), REF)


@pytest.mark.parametrize(
    "change",
    [
        "missing_components",
        "docker_component",
        "wrong_endpoint",
        "boolean_endpoint",
        "missing_url",
        "missing_tls",
    ],
)
async def test_podman_and_authenticated_endpoint_must_be_explicitly_verified(change):
    engine = Engine()
    if change == "missing_components":
        engine.version = {}
    elif change == "docker_component":
        engine.version = {"Components": [{"Name": "Engine", "Version": "28"}]}
    elif change == "wrong_endpoint":
        engine.endpoint["Id"] = 2
    elif change == "boolean_endpoint":
        engine.endpoint["Id"] = True
    elif change == "missing_url":
        engine.endpoint.pop("URL")
    else:
        engine.endpoint.pop("TLSConfig")
    async with engine.client() as client:
        with pytest.raises(ValueError, match="Podman"):
            await portainer_recovery.engine_id(adapter(client), REF)


@pytest.mark.parametrize("path", [PREFIX + "/version", "/api/endpoints/1"])
@pytest.mark.parametrize("status", [401, 403, 404, 500])
async def test_identity_api_failures_never_downgrade_or_retry(path, status):
    engine = Engine()
    engine.failures[path] = status
    async with engine.client() as client:
        with pytest.raises(PortainerResourceNotFoundError if status == 404 else RuntimeError):
            await portainer_recovery.engine_id(adapter(client), REF)
    assert engine.calls.count(path) == 1


async def test_existing_docker_snapshot_identity_is_preserved_without_extra_requests():
    calls = []

    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"ID": "original-daemon"})

    async with httpx.AsyncClient(
        base_url="http://portainer.test", transport=httpx.MockTransport(respond)
    ) as client:
        assert await portainer_recovery.engine_id(adapter(client), REF) == "original-daemon"
    assert calls == [PREFIX + "/info"]


async def test_old_podman_random_uuid_is_not_silently_rebound():
    engine = Engine()
    async with engine.client() as client:
        with pytest.raises(ValueError, match="engine identity changed"):
            await portainer_recovery.preflight(adapter(client), REF, deepcopy(EVIDENCE))


async def test_image_alias_and_project_guards_remain_strict():
    engine = Engine()
    async with engine.client() as client:
        runtime = adapter(client)
        saved = await portainer_recovery.capture(runtime, REF, engine.stack_file)
        engine.row["Image"] = "sha256:" + "b" * 64
        with pytest.raises(ValueError, match="image alias changed"):
            await portainer_recovery.preflight(runtime, REF, saved)
        assert await portainer_recovery.probe(runtime, REF, saved) == (False, ())
        engine.row["Config"]["Labels"]["com.docker.compose.project"] = "foreign-project"
        with pytest.raises(ValueError, match="project identity mismatch"):
            await portainer_recovery.probe(runtime, REF, saved)


@pytest.mark.parametrize("drift", ["endpoint", "storage", "none"])
async def test_real_admission_and_before_write_checks_bind_the_compatibility_identity(
    storage, drift
):
    from releasetracker.config import ExecutorServiceBinding
    from releasetracker.executors.portainer import PortainerRuntimeAdapter
    from releasetracker.services.deployment_diff import (
        EXPECTED_UPDATE_STATE,
        INSPECTED_UPDATE_STATE,
        verify_update_state,
    )
    from releasetracker.services.runtime_credentials import (
        materialize_runtime_connection_credentials,
    )
    from releasetracker.storage.sqlite_deployment_admission import AdmissionConflict
    from helpers.executor_runtime import create_portainer_runtime_connection
    from test_deployment_configuration_diff import setup

    executor, scheduler, handler, _, _, original_task = await setup(storage)
    engine = Engine()
    try:
        await storage.tasks.finish(original_task, "superseded")
        connection_id = await create_portainer_runtime_connection(storage)
        connection = await storage.get_runtime_connection(connection_id)
        connection_id = await storage.create_runtime_connection(
            connection.model_copy(
                update={
                    "id": None,
                    "name": "isolated-portainer-podman",
                    "config": {"base_url": "http://portainer.test", "endpoint_id": 1},
                }
            )
        )
        executor = executor.model_copy(
            update={
                "runtime_type": "portainer",
                "runtime_connection_id": connection_id,
                "target_ref": {
                    "mode": "portainer_stack",
                    "endpoint_id": 1,
                    "stack_id": 1,
                    "stack_name": REF["stack_name"],
                    "stack_type": "standalone",
                },
                "service_bindings": [
                    ExecutorServiceBinding(
                        service="api",
                        tracker_name=executor.tracker_name,
                        tracker_source_id=executor.tracker_source_id,
                        channel_name=executor.channel_name,
                    )
                ],
            }
        )
        await storage.update_executor_config(executor.id, executor)
        executor = await storage.get_executor_config(executor.id)
        connection = await materialize_runtime_connection_credentials(
            storage, await storage.get_runtime_connection(connection_id)
        )
        async with engine.client() as client:
            runtime = PortainerRuntimeAdapter(connection, client=client)
            scheduler._adapters[executor.id] = runtime
            receipt = await handler.enqueue(executor.id, manual=True)
            task = await storage.tasks.claim("deploy")
            assert task["id"] == receipt["task_id"]
            evidence = await handler._collect_admission_evidence(executor, task)
            inspected_state = INSPECTED_UPDATE_STATE.get()
            repeated = await handler._collect_admission_evidence(executor, task)
            assert evidence.evidence_hash == repeated.evidence_hash
            assert evidence.configuration_diff["lines"]
            plan = await handler.admission.stage(task, evidence)
            assert plan["state"] == "pending"
            await handler.admission.approve(task["id"], plan["id"], plan["fingerprint"], "admin")
            task = await storage.tasks.claim("deploy")
            await storage.tasks.start_attempt(task)
            if drift == "endpoint":
                engine.endpoint["URL"] = "tcp://different-engine:2375"
            elif drift == "storage":
                engine.info["DockerRootDir"] = "/different/storage"
            latest = await handler._collect_admission_evidence(executor, task)
            token = EXPECTED_UPDATE_STATE.set(inspected_state)
            try:
                if drift == "none":
                    await handler.admission.verify_before_write(task, latest)
                    assert await verify_update_state(runtime, executor.target_ref) is not None
                else:
                    assert latest.evidence_hash != evidence.evidence_hash
                    with pytest.raises(AdmissionConflict, match="deployment_plan_changed"):
                        await handler.admission.verify_before_write(task, latest)
                    with pytest.raises(ValueError, match="deployment_configuration_changed"):
                        await verify_update_state(runtime, executor.target_ref)
            finally:
                EXPECTED_UPDATE_STATE.reset(token)
    finally:
        await scheduler.shutdown()
