"""Persistent target lineage tests use fictional Podman targets only."""

from copy import deepcopy

import pytest

from releasetracker.services.deployment_plan import MARKER_KEYS
from releasetracker.services.podman_target_lineage import (
    normalize_members,
    snapshot_binding,
    target_fingerprint,
    validate_snapshot_binding,
)
from releasetracker.storage.sqlite_podman_lineage import LineageConflict

pytestmark = pytest.mark.asyncio


def markers(target="fictional-target"):
    return dict(zip(MARKER_KEYS, ("fictional-installation", target, "1")))


def member(identifier="0123456789abcdef", name="service-a", **values):
    return {
        "container_id": identifier,
        "name": name,
        "project": values.pop("project", None),
        "service": values.pop("service", None),
        "replica": values.pop("replica", None),
        "markers": markers(values.pop("target", "fictional-target")),
    } | values


async def establish(storage, executor_id=7, members=None):
    return await storage.podman_lineage.establish(
        executor_id=executor_id,
        target_id="fictional-target",
        mode="container",
        target_fingerprint=target_fingerprint("container", {"container_name": "service-a"}),
        members=[member()] if members is None else members,
        now=10,
    )


async def test_establish_is_idempotent_but_never_rebinds_existing_target(storage):
    initial = await establish(storage)
    assert initial["generation"] == 1
    assert await establish(storage) == initial
    with pytest.raises(LineageConflict, match="different members"):
        await establish(storage, members=[member("fedcba9876543210")])
    with pytest.raises(LineageConflict, match="another executor"):
        await establish(storage, executor_id=8)


async def test_transition_is_compare_and_swap_and_records_owner(storage):
    initial = await establish(storage)
    replacement = [member("fedcba9876543210")]
    current = await storage.podman_lineage.transition(
        executor_id=7,
        expected_generation=1,
        expected_members=initial["members"],
        new_members=replacement,
        task_id=42,
        now=20,
    )
    assert current["generation"] == 2
    assert current["members"] == normalize_members(replacement)
    db = await storage._get_connection()
    row = await (await db.execute("SELECT * FROM podman_target_lineage_transitions")).fetchone()
    assert row["task_id"] == 42 and row["executor_run_id"] is None
    with pytest.raises(LineageConflict, match="changed"):
        await storage.podman_lineage.transition(
            executor_id=7,
            expected_generation=1,
            expected_members=initial["members"],
            new_members=[member("1111111111111111")],
            task_id=43,
        )


@pytest.mark.parametrize("task_id,executor_run_id", [(None, None), (1, 2)])
async def test_transition_has_exactly_one_durable_owner(storage, task_id, executor_run_id):
    initial = await establish(storage)
    with pytest.raises(ValueError, match="exactly one"):
        await storage.podman_lineage.transition(
            executor_id=7,
            expected_generation=1,
            expected_members=initial["members"],
            new_members=[member("fedcba9876543210")],
            task_id=task_id,
            executor_run_id=executor_run_id,
        )


async def test_snapshot_binding_accepts_recorded_history_but_not_future_or_other_target(storage):
    initial = await establish(storage)
    snapshot = {"podman_target_lineage": snapshot_binding(initial)}
    current = await storage.podman_lineage.transition(
        executor_id=7,
        expected_generation=1,
        expected_members=initial["members"],
        new_members=[member("fedcba9876543210")],
        task_id=42,
    )
    assert validate_snapshot_binding(snapshot, current)["generation"] == 1
    recorded_current, recorded = await storage.podman_lineage.validate_snapshot(7, snapshot)
    assert recorded_current["generation"] == 2 and recorded["generation"] == 1
    forged = deepcopy(snapshot)
    forged["podman_target_lineage"]["members"][0]["container_id"] = "1111111111111111"
    with pytest.raises(LineageConflict, match="members were not recorded"):
        await storage.podman_lineage.validate_snapshot(7, forged)
    with pytest.raises(ValueError, match="generation"):
        validate_snapshot_binding(snapshot, current, allow_historical=False)
    foreign = deepcopy(snapshot)
    foreign["podman_target_lineage"]["target_id"] = "other-target"
    with pytest.raises(ValueError, match="different"):
        validate_snapshot_binding(foreign, current)
    future = deepcopy(snapshot)
    future["podman_target_lineage"]["generation"] = 99
    with pytest.raises(ValueError, match="generation"):
        validate_snapshot_binding(future, current)


@pytest.mark.parametrize(
    "bad",
    [
        [],
        [member("short")],
        [member(), member()],
        [member(), member("fedcba9876543210", name="service-a")],
        [member(target="wrong") | {"markers": {MARKER_KEYS[0]: "only-owner"}}],
    ],
)
async def test_invalid_or_ambiguous_members_never_persist(storage, bad):
    with pytest.raises(ValueError):
        await establish(storage, members=bad)
    assert await storage.podman_lineage.get(7) is None


async def test_target_fingerprint_excludes_mutable_container_ids_and_normalizes_services():
    first = target_fingerprint(
        "docker_compose", {"project": "example-project", "services": ["api", "web", "api"]}
    )
    second = target_fingerprint(
        "docker_compose", {"project": "example-project", "services": ["web", "api"]}
    )
    assert first == second
    assert first != target_fingerprint(
        "docker_compose", {"project": "other-project", "services": ["web", "api"]}
    )
