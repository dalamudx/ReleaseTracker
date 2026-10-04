"""Deleted owners need verified local history and fresh approval, never a marker bypass."""

from dataclasses import replace

import pytest

from releasetracker.services.deployment_plan import managed_markers
from releasetracker.storage.sqlite_deployment_admission import AdmissionConflict
from test_deployment_admission import staged, task

pytestmark = pytest.mark.asyncio


async def previous_owner(storage, *, delete=True, mutation_started=True):
    store, queued, observed, plan = await staged(storage)
    await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    running = await storage.tasks.claim("deploy")
    await storage.tasks.start_attempt(running)
    markers = managed_markers(await store.installation_id(), plan["target_id"], running["id"])
    observed = replace(observed, markers=(markers,))
    if mutation_started:
        await store.mark_applied(running, observed)
    await storage.tasks.finish(running, "succeeded", result={"mutation_started": mutation_started})
    if delete:
        assert await storage.delete_executor_config(queued["payload"]["executor_id"])
    return store, observed, plan


async def stage_replacement(storage, store, observed):
    from helpers.executor_runtime import create_runtime_connection
    from releasetracker.config import ExecutorConfig

    runtime_id = await create_runtime_connection(storage, name="replacement-runtime")
    executor_id = await storage.create_executor_config(
        ExecutorConfig(
            name="replacement-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name="test-tracker",
            target_ref={"mode": "container", "container_name": "app"},
        )
    )
    assert executor_id == 2
    queued = await task(storage, executor_id=executor_id)
    claimed = await storage.tasks.claim("deploy")
    return queued, await store.stage(claimed, observed)


@pytest.mark.parametrize("with_deployment_id", [False, True])
async def test_deleted_local_owner_needs_fresh_approval_and_can_be_reclaimed(
    storage, with_deployment_id
):
    store, observed, previous = await previous_owner(storage)
    if not with_deployment_id:
        marker = dict(observed.markers[0])
        marker.pop("releasetracker.io/deployment-id")
        observed = replace(observed, markers=(marker,))
    queued, plan = await stage_replacement(storage, store, observed)
    assert plan["state"] == "pending" and plan["reason"] == "unmanaged"
    assert plan["target_id"] != previous["target_id"]
    assert (await storage.tasks.get(queued["id"]))["approval_pending"] == 1
    assert await storage.tasks.claim("deploy") is None

    await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    running = await storage.tasks.claim("deploy")
    await storage.tasks.start_attempt(running)
    assert (await store.verify_before_write(running, observed))["id"] == plan["id"]
    # Only the subsequent approved write replaces remote markers; planning never does.
    assert observed.markers[0]["releasetracker.io/target-id"] == previous["target_id"]
    owned = replace(
        observed,
        markers=(managed_markers(await store.installation_id(), plan["target_id"], running["id"]),),
    )
    await store.mark_applied(running, owned)
    await storage.tasks.finish(running, "succeeded", result={"mutation_started": True})
    next_task = await task(storage, executor_id=2)
    next_plan = await store.stage(await storage.tasks.claim("deploy"), owned)
    assert next_plan["state"] == "approved" and next_plan["reason"] == "managed"
    assert next_plan["task_id"] == next_task["id"]


@pytest.mark.parametrize(
    "change,reason",
    [
        ("active_owner", "target_marker_conflict"),
        ("unknown_target", "target_marker_conflict"),
        ("foreign_owner", "foreign_owner"),
        ("unsupported_schema", "marker_schema_unsupported"),
        ("wrong_target_identity", "target_marker_conflict"),
        ("wrong_runtime_identity", "target_marker_conflict"),
        ("no_write", "target_marker_conflict"),
        ("no_approval", "target_marker_conflict"),
        ("wrong_deployment_id", "target_marker_conflict"),
        ("missing_owner", "target_marker_conflict"),
        ("missing_schema", "target_marker_conflict"),
        ("mixed_foreign", "foreign_owner"),
        ("mixed_schema", "marker_schema_unsupported"),
        ("mixed_unknown", "target_marker_conflict"),
    ],
)
async def test_stale_markers_without_matching_local_write_evidence_stay_blocked(
    storage, change, reason
):
    store, observed, previous = await previous_owner(
        storage, delete=change != "active_owner", mutation_started=change != "no_write"
    )
    marker = dict(observed.markers[0])
    if change == "active_owner":
        # Even a missing DB claim must not allow takeover from a still-active executor.
        db = await storage._get_connection()
        await db.execute("DELETE FROM managed_targets")
        await db.commit()
    elif change == "unknown_target":
        marker["releasetracker.io/target-id"] = "copied-target"
    elif change == "foreign_owner":
        marker["releasetracker.io/managed-by"] = "another-installation"
    elif change == "unsupported_schema":
        marker["releasetracker.io/schema"] = "999"
    elif change == "wrong_target_identity":
        observed = replace(observed, target_identity={"uid": "replacement-target"})
    elif change == "wrong_runtime_identity":
        observed = replace(observed, runtime_identity={"cluster": "another-cluster"})
    elif change == "no_approval":
        db = await storage._get_connection()
        await db.execute(
            "UPDATE deployment_plans SET approved_at=NULL WHERE id=?", (previous["id"],)
        )
        await db.commit()
    elif change == "wrong_deployment_id":
        marker["releasetracker.io/deployment-id"] = "999999"
    elif change == "missing_owner":
        marker.pop("releasetracker.io/managed-by")
    elif change == "missing_schema":
        marker.pop("releasetracker.io/schema")
    observed = replace(observed, markers=(marker,))
    if change.startswith("mixed_"):
        other = dict(marker)
        if change == "mixed_foreign":
            other["releasetracker.io/managed-by"] = "another-installation"
        elif change == "mixed_schema":
            other["releasetracker.io/schema"] = "999"
        else:
            other["releasetracker.io/target-id"] = "unknown-target"
        observed = replace(observed, markers=(marker, other))
    queued, plan = await stage_replacement(storage, store, observed)
    assert plan["state"] == "blocked" and plan["reason"] == reason
    with pytest.raises(AdmissionConflict, match="blocked"):
        await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    assert await storage.tasks.claim("deploy") is None


async def test_reclaimed_plan_rejects_configuration_drift_before_write(storage):
    store, observed, _ = await previous_owner(storage)
    queued, plan = await stage_replacement(storage, store, observed)
    await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    running = await storage.tasks.claim("deploy")
    await storage.tasks.start_attempt(running)
    with pytest.raises(AdmissionConflict, match="deployment_plan_changed"):
        await store.verify_before_write(
            running, replace(observed, configuration={"image": "changed"})
        )
