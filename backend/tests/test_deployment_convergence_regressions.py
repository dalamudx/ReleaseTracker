"""Regression cases from asynchronous Stack rollouts and windowed K8s updates."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
import time

import pytest

from releasetracker.services.deployment_plan import managed_markers
from releasetracker.services import deployment_readiness_probes as probes
from releasetracker.storage.sqlite_deployment_admission import AdmissionConflict
from test_deployment_admission import staged, task, managed_target
from test_deployment_readiness_probes import container, IMAGE


@pytest.mark.asyncio
async def test_kubernetes_evidence_completes_only_missing_pod_digests(storage):
    from helpers.executor_runtime import create_runtime_connection
    from releasetracker.config import ExecutorConfig
    from releasetracker.executor_scheduler import ExecutorScheduler
    from releasetracker.services.deploy_tasks import DeployTasks

    connection = await create_runtime_connection(storage)
    executor = ExecutorConfig(
        name="isolated",
        runtime_type="docker",
        runtime_connection_id=connection,
        tracker_name="test",
        target_ref={"mode": "container", "container_name": "isolated"},
    )
    executor = executor.model_copy(
        update={"id": 123, "target_ref": {"mode": "kubernetes_workload"}}
    )
    snapshot = {
        "mode": "kubernetes_workload",
        "recovery_scope": "workload_spec",
        "namespace": "isolated",
        "kind": "Deployment",
        "name": "test",
        "containers": {"web": "nginx:1.0.0"},
        "workload": {
            "metadata": {"uid": "uid"},
            "api_spec": {
                "replicas": 1,
                "template": {"spec": {"containers": [{"name": "web", "image": "nginx:1.0.0"}]}},
            },
        },
    }
    adapter = SimpleNamespace(
        validate_target_ref=AsyncMock(),
        get_managed_markers=AsyncMock(return_value=({},)),
        fetch_workload_service_images=AsyncMock(return_value=snapshot["containers"]),
        capture_snapshot=AsyncMock(side_effect=lambda *a: deepcopy(snapshot)),
        is_target_missing_error=lambda exc: False,
    )
    scheduler = ExecutorScheduler(storage)
    scheduler._adapters[123] = adapter
    handler = DeployTasks(storage, scheduler)
    try:
        incomplete = await handler._collect_admission_evidence(executor)
        assert incomplete.pre_rollout_hash is None
        snapshot["artifact_evidence"] = {
            "status": "verified",
            "images": {"containers": {"web": "nginx@sha256:" + "a" * 64}, "initContainers": {}},
        }
        complete = await handler._collect_admission_evidence(executor)
        assert complete.pre_rollout_hash == incomplete.evidence_hash
        assert complete.evidence_hash != incomplete.evidence_hash
        snapshot["workload"]["api_spec"]["replicas"] = 2
        changed = await handler._collect_admission_evidence(executor)
        assert changed.pre_rollout_hash != incomplete.evidence_hash
        snapshot["artifact_evidence"]["status"] = "unverified"
        assert (await handler._collect_admission_evidence(executor)).pre_rollout_hash is None
    finally:
        await scheduler.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["complete", "config_changed", "other_task", "failed", "already_verified"]
)
async def test_incomplete_baseline_requires_exact_successful_deployment(storage, case):
    store, queued, observed, plan = await staged(storage)
    await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    claimed = await storage.tasks.claim("deploy")
    await storage.tasks.start_attempt(claimed)
    await storage.tasks.checkpoint(claimed, {"mutation_started": True})
    await store.mark_applied(claimed, observed)
    await storage.tasks.finish(
        claimed, "failed" if case == "failed" else "succeeded", result={"mutation_started": True}
    )
    markers = managed_markers(
        await store.installation_id(),
        plan["target_id"],
        claimed["id"] + (1 if case == "other_task" else 0),
    )
    complete = replace(
        observed,
        configuration=observed.configuration | {"runtime_configuration": "verified"},
        markers=(markers,),
        baseline_task_id=claimed["id"],
        pre_rollout_hash=(
            observed.evidence_hash
            if case not in {"config_changed", "already_verified"}
            else "different"
        ),
    )
    await task(storage)
    next_task = await storage.tasks.claim("deploy")
    next_plan = await store.stage(next_task, complete)
    if case != "complete":
        assert next_plan["reason"] == "configuration_drift"
        assert (await managed_target(storage))["baseline"] == observed.evidence_hash
        return
    assert next_plan["state"] == "approved" and next_plan["reason"] == "managed"
    assert (await managed_target(storage))["baseline"] == complete.evidence_hash
    # A user confirming an already auto-authorized refreshed plan gets an idempotent receipt.
    confirmed = await store.approve(
        next_task["id"], next_plan["id"], next_plan["fingerprint"], "admin"
    )
    assert confirmed["state"] == "approved" and confirmed["reason"] == "managed"
    await storage.tasks.finish(next_task, "skipped")
    await task(storage)
    # Once completed, even a digest-only change must still require review.
    changed = replace(
        complete,
        configuration=complete.configuration | {"runtime_configuration": "different-digest"},
    )
    assert (await store.stage(await storage.tasks.claim("deploy"), changed))[
        "reason"
    ] == "configuration_drift"


@pytest.mark.asyncio
async def test_approval_survives_window_wait_but_not_drift_or_expiry(storage):
    store, queued, observed, plan = await staged(storage)
    now = time.time()
    window = now + 17 * 3600
    approved = await store.approve(
        queued["id"], plan["id"], plan["fingerprint"], "admin", now=now, not_before=window
    )
    assert approved["expires_at"] == window + 1800
    assert (await storage.tasks.get(queued["id"]))["due_at"] == window
    assert await storage.tasks.claim("deploy", now=window - 1) is None
    repeated = await store.approve(
        queued["id"],
        plan["id"],
        plan["fingerprint"],
        "admin",
        now=now + 60,
        not_before=window + 86400,
    )
    assert repeated["expires_at"] == approved["expires_at"]
    claimed = await storage.tasks.claim("deploy", now=window)
    assert (await store.stage(claimed, observed, now=window))["state"] == "approved"
    assert (await store.verify_before_write(claimed, observed, now=window))["id"] == plan["id"]
    with pytest.raises(AdmissionConflict, match="deployment_plan_changed"):
        await store.verify_before_write(
            claimed, replace(observed, configuration={"changed": True}), now=window
        )
    # A long-lived lease allows us to test the independent approval expiry guard.
    db = await storage._get_connection()
    await db.execute("UPDATE tasks SET lease_until=? WHERE id=?", (window + 7200, claimed["id"]))
    await db.commit()
    with pytest.raises(AdmissionConflict, match="deployment_plan_changed"):
        await store.verify_before_write(claimed, observed, now=window + 1800)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "manual,inside,expired",
    [(False, False, False), (False, False, True), (True, False, False), (False, True, False)],
)
async def test_approval_api_uses_window_only_for_automatic_work(
    storage, monkeypatch, manual, inside, expired
):
    from releasetracker.routers.tasks import approve_deployment, DeploymentApproval
    from releasetracker.services.deploy_tasks import DeployTasks

    store, queued, observed, plan = await staged(storage)
    db = await storage._get_connection()
    payload = queued["payload"] | {"manual": manual}
    await db.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps(payload), queued["id"]))
    if expired:
        await db.execute(
            "UPDATE deployment_plans SET expires_at=? WHERE id=?", (time.time() - 10, plan["id"])
        )
    await db.commit()
    monkeypatch.setattr(
        storage,
        "get_executor_config",
        AsyncMock(
            return_value=SimpleNamespace(
                update_mode="maintenance_window", maintenance_window="window"
            )
        ),
    )
    monkeypatch.setattr(
        DeployTasks, "_collect_admission_evidence", AsyncMock(return_value=observed)
    )
    scheduler = SimpleNamespace(
        _refresh_system_timezone=AsyncMock(),
        _within_maintenance_window=lambda w: inside,
        _seconds_until_next_maintenance_window=lambda w: 17 * 3600,
    )
    response = await approve_deployment(
        queued["id"],
        DeploymentApproval(plan_id=plan["id"], fingerprint=plan["fingerprint"], plan_reviewed=True),
        storage,
        SimpleNamespace(username="admin"),
        scheduler,
    )
    assert response.status_code == 202
    approved = await store.latest(queued["id"])
    current = await storage.tasks.get(queued["id"])
    waiting = not manual and not inside
    assert (current["due_at"] > time.time() + 16 * 3600) == waiting
    assert (approved["expires_at"] > time.time() + 17 * 3600) == waiting


@pytest.mark.parametrize(
    "case", ["own", "foreign", "missing_marker", "changed_image", "partial", "missing_proof"]
)
def test_skipped_stack_service_replacement_requires_own_deployment(case):
    markers = managed_markers("installation", "target", 1928)
    old = {"containers": {"service-a": [container("old-a"), container("old-b")]}}
    current = {"containers": {"service-a": [container("new-a"), container("new-b")]}}
    for row in current["containers"]["service-a"]:
        row["markers"] = dict(markers)
    verification = {
        "baseline": old,
        "target": deepcopy(old),
        "services": [{"service": "service-a", "status": "skipped", "to_version": IMAGE}],
    }
    if case == "foreign":
        current["containers"]["service-a"][0]["markers"]["releasetracker.io/deployment-id"] = "1929"
    if case == "missing_marker":
        current["containers"]["service-a"][0]["markers"] = {}
    if case == "changed_image":
        current["containers"]["service-a"][0]["image_id"] = "sha256:" + "b" * 64
    if case == "partial":
        current["containers"]["service-a"][0] = container("old-a")
    result = probes._probe_containers(
        current,
        verification,
        {"service-a": IMAGE},
        deployment_markers=None if case == "missing_proof" else markers,
    )
    if case not in {"own", "partial"}:
        assert result["outcome"] == "superseded" and "verification_update" not in result
        return
    assert result["outcome"] == "pending"
    if case == "partial":
        assert "verification_update" not in result
        return
    verification.update(json.loads(json.dumps(result["verification_update"])))
    assert (
        probes._probe_containers(
            current, verification, {"service-a": IMAGE}, deployment_markers=markers
        )["outcome"]
        == "healthy"
    )
    current["containers"]["service-a"][0]["id"] = "later-replacement"
    assert (
        probes._probe_containers(
            current, verification, {"service-a": IMAGE}, deployment_markers=markers
        )["outcome"]
        == "superseded"
    )


@pytest.mark.asyncio
async def test_recheck_chain_finishes_original_plan_and_baseline(storage, monkeypatch):
    from unittest.mock import MagicMock
    from releasetracker.services.recovery_tasks import RecoveryTasks
    from releasetracker.services.task_queue import TaskQueue
    from test_deployment_readiness_queue import (
        setup,
        observe,
        admission_baseline,
        verified_evidence,
    )

    executor, scheduler, observer, probe, now, original, run_id = await setup(
        storage, monkeypatch, stable=0
    )
    handler = scheduler.deploy_tasks
    plan = await handler.admission.latest(original["id"])
    markers = managed_markers(
        await handler.admission.installation_id(), plan["target_id"], original["id"]
    )
    converged = verified_evidence(
        original, configuration={"kind": "container", "complete": True}, markers=(markers,)
    )
    monkeypatch.setattr(handler, "_collect_admission_evidence", AsyncMock(return_value=converged))
    db = await storage._get_connection()
    # Simulate a failed post-write evidence read, as in the original Portainer run.
    await db.execute(
        "UPDATE deployment_plans SET state='approved',applied_at=NULL WHERE id=?", (plan["id"],)
    )
    await db.execute(
        "UPDATE managed_targets SET baseline=NULL,last_task_id=NULL WHERE executor_id=?",
        (executor.id,),
    )
    await db.commit()
    assert await probes._portainer_deployment_markers(storage, executor, run_id) == markers
    # Exercise the public probe with persisted legacy verification lacking markers.
    portainer = executor.model_copy(update={"target_ref": {"mode": "portainer_stack"}})
    old = {"kind": "portainer_stack", "containers": {"service-a": [container("old")]}}
    current = deepcopy(old)
    verification = {
        "run_id": run_id,
        "baseline": old,
        "target": deepcopy(old),
        "services": [{"service": "service-a", "status": "skipped", "to_version": IMAGE}],
    }
    with monkeypatch.context() as native:
        native.setattr(probes, "_capture", AsyncMock(side_effect=lambda *a: deepcopy(current)))
        native.setattr(
            probes, "_portainer_images", AsyncMock(return_value={"service-a": "sha256:" + "a" * 64})
        )
        assert (await probes.probe_deployment(storage, scheduler, portainer, verification))[
            "outcome"
        ] == "pending"
        current["containers"]["service-a"] = [container("new") | {"markers": markers}]
        observed = await probes.probe_deployment(storage, scheduler, portainer, verification)
        assert observed["outcome"] == "pending" and "verification_update" in observed
        verification.update(json.loads(json.dumps(observed["verification_update"])))
        assert (await probes.probe_deployment(storage, scheduler, portainer, verification))[
            "outcome"
        ] == "healthy"
    probe.return_value = {"outcome": "superseded", "services": []}
    await observe(storage, observer, original["id"])
    queue = TaskQueue(storage.tasks, MagicMock())
    queue.register("recover", RecoveryTasks(storage, scheduler))
    scheduler._run_executor_with_overlap_guard = AsyncMock(
        side_effect=AssertionError("must not redeploy")
    )
    previous_id = original["id"]
    try:
        for outcome in ("superseded", "healthy"):
            receipt = await observer.enqueue_recheck(await storage.tasks.get(previous_id))
            now[0] = max(now[0] + 1, time.time() + 1)
            recheck = await storage.tasks.claim("recover", now=now[0])
            assert recheck["id"] == receipt["task_id"]
            await queue._run(recheck)
            probe.return_value = {"outcome": outcome, "services": []}
            await observe(storage, observer, recheck["id"])
            previous_id = recheck["id"]
        assert (await storage.tasks.get(original["id"]))["state"] == "succeeded"
        assert (await handler.admission.latest(original["id"]))["state"] == "applied"
        assert await admission_baseline(storage, executor.id) == (
            converged.evidence_hash,
            original["id"],
        )
        scheduler._run_executor_with_overlap_guard.assert_not_awaited()
    finally:
        await scheduler.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["failed", "newer_write", "newer_baseline"])
async def test_missing_handoff_baseline_cannot_override_newer_or_failed_write(storage, case):
    from test_deployment_admission import succeeded_without_baseline

    store, previous, owned = await succeeded_without_baseline(
        storage, final_state="failed" if case == "failed" else "succeeded"
    )
    if case == "newer_write":
        await task(storage)
        claimed = await storage.tasks.claim("deploy")
        await storage.tasks.start_attempt(claimed)
        await storage.tasks.checkpoint(claimed, {"mutation_started": True})
    if case == "newer_baseline":
        db = await storage._get_connection()
        await db.execute(
            "UPDATE managed_targets SET baseline='newer',last_task_id=? WHERE executor_id=1",
            (previous["id"] + 1,),
        )
        await db.commit()
    assert not await store.refresh_applied(previous["id"], owned)
    assert (await store.latest(previous["id"]))["state"] == "approved"
