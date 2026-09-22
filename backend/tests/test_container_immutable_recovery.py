"""Immutable restore must not resolve mutable tags or accept start() as readiness."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from releasetracker.executors import container_recovery
from test_container_recovery_boundaries import runtime as runtime, snapshot, IMAGE
from test_executor_adapters import FakeContainer, FakeImage, RECOVERY_IMAGE_ID

OTHER_ID = "sha256:" + "b" * 64


@pytest.fixture(autouse=True)
def fast_observation(monkeypatch):
    monkeypatch.setattr(container_recovery, "VERIFY_INTERVAL", 0)
    monkeypatch.setattr(container_recovery, "VERIFY_TIMEOUT", 1)


@pytest.mark.asyncio
async def test_capture_uses_inspect_identity_not_republished_alias(runtime):
    adapter, client = runtime
    current = FakeContainer(
        "original-id",
        "service-a",
        FakeImage(tags=[IMAGE], image_id=OTHER_ID),
        attrs={
            "Image": RECOVERY_IMAGE_ID,
            "Config": {"Image": IMAGE},
            "HostConfig": {},
        },
    )
    client.containers._containers.append(current)
    saved = await adapter.capture_snapshot({"container_name": "service-a"}, IMAGE)
    assert saved["recovery_evidence"]["image_id"] == RECOVERY_IMAGE_ID
    assert saved["create_config"]["image"] == IMAGE


@pytest.mark.asyncio
async def test_restore_uses_only_local_id_and_never_reuses_same_tag(runtime):
    adapter, client = runtime
    current = FakeContainer(
        "replacement-id",
        "service-a",
        FakeImage(tags=[IMAGE], image_id=OTHER_ID),
        attrs={"Config": {}, "HostConfig": {}},
    )
    client.containers._containers.append(current)
    current._on_remove = lambda: client.containers._containers.remove(current)
    saved = snapshot(adapter)
    original = deepcopy(saved)
    client.images.get = Mock(return_value=FakeImage(image_id=RECOVERY_IMAGE_ID))
    result = await adapter.recover_from_snapshot({"container_name": "service-a"}, saved)
    assert result.updated
    assert result.new_image == IMAGE
    client.images.get.assert_called_with(RECOVERY_IMAGE_ID)
    assert client.images.pull_calls == []
    assert len(current.remove_calls) == 1
    assert client.containers.create_calls[-1]["image"] == RECOVERY_IMAGE_ID
    assert saved == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["missing_evidence", "invalid_id", "unobserved_config", "missing_local", "wrong_local"]
)
async def test_artifact_failure_blocks_before_container_mutation(runtime, fault):
    adapter, client = runtime
    current = FakeContainer("original-id", "service-a", FakeImage(tags=[IMAGE]))
    client.containers._containers.append(current)
    saved = snapshot(adapter)
    if fault == "missing_evidence":
        saved.pop("recovery_evidence")
    elif fault == "invalid_id":
        saved["recovery_evidence"]["image_id"] = IMAGE
    elif fault == "unobserved_config":
        saved["recovery_evidence"]["config_observed"] = False
    elif fault == "missing_local":
        client.images.get = Mock(side_effect=RuntimeError("image was pruned"))
    else:
        client.images.get = Mock(return_value=FakeImage(image_id=OTHER_ID))
    with pytest.raises((ValueError, RuntimeError)):
        await adapter.recover_from_snapshot({"container_name": "service-a"}, saved)
    assert current.remove_calls == current.stop_calls == []
    assert client.containers.create_calls == client.images.pull_calls == []


@pytest.mark.asyncio
async def test_group_preflights_all_local_images_before_first_mutation(runtime):
    adapter, client = runtime
    first, second = snapshot(adapter), snapshot(adapter)
    second["container_name"] = second["create_config"]["name"] = "service-b"
    second["recovery_evidence"]["image_id"] = OTHER_ID

    def local_image(image_id):
        if image_id == OTHER_ID:
            raise RuntimeError("image was pruned")
        return FakeImage(image_id=image_id)

    client.images.get = local_image
    group = {
        "mode": "docker_compose",
        "runtime_type": adapter.runtime_connection.type,
        "project": "example-project",
        "image": IMAGE,
        "snapshots": [first, second],
    }
    with pytest.raises(RuntimeError, match="pruned"):
        await adapter.recover_from_snapshot(
            {"mode": "docker_compose", "project": "example-project"}, group
        )
    assert client.containers.create_calls == client.images.pull_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "image",
        "name",
        "id",
        "config",
        "stopped",
        "paused",
        "restarting",
        "dead",
        "unhealthy",
        "missing_health",
        "inspect_error",
    ],
)
async def test_readiness_failure_is_not_success_and_keeps_created_container(
    runtime, monkeypatch, fault
):
    adapter, client = runtime
    saved = snapshot(adapter)
    healthcheck = {"Test": ["CMD", "true"]}
    saved["recovery_evidence"]["healthcheck"] = healthcheck
    saved["create_config"]["healthcheck"] = healthcheck
    get = client.containers.get
    monkeypatch.setattr(container_recovery, "VERIFY_TIMEOUT", 0)

    def inspect(identifier):
        current = get(identifier)
        if not client.containers._created or identifier != current.id:
            return current
        current.attrs["State"]["Health"] = {"Status": "healthy"}
        if fault == "image":
            current.attrs["Image"] = OTHER_ID
        elif fault == "name":
            current.name = "unrelated"
        elif fault == "id":
            current.id = "unrelated-id"
        elif fault == "config":
            current.attrs["Config"]["Healthcheck"] = None
        elif fault == "stopped":
            current.attrs["State"]["Running"] = False
        elif fault in ("paused", "restarting", "dead"):
            current.attrs["State"][fault.capitalize()] = True
        elif fault == "unhealthy":
            current.attrs["State"]["Health"]["Status"] = "unhealthy"
        elif fault == "missing_health":
            current.attrs["State"].pop("Health")
        else:
            raise TimeoutError("inspect unavailable")
        return current

    client.containers.get = inspect
    with pytest.raises((RuntimeError, TimeoutError)):
        await adapter.recover_from_snapshot({"container_name": "service-a"}, saved)
    created = client.containers._created[-1]
    assert created.remove_calls == []
    assert created in client.containers._containers
    assert client.images.pull_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("native_health", [False, True])
async def test_requires_two_stable_ready_samples_and_resets_after_restart(runtime, native_health):
    adapter, client = runtime
    saved = snapshot(adapter)
    if native_health:
        healthcheck = {"Test": ["CMD", "true"]}
        saved["recovery_evidence"]["healthcheck"] = healthcheck
        saved["create_config"]["healthcheck"] = healthcheck
    get = client.containers.get
    samples = []

    def inspect(identifier):
        current = get(identifier)
        if client.containers._created and identifier == current.id:
            samples.append(identifier)
            current.attrs["RestartCount"] = 0 if len(samples) == 1 else 1
            current.attrs["State"]["StartedAt"] = "2026-01-01T00:00:00Z"
            if native_health:
                current.attrs["State"]["Health"] = {
                    "Status": "starting" if len(samples) == 1 else "healthy"
                }
        return current

    client.containers.get = inspect
    result = await adapter.recover_from_snapshot({"container_name": "service-a"}, saved)
    assert result.updated
    assert len(samples) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "healthcheck", ["invalid", {"Test": "CMD true"}, {"Test": ["UNKNOWN"]}, {"Test": ["CMD", 1]}]
)
async def test_invalid_healthcheck_evidence_blocks_before_write(runtime, healthcheck):
    adapter, client = runtime
    saved = snapshot(adapter)
    saved["recovery_evidence"]["healthcheck"] = healthcheck
    saved["create_config"]["healthcheck"] = healthcheck
    with pytest.raises(ValueError, match="healthcheck"):
        await adapter.recover_from_snapshot({"container_name": "service-a"}, saved)
    assert client.containers.create_calls == client.images.pull_calls == []


@pytest.mark.asyncio
async def test_final_group_observation_cannot_pass_when_one_service_stops(runtime, monkeypatch):
    adapter, client = runtime
    first, second = snapshot(adapter), snapshot(adapter)
    second["container_name"] = second["create_config"]["name"] = "service-b"
    for identifier, saved in [("restored-a", first), ("restored-b", second)]:
        client.containers._containers.append(
            FakeContainer(
                identifier,
                saved["container_name"],
                FakeImage(image_id=RECOVERY_IMAGE_ID),
                attrs={"Config": {}, "State": {"Running": identifier == "restored-a"}},
            )
        )
    monkeypatch.setattr(container_recovery, "VERIFY_TIMEOUT", 0)
    with pytest.raises(RuntimeError, match="readiness"):
        await container_recovery.verify_group(
            adapter, ["restored-a", "restored-b"], [first, second]
        )
    assert all(not c.remove_calls for c in client.containers._containers)


@pytest.mark.asyncio
async def test_duplicate_or_incomplete_restored_identities_cannot_pass(runtime):
    adapter, _ = runtime
    saved = snapshot(adapter)
    for identifiers in ([], ["one"], ["one", "one"]):
        with pytest.raises(RuntimeError, match="identities"):
            await container_recovery.verify_group(adapter, identifiers, [saved, saved])
