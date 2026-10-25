"""Deployment completion, baseline review and Helm artifact receipts are independent."""

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from releasetracker.config import ExecutorConfig, RuntimeConnectionConfig
from releasetracker.executors.kubernetes import KubernetesRuntimeAdapter
from releasetracker.services.deploy_tasks import DeployTasks
from releasetracker.services.deployment_baseline import BaselineResult
from releasetracker.routers.tasks import recheck_baseline
from test_deployment_admission import task, staged
from test_deployment_readiness_queue import setup, observe

pytestmark = pytest.mark.asyncio


async def test_helm_receipts_do_not_change_identity_but_release_coordinates_do(storage):
    handler = DeployTasks(storage, SimpleNamespace())
    executor = ExecutorConfig(
        name="chart",
        tracker_name="helm-test",
        runtime_type="kubernetes",
        runtime_connection_id=1,
        target_ref={
            "mode": "helm_release",
            "namespace": "apps",
            "release_name": "demo",
            "chart_name": "demo",
            "chart_version": "1.0.0",
        },
    )
    identity = await handler.identity(executor)
    updated = executor.model_copy(
        update={
            "target_ref": executor.target_ref
            | {
                "chart_name": "demo",
                "chart_version": "1.1.0",
                "chart_digest": "a" * 64,
                "chart_revision": 2,
            }
        }
    )
    assert await handler.identity(updated) == identity
    changed = updated.model_copy(update={"target_ref": updated.target_ref | {"namespace": "other"}})
    assert await handler.identity(changed) != identity


async def test_superseded_baseline_does_not_downgrade_success(storage, monkeypatch):
    executor, scheduler, observer, _, _, original, _ = await setup(storage, monkeypatch, stable=0)
    try:
        await observe(storage, observer, original["id"])
        await observe(storage, observer, original["id"])
        newer = await task(storage, executor.id)
        async with storage.tasks.transaction() as db:
            await db.execute(
                "UPDATE tasks SET result=json_set(result,'$.baseline_status','pending') WHERE id=?",
                (original["id"],),
            )
            await db.execute(
                "UPDATE tasks SET result=? WHERE id=?",
                (json.dumps({"mutation_started": True}), newer["id"]),
            )
        collect = AsyncMock(side_effect=AssertionError("obsolete baselines need no remote read"))
        monkeypatch.setattr(scheduler.deploy_tasks, "_collect_admission_evidence", collect)
        assert not await scheduler.deploy_tasks.refresh_admission_baseline(
            original["id"], executor.id
        )
        saved = await storage.tasks.get(original["id"])
        assert saved["state"] == "succeeded"
        assert saved["result"]["baseline_status"] == "superseded"
        collect.assert_not_awaited()
    finally:
        await observer.shutdown()
        await scheduler.shutdown()


@pytest.mark.parametrize("healthy", [True, False])
async def test_only_healthy_legacy_baseline_failures_release_global_fence(
    storage, monkeypatch, healthy
):
    executor, scheduler, observer, _, _, original, run_id = await setup(
        storage, monkeypatch, stable=0
    )
    try:
        async with storage.tasks.transaction() as db:
            await db.execute(
                "UPDATE executor_run_history SET status='success' WHERE id=?", (run_id,)
            )
            await db.execute(
                "UPDATE deployment_observations SET state='blocked' WHERE task_id=?",
                (original["id"],),
            )
            await db.execute(
                "UPDATE tasks SET state='needs_attention',owner=NULL,error_code='deployment_baseline_unverified',result=? WHERE id=?",
                (
                    json.dumps(
                        {
                            "run_id": run_id,
                            "mutation_started": True,
                            "baseline_status": "needs_attention",
                            "health_check": {"outcome": "healthy" if healthy else "unknown"},
                        }
                    ),
                    original["id"],
                ),
            )
        await scheduler.deploy_tasks._reconcile_baselines()
        saved = await storage.tasks.get(original["id"])
        assert saved["state"] == ("succeeded" if healthy else "needs_attention")
        another = await task(storage, executor.id + 1)
        blockers = await storage.tasks.blockers(await storage.tasks.get(another["id"]))
        assert bool(blockers) is (not healthy)
        assert bool(await storage.tasks.claim("deploy")) is healthy
    finally:
        await observer.shutdown()
        await scheduler.shutdown()


