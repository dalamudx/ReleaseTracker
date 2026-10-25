"""Legacy identities may be reviewed, but never silently authorize a new target."""

import json
from dataclasses import replace
from datetime import datetime

from releasetracker.models import ExecutorRunHistory

import pytest

from releasetracker.services.deployment_plan import TargetEvidence, fingerprint, managed_markers
from releasetracker.storage.sqlite_deployment_admission import (
    AdmissionConflict,
    DeploymentAdmissionStore,
)
from test_deployment_admission import _seed_executor, task

pytestmark = pytest.mark.asyncio


async def legacy(storage):
    await _seed_executor(storage)
    store = DeploymentAdmissionStore(storage)
    old_ref = {
        "mode": "helm_release",
        "namespace": "apps",
        "release_name": "demo",
        "chart_name": "demo",
        "chart_version": "1.0.0",
    }
    original = await task(storage)
    payload = original["payload"] | {"targets": [{"chart_version": "1.1.0"}]}
    async with storage.tasks.transaction() as db:
        await db.execute(
            "UPDATE tasks SET payload=? WHERE id=?",
            (json.dumps(payload, sort_keys=True), original["id"]),
        )
    claimed = await storage.tasks.claim("deploy")
    before = TargetEvidence(
        "cluster-a",
        fingerprint({"mode": "helm_release", "target": old_ref}),
        {"mode": "helm_release", "target": old_ref, "current_chart_version": "1.0.0"},
        ({},),
        "helm_revision",
        configuration_hash="before",
        desired_configuration_hash="after",
    )
    plan = await store.stage(claimed, before)
    await store.approve(original["id"], plan["id"], plan["fingerprint"], "admin")
    run_id = await storage.create_executor_run(
        ExecutorRunHistory(
            executor_id=1, started_at=datetime.now(), finished_at=datetime.now(), status="failed"
        )
    )
    async with storage.tasks.transaction() as db:
        await db.execute("UPDATE deployment_plans SET state='applied' WHERE id=?", (plan["id"],))
        await db.execute(
            "UPDATE managed_targets SET baseline=?,last_task_id=? WHERE executor_id=1",
            (before.evidence_hash, original["id"]),
        )
        await db.execute(
            "UPDATE tasks SET state='failed',error_code='operator_reconciled',result=? WHERE id=?",
            (
                json.dumps(
                    {
                        "mutation_started": True,
                        "mutation_complete": True,
                        "manual_resolution": {"actor": "admin"},
                    }
                ),
                original["id"],
            ),
        )
        await db.execute(
            "INSERT INTO deployment_observations(task_id,run_id,executor_id,resource_scope,executor_config,verification,finalization,started_at,deadline,due_at,state) VALUES (?,?,1,'*',?,'{}','{}',0,60,0,'blocked')",
            (original["id"], run_id, json.dumps({"target_ref": old_ref})),
        )
    target = {k: v for k, v in old_ref.items() if k != "chart_version"}
    markers = managed_markers(await store.installation_id(), plan["target_id"], original["id"])
    current = replace(
        before,
        target_identity=fingerprint({"mode": "helm_release", "target": target}),
        configuration={"mode": "helm_release", "target": target, "current_chart_version": "1.1.0"},
        markers=(markers,),
        configuration_diff={
            "lines": [{"operation": "+", "path": "/chart_version", "value": "1.2.0"}],
            "truncated": False,
        },
    )
    await task(storage)
    claimed = await storage.tasks.claim("deploy")
    return store, original, before, claimed, current


async def test_legacy_transition_requires_approval_and_preserves_baseline(storage):
    store, original, before, claimed, current = await legacy(storage)
    plan = await store.stage(claimed, current)
    assert (plan["state"], plan["reason"]) == ("pending", "helm_identity_review_required")
    db = await storage._get_connection()
    prior = dict(
        await (await db.execute("SELECT * FROM managed_targets WHERE executor_id=1")).fetchone()
    )
    assert prior["identity_key"] == before.identity_key
    with pytest.raises(AdmissionConflict):
        await store.verify_before_write(await storage.tasks.get(claimed["id"]), current)
    await store.approve(claimed["id"], plan["id"], plan["fingerprint"], "admin")
    adopted = dict(
        await (await db.execute("SELECT * FROM managed_targets WHERE executor_id=1")).fetchone()
    )
    assert adopted["identity_key"] == current.identity_key
    assert adopted["baseline"] == prior["baseline"]
    assert adopted["last_task_id"] == original["id"]
    resumed = await storage.tasks.claim("deploy")
    refreshed = await store.stage(resumed, current)
    assert refreshed["id"] == plan["id"] and refreshed["state"] == "approved"
    assert refreshed["approved_by"] == "admin"
    await store.verify_before_write(resumed, current)
    with pytest.raises(AdmissionConflict, match="deployment_plan_changed"):
        await store.verify_before_write(
            resumed, replace(current, configuration=current.configuration | {"env": "changed"})
        )


