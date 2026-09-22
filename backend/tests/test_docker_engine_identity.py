"""An endpoint/name/image match is not proof of the original Docker daemon."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors import container_recovery
from releasetracker.executors.base import RuntimeMutationError
from releasetracker.executors.docker import DockerRuntimeAdapter
from test_container_recovery_boundaries import snapshot, IMAGE
from test_executor_adapters import (
    FakeDockerRecreateClient,
    FakeContainer,
    FakeImage,
    DOCKER_ENGINE_ID,
    docker_engine_identity,
)

OTHER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


@pytest.fixture
def runtime(monkeypatch):
    existing = FakeContainer(
        "original-id",
        "service-a",
        FakeImage(tags=[IMAGE]),
        attrs={
            "Config": {
                "Image": IMAGE,
                "Labels": {
                    "com.docker.compose.project": "example-project",
                    "com.docker.compose.service": "service-a",
                },
            },
            "HostConfig": {},
        },
    )
    client = FakeDockerRecreateClient([existing], network_names=["example-net"])
    state = {"id": DOCKER_ENGINE_ID}
    client.info = Mock(side_effect=lambda: {"ID": state["id"], "Name": "example-host"})
    adapter = DockerRuntimeAdapter(
        RuntimeConnectionConfig(
            name="example", type="docker", config={"socket": "unix:///example.sock"}
        ),
        client=client,
    )
    monkeypatch.setattr(container_recovery, "VERIFY_INTERVAL", 0)
    monkeypatch.setattr(container_recovery, "VERIFY_TIMEOUT", 0.1)
    return SimpleNamespace(
        adapter=adapter,
        client=client,
        existing=existing,
        state=state,
        saved=snapshot(adapter),
        target={"container_name": "service-a"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [False, True])
async def test_capture_binds_all_members_and_rechecks_after_inspection(runtime, group):
    x = runtime
    target = {"mode": "docker_compose", "project": "example-project"} if group else x.target
    saved = await x.adapter.capture_snapshot(target, IMAGE)
    assert saved["engine_identity"] == docker_engine_identity()
    assert x.client.info.call_count == 2
    if group:
        assert all(s["engine_identity"] == saved["engine_identity"] for s in saved["snapshots"])
    x.client.info.side_effect = [{"ID": DOCKER_ENGINE_ID}, {"ID": OTHER}]
    with pytest.raises(ValueError, match="identity changed"):
        await x.adapter.capture_snapshot(target, IMAGE)
    assert x.existing.remove_calls == x.client.containers.create_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", [None, {}, {"ID": ""}, {"ID": 1}, {"Name": "example-host"}, {"ID": " has whitespace "}]
)
async def test_unavailable_identity_blocks_capture_and_restore(runtime, value):
    x = runtime
    x.client.info.return_value = value
    x.client.info.side_effect = None
    with pytest.raises(ValueError, match="identity could not be verified"):
        await x.adapter.capture_snapshot(x.target, IMAGE)
    with pytest.raises(ValueError, match="identity could not be verified"):
        await x.adapter.recover_from_snapshot(x.target, x.saved)
    assert x.existing.remove_calls == x.client.containers.create_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["foreign", "missing", "kind", "schema", "offline"])
async def test_foreign_or_unbound_snapshot_never_deletes_same_named_container(runtime, fault):
    x = runtime
    if fault == "foreign":
        x.state["id"] = OTHER
    elif fault == "missing":
        x.saved.pop("engine_identity")
    elif fault == "kind":
        x.saved["engine_identity"]["kind"] = "podman"
    elif fault == "schema":
        x.saved["engine_identity"]["schema"] = True
    else:
        x.client.info.side_effect = RuntimeError("credential=private-fixture")
    with pytest.raises(ValueError) as error:
        await x.adapter.recover_from_snapshot(x.target, x.saved)
    assert "private-fixture" not in str(error.value)
    assert x.existing.remove_calls == x.client.containers.create_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["image_lookup", "conflict_lookup"])
async def test_prewrite_drift_during_readonly_preparation_does_not_mutate(runtime, stage):
    x = runtime
    if stage == "image_lookup":
        get = x.client.images.get

        def image(image_id):
            x.state["id"] = OTHER
            return get(image_id)

        x.client.images.get = image
    else:
        get = x.client.containers.get
        calls = []

        def container(identifier):
            calls.append(identifier)
            if len(calls) == 2:
                x.state["id"] = OTHER
            return get(identifier)

        x.client.containers.get = container
    with pytest.raises(ValueError, match="identity changed"):
        await x.adapter.recover_from_snapshot(x.target, x.saved)
    assert x.existing.remove_calls == x.client.containers.create_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    ["remove", "create", "network_lookup", "network_connect", "start", "observe", "cleanup"],
)
async def test_postwrite_drift_stops_without_foreign_cleanup_or_success(runtime, stage):
    x = runtime
    remove = x.existing.remove
    if stage == "remove":

        def changed_remove(**kwargs):
            remove(**kwargs)
            x.state["id"] = OTHER

        x.existing.remove = changed_remove
    create = x.client.containers.create

    def changed_create(**kwargs):
        result = create(**kwargs)
        if stage == "create":
            x.state["id"] = OTHER
        if stage in {"start", "cleanup"}:
            start = result.start

            def changed_start():
                start()
                x.state["id"] = OTHER
                if stage == "cleanup":
                    raise RuntimeError("simulated uncertain start")

            result.start = changed_start
        return result

    x.client.containers.create = changed_create
    if stage.startswith("network"):
        x.saved["network_config"] = {"network_mode": "bridge", "endpoints": {"example-net": {}}}
        get_network = x.client.networks.get

        def network(name):
            obj = get_network(name)
            if stage == "network_lookup":
                x.state["id"] = OTHER
            else:
                connect = obj.connect

                def changed_connect(*args, **kwargs):
                    connect(*args, **kwargs)
                    x.state["id"] = OTHER

                obj.connect = changed_connect
            return obj

        x.client.networks.get = network
    if stage == "observe":
        get = x.client.containers.get

        def inspect(identifier):
            result = get(identifier)
            if x.client.containers._created and identifier == result.id:
                x.state["id"] = OTHER
            return result

        x.client.containers.get = inspect
    with pytest.raises(RuntimeMutationError, match="identity changed") as error:
        await x.adapter.recover_from_snapshot(x.target, x.saved)
    assert error.value.destructive_started
    assert len(x.existing.remove_calls) == 1
    if stage == "remove":
        assert x.client.containers.create_calls == []
    else:
        assert len(x.client.containers._created) == 1
        created = x.client.containers._created[0]
        assert created.remove_calls == []
        if stage in {"create", "network_lookup", "network_connect"}:
            assert created.start_calls == []
    if stage == "network_lookup":
        assert x.client.networks.connect_calls == x.client.networks.disconnect_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["member", "root", "missing_member"])
async def test_group_identity_preflight_covers_every_member(runtime, fault):
    x = runtime
    first, second = deepcopy(x.saved), deepcopy(x.saved)
    second["container_name"] = second["create_config"]["name"] = "service-b"
    group = {
        "mode": "docker_compose",
        "project": "example-project",
        "runtime_type": "docker",
        "image": IMAGE,
        "engine_identity": docker_engine_identity(),
        "snapshots": [first, second],
    }
    if fault == "member":
        second["engine_identity"]["daemon_id"] = OTHER
    elif fault == "root":
        group["engine_identity"]["daemon_id"] = OTHER
    else:
        second.pop("engine_identity")
    with pytest.raises(ValueError, match="daemon"):
        await x.adapter.recover_from_snapshot(
            {"mode": "docker_compose", "project": "example-project"}, group
        )
    assert x.existing.remove_calls == x.client.containers.create_calls == []


@pytest.mark.asyncio
async def test_same_engine_different_connection_name_is_not_drift(runtime):
    x = runtime
    original = deepcopy(x.saved)
    x.adapter.runtime_connection.name = "renamed-connection"
    x.client.info.side_effect = lambda: {"ID": DOCKER_ENGINE_ID, "Name": "renamed-host"}
    result = await x.adapter.recover_from_snapshot(x.target, x.saved)
    assert result.updated
    assert x.saved == original
