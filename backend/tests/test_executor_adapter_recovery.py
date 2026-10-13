"""Snapshot recreation scenarios through the public adapter recovery API."""

import logging

import pytest

from helpers.container_runtime import (
    RECOVERY_IMAGE_ID,
    FakeContainer,
    FakeDockerRecreateClient,
    FakeImage,
    FakePodmanClient,
    docker_engine_identity,
    recovery_evidence,
)
from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.docker import DockerRuntimeAdapter
from releasetracker.executors.podman import PodmanRuntimeAdapter

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fast_recovery_polling")]


@pytest.mark.parametrize(
    ("container_id", "image", "state"),
    [
        pytest.param("docker-new-id", "api:2.0", None, id="partial-replacement"),
        pytest.param("old-id", "api:1.0", None, id="original-stopped"),
        pytest.param(
            "old-id", "api:1.0", {"Status": "running", "Running": True}, id="original-running"
        ),
    ],
)
async def test_docker_recovery_recreates_container(container_id, image, state):
    runtime = RuntimeConnectionConfig(
        name="docker-prod",
        type="docker",
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={},
    )
    attrs = {"Config": {}, "HostConfig": {}}
    if state is not None:
        attrs["State"] = state
    container = FakeContainer(container_id, "api", FakeImage(tags=[image]), attrs=attrs)
    client = FakeDockerRecreateClient([container])
    adapter = DockerRuntimeAdapter(runtime, client=client)

    result = await adapter.recover_from_snapshot(
        {"container_name": "api"},
        {
            "runtime_type": "docker",
            "engine_identity": docker_engine_identity(),
            "recovery_evidence": recovery_evidence(),
            "container_id": "old-id",
            "container_name": "api",
            "image": "api:1.0",
            "create_config": {"image": "api:1.0", "name": "api"},
        },
    )

    assert result.updated is True
    assert result.new_image == "api:1.0"
    assert result.new_container_id == "docker-new-id"
    assert container.start_calls == []
    assert len(container.remove_calls) == 1
    assert client.containers.create_calls == [
        {"image": RECOVERY_IMAGE_ID, "name": "api", "use_config_proxy": False}
    ]


@pytest.mark.parametrize(
    "working_dir", [pytest.param("   ", id="blank"), pytest.param("/srv/app", id="nonblank")]
)
async def test_podman_recovery_working_dir(working_dir, caplog):
    runtime = RuntimeConnectionConfig(
        name="podman-prod",
        type="podman",
        config={"socket": "unix:///run/podman/podman.sock"},
        secrets={},
    )
    client = FakePodmanClient([])
    adapter = PodmanRuntimeAdapter(runtime, client=client)
    with caplog.at_level(logging.INFO, logger="releasetracker.executors.podman"):
        result = await adapter.recover_from_snapshot(
            {"container_name": "sample-cache"},
            {
                "runtime_type": "podman",
                "recovery_evidence": recovery_evidence(),
                "container_id": "def",
                "container_name": "sample-cache",
                "image": "sample-cache:7.2",
                "create_config": {
                    "image": "sample-cache:7.2",
                    "name": "sample-cache",
                    "working_dir": working_dir,
                },
                "pod_id": None,
            },
        )

    assert result.updated is True
    assert len(client.api.post_calls) == 1
    create_payload = client.api.post_calls[0]["data"]
    create_kwargs = client.containers.create_calls[0]
    for payload in (create_payload, create_kwargs):
        assert "working_dir" not in payload
        if working_dir.strip():
            assert payload["work_dir"] == working_dir
        else:
            assert "work_dir" not in payload
    if working_dir.strip():
        assert working_dir not in caplog.text
