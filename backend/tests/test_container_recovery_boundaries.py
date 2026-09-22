"""Read failures and deployment context must not change snapshot recovery semantics."""

from copy import deepcopy
from unittest.mock import Mock

import httpx
import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.docker import DockerRuntimeAdapter
from releasetracker.executors.podman import PodmanRuntimeAdapter
from releasetracker.services.deployment_plan import MANAGED_MARKERS
from test_executor_adapters import (
    FakeContainer,
    RECOVERY_IMAGE_ID,
    recovery_evidence,
    docker_engine_identity,
    FakeContainerNotFound,
    FakeImage,
    FakeDockerRecreateClient,
    FakePodmanClient,
)

IMAGE = "registry.example.test/team/service-a:1.0.0"
MARKERS = {"releasetracker.io/managed-by": "new-installation"}


@pytest.fixture(params=["docker", "podman"])
def runtime(request):
    kind = request.param
    client = (
        FakeDockerRecreateClient([])
        if kind == "docker"
        else FakePodmanClient([], raw_api_available=False)
    )
    cls = DockerRuntimeAdapter if kind == "docker" else PodmanRuntimeAdapter
    adapter = cls(
        RuntimeConnectionConfig(
            name="example-runtime", type=kind, config={"socket": "unix:///example.sock"}
        ),
        client=client,
    )
    return adapter, client


def snapshot(adapter):
    return {
        "runtime_type": adapter.runtime_connection.type,
        **(
            {"engine_identity": docker_engine_identity()}
            if adapter.runtime_connection.type == "docker"
            else {}
        ),
        "recovery_evidence": recovery_evidence(),
        "container_id": "original-id",
        "container_name": "service-a",
        "image": IMAGE,
        "create_config": {"image": IMAGE, "name": "service-a", "labels": {"owner": "example"}},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "labels", [{"owner": "example"}, {"releasetracker.io/managed-by": "historical-installation"}]
)
async def test_restore_preserves_snapshot_labels_and_forward_deploy_still_injects(runtime, labels):
    adapter, client = runtime
    saved = snapshot(adapter)
    saved["create_config"]["labels"] = labels
    original = deepcopy(saved)
    token = MANAGED_MARKERS.set(MARKERS)
    try:
        await adapter.recover_from_snapshot({"container_name": "service-a"}, saved)
        assert client.containers.create_calls[-1]["labels"] == labels
        assert saved == original
        assert MANAGED_MARKERS.get() == MARKERS
        await adapter.update_image(
            {"container_name": "service-a"}, "registry.example.test/team/service-a:2.0.0"
        )
        assert (
            client.containers.create_calls[-1]["labels"]["releasetracker.io/managed-by"]
            == "new-installation"
        )
    finally:
        MANAGED_MARKERS.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("offline"),
        PermissionError("denied"),
        RuntimeError("server failure"),
        KeyError("invalid response"),
    ],
)
async def test_inspect_error_is_not_absence_and_cannot_begin_recovery(runtime, error):
    adapter, client = runtime
    client.containers.get = Mock(side_effect=error)
    token = MANAGED_MARKERS.set(MARKERS)
    try:
        with pytest.raises(type(error)):
            await adapter.recover_from_snapshot({"container_name": "service-a"}, snapshot(adapter))
        assert MANAGED_MARKERS.get() == MARKERS
    finally:
        MANAGED_MARKERS.reset(token)
    assert client.images.pull_calls == []
    assert client.containers.create_calls == []


@pytest.mark.asyncio
async def test_explicit_404_allows_absent_container_restore(runtime):
    adapter, client = runtime
    original_get = client.containers.get
    error = httpx.HTTPStatusError(
        "not found",
        request=httpx.Request("GET", "https://runtime.example.test/containers/service-a"),
        response=httpx.Response(404),
    )

    def get(identifier):
        if not client.containers._containers:
            raise error
        return original_get(identifier)

    client.containers.get = get
    result = await adapter.recover_from_snapshot({"container_name": "service-a"}, snapshot(adapter))
    assert result.updated
    assert len(client.containers.create_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["runtime", "target_name", "create_name", "target_id"])