@pytest.mark.parametrize(
    "change",
    [
        "namespace",
        "release_name",
        "chart_name",
        "runtime",
        "marker",
        "missing_marker",
        "unfinished",
        "unapproved",
        "newer_write",
        "version_unavailable",
        "old_identity",
    ],
)
async def test_incomplete_or_changed_ownership_stays_blocked(storage, change):
    store, original, before, claimed, current = await legacy(storage)
    async with storage.tasks.transaction() as db:
        if change in {"namespace", "release_name", "chart_name"}:
            ref = current.configuration["target"] | {change: "other"}
            current = replace(
                current,
                configuration=current.configuration | {"target": ref},
                target_identity=fingerprint({"mode": "helm_release", "target": ref}),
            )
        elif change == "runtime":
            current = replace(current, runtime_identity="cluster-b")
        elif change == "marker":
            current = replace(
                current,
                markers=(current.markers[0] | {"releasetracker.io/managed-by": "another-owner"},),
            )
        elif change == "missing_marker":
            current = replace(current, markers=({},))
        elif change == "unfinished":
            await db.execute(
                "UPDATE tasks SET result=json_remove(result,'$.mutation_complete') WHERE id=?",
                (original["id"],),
            )
        elif change == "unapproved":
            await db.execute(
                "UPDATE deployment_plans SET approved_at=NULL WHERE task_id=?", (original["id"],)
            )
        elif change == "newer_write":
            await db.execute(
                "UPDATE tasks SET result=? WHERE id=?",
                (json.dumps({"mutation_started": True}), claimed["id"]),
            )
        elif change == "version_unavailable":
            current = replace(
                current, configuration=current.configuration | {"current_chart_version": None}
            )
        else:
            await db.execute(
                "UPDATE managed_targets SET identity_key='unrelated' WHERE executor_id=1"
            )
    if change == "newer_write":
        # A new started deployment is separate from the unstarted plan under review.
        async with storage.tasks.transaction() as db:
            await db.execute(
                "UPDATE tasks SET state='failed',owner=NULL WHERE id=?", (claimed["id"],)
            )
        await task(storage)
        claimed = await storage.tasks.claim("deploy")
    plan = await store.stage(claimed, current)
    assert plan["state"] == "blocked"
    assert "helm_identity_review" not in plan["summary"]


@pytest.mark.parametrize("concurrent", ["baseline", "write"])
async def test_concurrent_baseline_change_invalidates_identity_approval(storage, concurrent):
    store, _, _, claimed, current = await legacy(storage)
    plan = await store.stage(claimed, current)
    async with storage.tasks.transaction() as db:
        if concurrent == "baseline":
            await db.execute(
                "UPDATE managed_targets SET baseline='newer-baseline' WHERE executor_id=1"
            )
        else:
            await db.execute(
                "UPDATE tasks SET result=? WHERE id=?",
                (json.dumps({"mutation_started": True}), claimed["id"]),
            )
    with pytest.raises(AdmissionConflict, match="managed_target_changed"):
        await store.approve(claimed["id"], plan["id"], plan["fingerprint"], "admin")


async def test_expired_review_can_be_renewed_after_identity_conversion(storage):
    store, _, _, claimed, current = await legacy(storage)
    plan = await store.stage(claimed, current, now=1000)
    await store.approve(claimed["id"], plan["id"], plan["fingerprint"], "admin", now=1001)
    resumed = await storage.tasks.claim("deploy")
    expired = await store.stage(resumed, current, now=4000)
    assert expired["state"] == "pending"
    await store.approve(claimed["id"], expired["id"], expired["fingerprint"], "admin", now=4001)
    resumed = await storage.tasks.claim("deploy")
    await store.verify_before_write(resumed, current, now=4002)


async def test_missing_observation_or_runtime_routing_change_remains_blocked(storage):
    store, original, _, claimed, current = await legacy(storage)
    async with storage.tasks.transaction() as db:
        await db.execute("DELETE FROM deployment_observations WHERE task_id=?", (original["id"],))
    plan = await store.stage(claimed, current)
    assert plan["state"] == "blocked"


async def test_outside_chart_upgrade_needs_review_with_visible_drift(storage):
    store, _, _, claimed, current = await legacy(storage)
    upgraded = replace(
        current, configuration=current.configuration | {"current_chart_version": "1.2.0"}
    )
    plan = await store.stage(claimed, upgraded)
    assert (plan["state"], plan["reason"]) == ("pending", "helm_identity_review_required")
    assert plan["summary"]["helm_identity_review"]["external_chart_version"] == {
        "approved": "1.1.0",
        "current": "1.2.0",
    }


async def test_helm_admission_reads_runtime_with_receipts(storage, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from helpers.executor_runtime import create_runtime_connection
    from releasetracker.config import ExecutorConfig
    from releasetracker.executor_scheduler import ExecutorScheduler
    from releasetracker.services import deployment_diff
    from releasetracker.services.deploy_tasks import DeployTasks

    connection = await create_runtime_connection(storage)
    ref = {
        "mode": "helm_release",
        "namespace": "apps",
        "release_name": "demo",
        "chart_name": "demo",
        "chart_version": "1.0.0",
        "chart_digest": "a" * 64,
        "chart_revision": 3,
    }
    executor = ExecutorConfig(
        name="isolated",
        runtime_type="docker",
        runtime_connection_id=connection,
        tracker_name="test",
        target_ref={"mode": "container", "container_name": "isolated"},
    ).model_copy(update={"id": 123, "target_ref": ref})
    adapter = SimpleNamespace(
        validate_target_ref=AsyncMock(),
        get_managed_markers=AsyncMock(return_value=({},)),
        get_helm_release_version=AsyncMock(return_value="1.0.0"),
        get_helm_release_digest=AsyncMock(return_value="sha256:" + "a" * 64),
    )
    monkeypatch.setattr(deployment_diff, "native_review", AsyncMock(return_value=("state", None)))
    scheduler = ExecutorScheduler(storage)
    scheduler._adapters[123] = adapter
    try:
        evidence = await DeployTasks(storage, scheduler)._collect_admission_evidence(executor)
    finally:
        await scheduler.shutdown()
    assert adapter.get_helm_release_version.await_args.args[0] == ref
    assert adapter.get_helm_release_digest.await_args.args[0] == ref
    assert "chart_version" not in evidence.configuration["target"]
    assert evidence.configuration["current_chart_digest"] == "sha256:" + "a" * 64
