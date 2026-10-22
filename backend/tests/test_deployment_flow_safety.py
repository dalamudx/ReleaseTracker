"""Deployment acceptance must survive mutable tags, incomplete evidence and restarts."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.services.deploy_tasks import DeployTasks, _already_current
from releasetracker.services.deployment_diff import native_review
from releasetracker.services.deployment_plan import managed_markers
from releasetracker.services.deployment_readiness_probes import _probe_containers
from releasetracker.services.deployment_version_evidence import targets_verified
from releasetracker.storage.sqlite_deployment_admission import AdmissionConflict
from test_deployment_admission import staged, evidence, managed_target
from test_deployment_readiness_queue import setup, verified_evidence

DIGEST = "sha256:" + "a" * 64
IMAGE = "example/service:stable"


@pytest.mark.parametrize("bad", ["configuration", "artifact", "missing_proof", "legacy_plan"])
async def test_mark_applied_requires_approved_configuration_and_artifact(storage, bad):
    store, queued, observed, plan = await staged(storage)
    await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    claimed = await storage.tasks.claim("deploy")
    await storage.tasks.start_attempt(claimed)
    await storage.tasks.checkpoint(claimed, {"mutation_started": True})
    if bad == "configuration":
        observed = replace(observed, configuration_hash="unapproved-environment")
    elif bad == "artifact":
        observed = replace(
            observed,
            version_evidence=tuple(p | {"same_artifact": False} for p in observed.version_evidence),
        )
    elif bad == "missing_proof":
        observed = replace(observed, version_evidence=())
    else:
        db = await storage._get_connection()
        await db.execute(
            "UPDATE deployment_plans SET summary=json_remove(summary,'$.desired_configuration_hash') WHERE id=?",
            (plan["id"],),
        )
        await db.commit()
    with pytest.raises(AdmissionConflict, match="deployment_result_unverified"):
        await store.mark_applied(claimed, observed)
    assert (await managed_target(storage))["baseline"] is None


@pytest.mark.parametrize("bad", ["configuration", "artifact"])
async def test_completed_readiness_does_not_adopt_unapproved_changes(storage, bad):
    store, queued, observed, plan = await staged(storage)
    await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    claimed = await storage.tasks.claim("deploy")
    await storage.tasks.start_attempt(claimed)
    await store.mark_applied(claimed, observed)
    await storage.tasks.finish(claimed, "succeeded", result={"mutation_started": True})
    live = replace(
        observed,
        markers=(managed_markers(await store.installation_id(), plan["target_id"], claimed["id"]),),
        configuration=observed.configuration | {"runtime_configuration": "changed"},
    )
    if bad == "configuration":
        live = replace(live, configuration_hash="unapproved-env")
    else:
        live = replace(
            live,
            version_evidence=tuple(p | {"same_artifact": False} for p in live.version_evidence),
            pre_rollout_hash=observed.evidence_hash,
        )
    assert not await store.refresh_applied(claimed["id"], live)
    assert (await managed_target(storage))["baseline"] == observed.evidence_hash


@pytest.mark.parametrize("mode", ["docker_compose", "portainer_stack", "kubernetes_workload"])
@pytest.mark.parametrize("proven", [False, True])
def test_same_tag_is_not_a_noop_without_matching_frozen_digest(mode, proven):
    task = {
        "payload": {
            "targets": [{"source_id": 1, "channel": "stable", "target": ["stable", DIGEST]}],
            "policy_bindings": [
                {"tracker_source_id": 1, "channel_name": "stable", "service": "app"}
            ],
        }
    }
    proof = {
        "source_id": 1,
        "channel_name": "stable",
        "service": "app",
        "target": ["stable", DIGEST],
        "same_artifact": proven,
    }
    live = evidence(
        recovery="compose_config",
        configuration_diff={"lines": [], "truncated": False},
        version_evidence=(proof,),
    )
    assert _already_current(SimpleNamespace(target_ref={"mode": mode}), live, task) is proven


def test_target_proof_does_not_cross_alias_bindings():
    task = {
        "payload": {
            "targets": [
                {
                    "source_id": 1,
                    "channel": "stable",
                    "deploy_alias": "blue",
                    "target": ["stable", DIGEST],
                }
            ],
            "policy_bindings": [
                {
                    "tracker_source_id": 1,
                    "channel_name": "stable",
                    "deploy_alias": "blue",
                    "service": "a",
                },
                {
                    "tracker_source_id": 1,
                    "channel_name": "stable",
                    "deploy_alias": "green",
                    "service": "b",
                },
            ],
        }
    }
    proof = {
        "source_id": 1,
        "channel_name": "stable",
        "service": "a",
        "target": ["stable", DIGEST],
        "same_artifact": True,
    }
    assert targets_verified(task, evidence(recovery="compose_config", version_evidence=(proof,)))
    assert not targets_verified(
        task, evidence(recovery="compose_config", version_evidence=(proof | {"service": "b"},))
    )


def container(identifier, markers):
    return {
        "id": identifier,
        "image": IMAGE,
        "image_id": DIGEST,
        "state": {"Running": True},
        "restart_count": 0,
        "markers": markers,
    }


@pytest.mark.parametrize(
    "bad", [None, "foreign_deployment", "wrong_image", "missing_proof", "extra_replica"]
)
def test_portainer_updated_services_require_ownership_and_immutable_image(bad):
    markers = managed_markers("installation", "target", 42)
    before = {"kind": "portainer_stack", "containers": {"app": [container("old", {})]}}
    current = {"kind": "portainer_stack", "containers": {"app": [container("new", markers)]}}
    verification = {
        "baseline": before,
        "target": deepcopy(before),
        "services": [{"service": "app", "status": "success"}],
    }
    immutable = {"app": DIGEST}
    if bad == "foreign_deployment":
        current["containers"]["app"][0]["markers"] = managed_markers("installation", "target", 99)
    elif bad == "wrong_image":
        current["containers"]["app"][0]["image_id"] = "sha256:" + "b" * 64
    elif bad == "missing_proof":
        immutable = {}
    elif bad == "extra_replica":
        current["containers"]["app"].append(container("extra", markers))
    result = _probe_containers(
        current,
        verification,
        {"app": IMAGE},
        deployment_markers=markers,
        immutable_images=immutable,
    )
    if bad:
        assert result["outcome"] != "healthy"
        assert "verification_update" not in result
        return
    verification.update(json.loads(json.dumps(result["verification_update"])))
    assert (
        _probe_containers(
            current,
            verification,
            {"app": IMAGE},
            deployment_markers=markers,
            immutable_images=immutable,
        )["outcome"]
        == "healthy"
    )
    current["containers"]["app"][0]["id"] = "another-replacement"
    assert (
        _probe_containers(
            current,
            verification,
            {"app": IMAGE},
            deployment_markers=markers,
            immutable_images=immutable,
        )["outcome"]
        == "superseded"
    )


@pytest.mark.parametrize(
    "reason", ["pod_not_ready", "pod_inspection_failed", "pod_image_id_unavailable"]
)
async def test_temporary_kubernetes_evidence_failure_is_retryable(storage, reason):
    adapter = SimpleNamespace(
        capture_snapshot=AsyncMock(
            return_value={
                "workload": {
                    "kind": "Deployment",
                    "metadata": {},
                    "spec": {"template": {"spec": {"containers": []}}},
                },
                "containers": {},
                "artifact_evidence": {"status": "unavailable", "reason": reason},
            }
        )
    )
    executor = SimpleNamespace(target_ref={"mode": "kubernetes_workload"})
    with pytest.raises(ValueError, match="deployment_artifact_evidence_unavailable"):
        await native_review(storage, None, executor, adapter, None)


async def test_baseline_reconciliation_survives_cancelled_finalization(storage, monkeypatch):
    executor, scheduler, observer, _, _, task, _ = await setup(storage, monkeypatch, stable=0)
    handler = scheduler.deploy_tasks
    plan = await handler.admission.latest(task["id"])
    markers = managed_markers(
        await handler.admission.installation_id(), plan["target_id"], task["id"]
    )
    db = await storage._get_connection()
    row = await (
        await db.execute("SELECT * FROM deployment_observations WHERE task_id=?", (task["id"],))
    ).fetchone()
    monkeypatch.setattr(
        handler, "refresh_admission_baseline", AsyncMock(side_effect=asyncio.CancelledError)
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await observer._finalize(row, executor, {"outcome": "healthy", "services": []})
        saved = await storage.tasks.get(task["id"])
        assert saved["state"] == "succeeded" and saved["result"]["baseline_status"] == "pending"
        assert (
            await (
                await db.execute(
                    "SELECT state FROM deployment_observations WHERE task_id=?", (task["id"],)
                )
            ).fetchone()
        )[0] == "completed"
        await db.execute(
            "UPDATE tasks SET result=json_set(result,'$.baseline_next_attempt',0) WHERE id=?",
            (task["id"],),
        )
        await db.commit()
        restarted = DeployTasks(storage, scheduler)
        live = verified_evidence(
            task, markers=(markers,), configuration={"kind": "container", "artifacts": "verified"}
        )
        monkeypatch.setattr(restarted, "_collect_admission_evidence", AsyncMock(return_value=live))
        await restarted._reconcile_baselines()
        assert (await storage.tasks.get(task["id"]))["result"]["baseline_status"] == "verified"
        restarted._collect_admission_evidence.reset_mock()
        await restarted._reconcile_baselines()
        restarted._collect_admission_evidence.assert_not_awaited()
    finally:
        await observer.shutdown()


async def test_unverifiable_baseline_has_bounded_retries(storage, monkeypatch):
    executor, scheduler, observer, _, _, task, _ = await setup(storage, monkeypatch, stable=0)
    db = await storage._get_connection()
    try:
        await db.execute(
            "UPDATE tasks SET state='succeeded',result=json_set(result,'$.baseline_attempts',119,'$.baseline_status','pending') WHERE id=?",
            (task["id"],),
        )
        await db.commit()
        await scheduler.deploy_tasks._baseline_receipt(task["id"], False)
        saved = await storage.tasks.get(task["id"])
        assert (saved["state"], saved["error_code"]) == (
            "needs_attention",
            "deployment_baseline_unverified",
        )
        assert saved["result"]["baseline_status"] == "needs_attention"
        probe = AsyncMock()
        monkeypatch.setattr(scheduler.deploy_tasks, "refresh_admission_baseline", probe)
        await scheduler.deploy_tasks._reconcile_baselines()
        probe.assert_not_awaited()
    finally:
        await observer.shutdown()


async def test_delayed_failed_read_does_not_replace_verified_receipt(storage, monkeypatch):
    from test_deployment_readiness_queue import setup

    _, scheduler, observer, _, _, task, _ = await setup(storage, monkeypatch, stable=0)
    try:
        await scheduler.deploy_tasks._baseline_receipt(task["id"], True)
        before = await storage.tasks.get(task["id"])
        await scheduler.deploy_tasks._baseline_receipt(task["id"], False)
        after = await storage.tasks.get(task["id"])
        assert after["result"] == before["result"]
        assert after["result"]["baseline_status"] == "verified"
    finally:
        await observer.shutdown()
