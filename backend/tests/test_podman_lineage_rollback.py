"""Podman rollback binds both the historical snapshot and current target chain."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from releasetracker.services.deployment_plan import MARKER_KEYS
from releasetracker.services.podman_target_lineage import snapshot_binding, target_fingerprint
from releasetracker.services.rollback_service import RollbackService
from releasetracker.storage.sqlite_podman_lineage import LineageConflict

pytestmark = pytest.mark.asyncio
MARKERS = dict(zip(MARKER_KEYS, ("fictional-installation", "fictional-target", "1")))


def member(identifier="0123456789abcdef", marker_state="managed"):
    return [
        {
            "container_id": identifier,
            "name": "service-a",
            "project": None,
            "service": None,
            "replica": None,
            "markers": MARKERS,
            "marker_state": marker_state,
        }
    ]


class Adapter:
    def __init__(self, members):
        self.members = members
        self.error = None

    async def get_target_lineage_members(self, target_ref, planned):
        del target_ref
        assert planned == MARKERS
        if self.error:
            raise self.error
        return deepcopy(self.members)


async def setup(storage):
    lineage = await storage.podman_lineage.establish(
        executor_id=7,
        target_id="fictional-target",
        mode="container",
        target_fingerprint=target_fingerprint("container", {"container_name": "service-a"}),
        members=member(),
    )
    config = SimpleNamespace(
        id=7,
        runtime_type="podman",
        target_ref={"mode": "container", "container_id": "0123456789abcdef"},
    )
    return lineage, config


async def test_rollback_requires_snapshot_and_live_target_in_same_recorded_chain(storage):
    lineage, config = await setup(storage)
    snapshot = {"podman_target_lineage": snapshot_binding(lineage)}
    service = RollbackService(storage, SimpleNamespace())
    adapter = Adapter(member())
    assert (
        await service._validate_podman_lineage_snapshot(config, adapter, snapshot, verify_live=True)
        == lineage
    )
    adapter.members = member("fedcba9876543210")
    with pytest.raises(ValueError, match="no longer matches"):
        await service._validate_podman_lineage_snapshot(config, adapter, snapshot, verify_live=True)
    adapter.error = LookupError("target missing")
    with pytest.raises(LookupError, match="target missing"):
        await service._validate_podman_lineage_snapshot(config, adapter, snapshot, verify_live=True)


async def test_rollback_rejects_unbound_old_snapshot_and_other_target(storage):
    lineage, config = await setup(storage)
    service = RollbackService(storage, SimpleNamespace())
    adapter = Adapter(member())
    with pytest.raises(ValueError, match="lacks verified"):
        await service._validate_podman_lineage_snapshot(config, adapter, {}, verify_live=False)
    snapshot = {"podman_target_lineage": snapshot_binding(lineage)}
    snapshot["podman_target_lineage"]["target_id"] = "other-target"
    with pytest.raises(ValueError, match="different"):
        await service._validate_podman_lineage_snapshot(
            config, adapter, snapshot, verify_live=False
        )


async def test_unmanaged_historical_generation_remains_explicit(storage):
    initial, config = await setup(storage)
    initial_members = member(marker_state="approved_unmanaged")
    # Build a separate executor because an established lineage cannot be rebound.
    historic = await storage.podman_lineage.establish(
        executor_id=8,
        target_id="fictional-unmanaged-target",
        mode="container",
        target_fingerprint=target_fingerprint("container", {"container_name": "service-a"}),
        members=initial_members,
    )
    snapshot = {"podman_target_lineage": snapshot_binding(historic)}
    await storage.podman_lineage.transition(
        executor_id=8,
        expected_generation=1,
        expected_members=initial_members,
        new_members=member("fedcba9876543210"),
        executor_run_id=50,
    )
    current, binding = await storage.podman_lineage.validate_snapshot(8, snapshot)
    assert current["generation"] == 2
    assert binding["members"][0]["marker_state"] == "approved_unmanaged"