async def test_skipped_pending_baseline_is_retried(storage):
    queued = await task(storage)
    async with storage.tasks.transaction() as db:
        await db.execute(
            "UPDATE tasks SET state='skipped',result=? WHERE id=?",
            (json.dumps({"baseline_status": "pending", "baseline_next_attempt": 0}), queued["id"]),
        )
    handler = DeployTasks(storage, SimpleNamespace())
    handler.refresh_admission_baseline = AsyncMock()
    await handler._reconcile_baselines()
    handler.refresh_admission_baseline.assert_awaited_once_with(queued["id"], 1)


async def test_legacy_plan_is_reviewable_without_120_reads(storage):
    store, queued, evidence, plan = await staged(storage)
    await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    async with storage.tasks.transaction() as db:
        await db.execute("UPDATE tasks SET state='succeeded' WHERE id=?", (queued["id"],))
        await db.execute(
            "UPDATE deployment_plans SET summary=json_remove(summary,'$.desired_configuration_hash') WHERE id=?",
            (plan["id"],),
        )
    outcome = await store.refresh_applied(queued["id"], evidence)
    assert outcome == BaselineResult("review_required", "legacy_plan_missing_proof")


async def test_baseline_recheck_preserves_outcome_and_never_enqueues_deployment(storage):
    original = await task(storage)
    async with storage.tasks.transaction() as db:
        await db.execute(
            "UPDATE tasks SET state='succeeded',result=? WHERE id=?",
            (
                json.dumps({"baseline_status": "review_required", "mutation_started": True}),
                original["id"],
            ),
        )
    assert await recheck_baseline(original["id"], storage) == {"status": "pending"}
    saved = await storage.tasks.get(original["id"])
    assert saved["state"] == "succeeded" and saved["result"]["mutation_started"]
    assert saved["result"]["baseline_status"] == "pending"
    assert await storage.tasks.claim("deploy") is None


async def test_slow_baseline_does_not_block_dispatch_and_has_one_owned_worker(storage):
    workers = []
    scheduler = SimpleNamespace(
        _track_background_task=lambda coro: workers.append(asyncio.create_task(coro)) or workers[-1]
    )
    handler = DeployTasks(storage, scheduler)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow():
        entered.set()
        await release.wait()

    handler._reconcile_baselines = slow
    try:
        await handler.dispatch_baseline_checks()
        assert await asyncio.wait_for(handler.dispatch_pending(), 1) == 0
        await asyncio.wait_for(entered.wait(), 1)
        assert await asyncio.wait_for(handler.dispatch_pending(), 1) == 0
        await handler.dispatch_baseline_checks()
        assert len(workers) == 1
    finally:
        release.set()
        await asyncio.gather(*workers)


def helm(monkeypatch, *, current="1.0.0", installed="1.0.0"):
    adapter = KubernetesRuntimeAdapter(
        RuntimeConnectionConfig(
            name="isolated",
            type="kubernetes",
            config={"namespace": "apps", "in_cluster": True},
            secrets={},
        )
    )
    calls = []
    release = {"chart": {"metadata": {"version": current}}, "version": 1}

    def command(args):
        calls.append(args)
        if args[0] == "pull":
            (Path(args[args.index("--destination") + 1]) / "demo.tgz").write_bytes(
                b"isolated chart bytes"
            )
        if args[0] == "upgrade":
            assert Path(args[2]).read_bytes() == b"isolated chart bytes"
            release.update(chart={"metadata": {"version": installed}}, version=2)
            return json.dumps(release | {"name": "demo", "namespace": "apps"})
        return ""

    monkeypatch.setattr(adapter, "_run_helm_command", command)
    monkeypatch.setattr(adapter, "_get_helm_release", lambda _: release)
    monkeypatch.setattr(adapter, "_ensure_helm_marker_target", Mock())
    monkeypatch.setattr(adapter, "_apply_helm_managed_markers", Mock())
    return adapter, calls