async def test_wrong_snapshot_target_is_rejected_before_side_effects(runtime, mismatch):
    adapter, client = runtime
    saved = snapshot(adapter)
    target = {"container_name": "service-a"}
    if mismatch == "runtime":
        saved["runtime_type"] = (
            "podman" if adapter.runtime_connection.type == "docker" else "docker"
        )
    elif mismatch == "target_name":
        target["container_name"] = "unrelated"
    elif mismatch == "create_name":
        saved["create_config"]["name"] = "unrelated"
    else:
        target = {"container_id": "unrelated-id"}
        client.containers.get = Mock(
            return_value=FakeContainer("unrelated-id", "unrelated", FakeImage(tags=[IMAGE]))
        )
    with pytest.raises(ValueError, match="snapshot"):
        await adapter.recover_from_snapshot(target, saved)
    assert client.images.pull_calls == []
    assert client.containers.create_calls == []


@pytest.mark.asyncio
async def test_recreated_target_id_is_valid_only_with_matching_inspected_name(runtime):
    adapter, client = runtime
    current = FakeContainer("replacement-id", "service-a", FakeImage(tags=[IMAGE]))
    client.containers.get = Mock(return_value=current)
    await adapter.validate_snapshot({"container_id": "replacement-id"}, snapshot(adapter))
    client.containers.get.assert_called_with("replacement-id")


@pytest.mark.asyncio
async def test_target_identity_is_rechecked_after_artifact_preparation(runtime):
    adapter, client = runtime
    current = FakeContainer("replacement-id", "service-a", FakeImage(tags=[IMAGE]))
    client.containers.get = Mock(return_value=current)

    def drift(_):
        current.name = "unrelated"
        return FakeImage(image_id=RECOVERY_IMAGE_ID)

    client.images.get = drift
    with pytest.raises(ValueError, match="identity"):
        await adapter.recover_from_snapshot({"container_id": "replacement-id"}, snapshot(adapter))
    assert current.stop_calls == []
    assert current.remove_calls == []
    assert client.containers.create_calls == []


@pytest.mark.asyncio
async def test_late_inspect_failure_does_not_trigger_conflict_cleanup(runtime):
    adapter, client = runtime
    client.containers.get = Mock(
        side_effect=[FakeContainerNotFound("absent"), TimeoutError("offline")]
    )
    with pytest.raises(TimeoutError):
        await adapter.recover_from_snapshot({"container_name": "service-a"}, snapshot(adapter))
    assert client.containers.create_calls == []


@pytest.mark.parametrize(
    "error", [TimeoutError("unknown"), PermissionError("denied"), TypeError("SDK decode failure")]
)
def test_uncertain_remove_is_never_replayed(runtime, error):
    adapter, _ = runtime
    current = FakeContainer(
        "replacement-id", "service-a", FakeImage(tags=[IMAGE]), attrs={"State": {"Running": True}}
    )
    current.remove = Mock(side_effect=error)
    with pytest.raises(type(error)):
        adapter._remove_container_if_present(current)
    current.remove.assert_called_once_with(force=True)
    assert current.stop_calls == []


@pytest.mark.parametrize("stop_fails", [False, True])
def test_legacy_remove_signature_is_checked_before_any_destructive_call(runtime, stop_fails):
    adapter, _ = runtime
    current = FakeContainer(
        "replacement-id", "service-a", FakeImage(tags=[IMAGE]), attrs={"State": {"Running": True}}
    )
    removed = []

    def remove():
        removed.append(True)

    current.remove = remove
    if stop_fails:
        current.stop = Mock(side_effect=TimeoutError("stop unknown"))
        with pytest.raises(TimeoutError):
            adapter._remove_container_if_present(current)
        assert removed == []
    else:
        adapter._remove_container_if_present(current)
        assert current.stop_calls == [True]
        assert removed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["runtime", "entry_runtime", "entry_name"])
async def test_group_validates_all_snapshot_identities_before_any_service_write(runtime, mismatch):
    adapter, client = runtime
    first, last = snapshot(adapter), snapshot(adapter)
    group = {
        "mode": "docker_compose",
        "project": "example-project",
        "runtime_type": adapter.runtime_connection.type,
        "image": IMAGE,
        "snapshots": [first, last],
    }
    if mismatch == "runtime":
        group["runtime_type"] = "unrelated"
    elif mismatch == "entry_runtime":
        last["runtime_type"] = "unrelated"
    else:
        last["create_config"]["name"] = "unrelated"
    with pytest.raises(ValueError, match="snapshot"):
        await adapter.recover_from_snapshot(
            {"mode": "docker_compose", "project": "example-project"}, group
        )
    assert client.images.pull_calls == []
    assert client.containers.create_calls == []