@pytest.mark.parametrize("matches", [True, False])
async def test_same_version_helm_verifies_archive_before_any_write(monkeypatch, matches):
    adapter, calls = helm(monkeypatch)
    actual = "sha256:" + hashlib.sha256(b"isolated chart bytes").hexdigest()
    guard = AsyncMock()
    monkeypatch.setattr("releasetracker.services.task_effects.mark_deployment_mutation", guard)
    ref = {"mode": "helm_release", "namespace": "apps", "release_name": "demo"}
    args = dict(
        chart_ref="demo",
        chart_version="1.0.0",
        repo_url="https://example.test/charts",
        expected_digest=actual if matches else "b" * 64,
    )
    if matches:
        result = await adapter.upgrade_helm_release(ref, **args)
        assert result.updated and result.artifact_digest == actual and result.artifact_revision == 2
        assert [c[0] for c in calls] == ["pull", "upgrade"]
        guard.assert_awaited_once()
    else:
        with pytest.raises(ValueError, match="digest does not match"):
            await adapter.upgrade_helm_release(ref, **args)
        assert [c[0] for c in calls] == ["pull"]
        guard.assert_not_awaited()
    assert not Path(calls[0][calls[0].index("--destination") + 1]).exists()


async def test_helm_digest_receipt_is_bound_to_live_revision(monkeypatch):
    adapter, calls = helm(monkeypatch)
    ref = {
        "mode": "helm_release",
        "namespace": "apps",
        "release_name": "demo",
        "chart_version": "1.0.0",
        "chart_digest": "a" * 64,
        "chart_revision": 1,
    }
    assert await adapter.get_helm_release_digest(ref) == "sha256:" + "a" * 64
    result = await adapter.upgrade_helm_release(
        ref, chart_ref="demo", chart_version="1.0.0", repo_url=None, expected_digest="a" * 64
    )
    assert not result.updated and not calls


async def test_helm_chart_version_falls_back_to_release_metadata(monkeypatch):
    adapter, calls = helm(monkeypatch)
    monkeypatch.setattr(adapter, "_get_helm_release", lambda _: {"name": "demo", "version": 4})
    monkeypatch.setattr(
        adapter,
        "_run_helm_command",
        lambda args: calls.append(args) or json.dumps({"version": "2.0.0", "revision": 5}),
    )
    ref = {
        "mode": "helm_release",
        "namespace": "apps",
        "release_name": "demo",
        "chart_version": "1.0.0",
        "chart_digest": "a" * 64,
        "chart_revision": 5,
    }
    assert await adapter.get_helm_release_version(ref) == "2.0.0"
    assert calls[0][:3] == ["get", "metadata", "demo"]
    # The receipt binds to the metadata record, so an outside upgrade drops the old digest.
    assert await adapter.get_helm_release_digest(ref) is None
    assert await adapter.get_helm_release_digest(ref | {"chart_version": "2.0.0"}) == (
        "sha256:" + "a" * 64
    )
    assert await adapter.get_helm_release_digest(ref | {"chart_revision": 2}) is None


async def test_pending_baseline_requires_review_for_next_actual_update(storage):
    from test_deployment_admission import succeeded_without_baseline

    store, previous, owned = await succeeded_without_baseline(storage)
    assert await store.refresh_applied(previous["id"], owned)
    handler = DeployTasks(storage, SimpleNamespace())
    await handler._baseline_receipt(
        previous["id"], BaselineResult("review_required", "legacy_plan_missing_proof")
    )
    await task(storage)
    claimed = await storage.tasks.claim("deploy")
    plan = await store.stage(claimed, owned)
    assert plan["state"] == "pending"
    assert plan["reason"] == "baseline_review_required"


async def test_new_baseline_review_invalidates_old_automatic_approval(storage):
    from test_deployment_admission import succeeded_without_baseline

    store, previous, owned = await succeeded_without_baseline(storage)
    assert await store.refresh_applied(previous["id"], owned)
    await task(storage)
    claimed = await storage.tasks.claim("deploy")
    approved = await store.stage(claimed, owned)
    assert approved["state"] == "approved"
    handler = DeployTasks(storage, SimpleNamespace())
    await handler._baseline_receipt(
        previous["id"], BaselineResult("review_required", "configuration_changed")
    )
    reviewed = await store.stage(claimed, owned)
    assert reviewed["state"] == "pending"
    assert reviewed["reason"] == "baseline_review_required"


async def test_baseline_recheck_rejects_unfinished_execution(storage):
    from fastapi import HTTPException

    queued = await task(storage)
    with pytest.raises(HTTPException) as error:
        await recheck_baseline(queued["id"], storage)
    assert error.value.status_code == 409


async def test_baseline_worker_registered_and_drained_on_scheduler_shutdown(storage):
    from releasetracker.executor_scheduler import ExecutorScheduler

    host = SimpleNamespace(start=AsyncMock(), add_interval_job=Mock())
    scheduler = ExecutorScheduler(storage, scheduler_host=host)
    handler = scheduler.deploy_tasks = DeployTasks(storage, scheduler)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def slow():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    handler._reconcile_baselines = slow
    scheduler.reconcile_pending_desired_states = AsyncMock()
    try:
        await scheduler.start()
        await asyncio.wait_for(entered.wait(), 1)
        assert any(
            call.args[1] == "deployment-baselines"
            and call.args[2] == handler.dispatch_baseline_checks
            for call in host.add_interval_job.call_args_list
        )
        await scheduler.shutdown()
        assert cancelled.is_set()
        await handler.dispatch_baseline_checks()
        assert not scheduler._background_tasks
    finally:
        await scheduler.shutdown()


async def test_external_helm_upgrade_cannot_inherit_this_archive_digest(monkeypatch):
    from releasetracker.executors.base import RuntimeMutationError

    adapter, calls = helm(monkeypatch)
    command = adapter._run_helm_command

    def raced(args):
        output = command(args)
        if args[0] == "upgrade":
            monkeypatch.setattr(
                adapter,
                "_get_helm_release",
                lambda _: {"chart": {"metadata": {"version": "1.0.0"}}, "version": 3},
            )
        return output

    monkeypatch.setattr(adapter, "_run_helm_command", raced)
    digest = "sha256:" + hashlib.sha256(b"isolated chart bytes").hexdigest()
    with pytest.raises(RuntimeMutationError, match="does not match upgrade receipt"):
        await adapter.upgrade_helm_release(
            {"mode": "helm_release", "namespace": "apps", "release_name": "demo"},
            chart_ref="demo",
            chart_version="1.0.0",
            repo_url=None,
            expected_digest=digest,
        )
    adapter._apply_helm_managed_markers.assert_not_called()
    assert [call[0] for call in calls] == ["pull", "upgrade"]


async def test_recovery_diagnostics_do_not_treat_blocked_deployment_as_a_fence(
    storage, monkeypatch
):
    executor, scheduler, observer, _, _, original, _ = await setup(storage, monkeypatch)
    try:
        async with storage.tasks.transaction() as db:
            await db.execute(
                "UPDATE tasks SET state='needs_attention',owner=NULL WHERE id=?", (original["id"],)
            )
            await db.execute(
                "UPDATE deployment_observations SET state='blocked' WHERE task_id=?",
                (original["id"],),
            )
        recovery = await storage.tasks.enqueue(
            kind="recover",
            resource_key="deployment-mutations",
            dedupe_key="recovery-diagnostics",
            target_label="isolated",
            payload={"executor_id": executor.id},
            trigger_mode="manual",
        )
        assert await storage.tasks.blockers(await storage.tasks.get(recovery["id"])) == []
        assert await storage.tasks.claim("recover") is not None
    finally:
        await observer.shutdown()
        await scheduler.shutdown()
