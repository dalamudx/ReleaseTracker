"""Cross-flow contracts: cleanup, restore, events and deployment facts."""

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from releasetracker.models import ExecutorRunHistory, ExecutorStatus
from releasetracker.scheduler import ReleaseScheduler
from releasetracker.services.instance_backup import (
    InstanceBackup,
    restore_to_new_directory,
)
from releasetracker.services.restore_safety import REVIEW_SETTING
from releasetracker.services.runtime_health_watch import RuntimeHealthWatch
from releasetracker.services.rollback_service import RollbackService
from test_deployment_readiness_queue import setup, observe
from test_release_notification_outbox import Upstream, upstream_release, notifier, rows
from releasetracker.models import AggregateTracker, TrackerSource

pytestmark = pytest.mark.asyncio


async def test_cleanup_cannot_remove_active_observation_or_referenced_success(storage, monkeypatch):
    executor, _, observer, probe, _, task, run_id = await setup(storage, monkeypatch, stable=0)
    assert await storage.delete_executor_run_history(executor.id) == 0
    await observe(storage, observer, task["id"])
    assert (await storage.tasks.get(task["id"]))["state"] == "succeeded"
    assert await storage.delete_executor_run_history(executor.id) == 0
    assert (await storage.get_executor_run(run_id)).status == "success"
    probe.assert_awaited_once()


async def test_missing_run_is_blocked_instead_of_finalize_loop(storage, monkeypatch):
    _, _, observer, probe, _, task, run_id = await setup(storage, monkeypatch, stable=0)
    db = await storage._get_connection()
    await db.execute("DELETE FROM executor_run_history WHERE id=?", (run_id,))
    await db.commit()
    await observe(storage, observer, task["id"])
    updated = await storage.tasks.get(task["id"])
    assert updated["state"] == "needs_attention" and updated["error_code"] == "executor_run_missing"
    state = (
        await (
            await db.execute(
                "SELECT state FROM deployment_observations WHERE task_id=?", (task["id"],)
            )
        ).fetchone()
    )[0]
    assert state == "blocked"
    probe.assert_not_awaited()


async def test_noop_does_not_retire_successful_health_baseline(storage, monkeypatch):
    executor, scheduler, observer, _, _, task, run_id = await setup(storage, monkeypatch, stable=0)
    await observe(storage, observer, task["id"])
    await storage.create_executor_run(
        ExecutorRunHistory(
            executor_id=executor.id,
            status="skipped",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )
    )
    watch = RuntimeHealthWatch(storage, scheduler, observer)
    db = await storage._get_connection()
    row = await (
        await db.execute("SELECT * FROM deployment_observations WHERE task_id=?", (task["id"],))
    ).fetchone()
    # Override configuration identity only; the latest significant run must remain the original success.
    from releasetracker.services.deploy_tasks import DeployTasks

    monkeypatch.setattr(
        DeployTasks, "identity", AsyncMock(return_value=task["payload"]["config_identity"])
    )
    probe = AsyncMock(return_value={"outcome": "healthy", "services": []})
    watch.probe = probe
    candidate = dict(row) | {"task_payload": json.dumps(task["payload"])}
    assert await watch.check(candidate)
    probe.assert_awaited_once()
    assert (await storage.get_executor_run(run_id)).status == "success"


async def test_rollback_finalization_updates_summary_and_notification_atomically(
    storage, monkeypatch
):
    executor, _, _, _, _, _, run_id = await setup(storage, monkeypatch, stable=0)
    await storage.update_executor_status(
        ExecutorStatus(executor_id=executor.id, last_result="failed", last_version="2.0.0")
    )
    service = RollbackService(storage, MagicMock())
    run = await service._finalize_run(
        run_id=run_id,
        status="success",
        from_version="2.0.0",
        to_version="1.0.0",
        message=None,
        diagnostics={"services": []},
    )
    assert run.status == "success"
    status = await storage.get_executor_status(executor.id)
    assert status.last_result == "success" and status.last_version == "1.0.0"
    db = await storage._get_connection()
    intent = await (
        await db.execute(
            "SELECT payload FROM executor_notification_intents WHERE run_id=?",
            (run_id,),
        )
    ).fetchone()
    assert json.loads(intent[0])["status"] == "success"
    assert json.loads(intent[0])["to_version"] == "1.0.0"


async def test_restored_tasks_revoked_and_new_mutations_gated(
    storage, system_key_manager, tmp_path
):
    manager = system_key_manager
    try:
        await storage.tasks.enqueue(
            kind="recover",
            resource_key="deployment-mutations",
            dedupe_key="old",
            target_label="test-target",
            payload={},
            trigger_mode="manual",
        )
        archive = await InstanceBackup(storage, manager, tmp_path / "backups").create()
        target = tmp_path / "restored"
        await asyncio.to_thread(restore_to_new_directory, archive, target)
        restored = type(storage)(str(target / "releases.db"), system_key_manager=manager)
        try:
            old = await restored.tasks.get(1)
            assert old["state"] == "cancelled" and old["error_code"] == "restore_review_required"
            assert await restored.get_setting(REVIEW_SETTING)
            fresh = await restored.tasks.enqueue(
                kind="deploy",
                resource_key="deployment-mutations",
                dedupe_key="new",
                target_label="test-target",
                payload={},
                trigger_mode="manual",
            )
            assert await restored.tasks.claim("deploy") is None
            await restored.tasks.enqueue(
                kind="fetch",
                resource_key="tracker:1",
                dedupe_key="f",
                target_label="test-target",
                payload={},
                trigger_mode="manual",
            )
            assert (await restored.tasks.claim("fetch"))["kind"] == "fetch"
            db = await restored._get_connection()
            await db.execute("DELETE FROM settings WHERE key=?", (REVIEW_SETTING,))
            await db.commit()
            assert (await restored.tasks.claim("deploy"))["id"] == fresh["id"]
        finally:
            await restored.close()
    finally:
        await storage.close()


async def test_restore_revokes_pending_desired_intent(
    storage, monkeypatch, system_key_manager, tmp_path
):
    executor, _, observer, _, _, task, _ = await setup(storage, monkeypatch, stable=0)
    await observe(storage, observer, task["id"])
    await storage.upsert_executor_desired_state(
        executor_id=executor.id,
        desired_state_revision="old-authorized",
        desired_target={"version": "2.0.0"},
    )
    claimed = await storage.claim_pending_executor_desired_states(claimed_by="old-worker")
    assert len(claimed) == 1
    archive = await InstanceBackup(storage, system_key_manager, tmp_path / "backup").create()
    target = tmp_path / "restored"
    await asyncio.to_thread(restore_to_new_directory, archive, target)
    restored = type(storage)(str(target / "releases.db"), system_key_manager=system_key_manager)
    try:
        state = await restored.get_executor_desired_state(executor.id)
        assert not state.pending and state.claimed_by is None and state.claim_until is None
        assert state.last_completed_revision == "old-authorized"
    finally:
        await restored.close()


async def test_history_cleanup_retains_snapshot_references(storage, monkeypatch):
    from releasetracker.models import ExecutorSnapshot

    executor, _, observer, _, _, task, _ = await setup(storage, monkeypatch, stable=0)
    await observe(storage, observer, task["id"])
    snapshot_run = await storage.create_executor_run(
        ExecutorRunHistory(
            executor_id=executor.id,
            status="skipped",
            started_at=datetime(2000, 1, 1),
            finished_at=datetime(2000, 1, 1),
        )
    )
    snapshot_id = await storage.create_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor.id,
            executor_run_id=snapshot_run,
            trigger="pre_update",
            snapshot_data={"image": "old"},
        )
    )
    assert await storage.delete_executor_run_history(executor.id) == 0
    assert await storage.prune_old_executor_runs() == 0
    assert (
        await storage.get_executor_snapshot_by_id(executor.id, snapshot_id)
    ).executor_run_id == snapshot_run
    db = await storage._get_connection()
    assert await (await db.execute("PRAGMA foreign_key_check")).fetchall() == []


async def test_corrupt_relations_are_not_valid_backup(storage, system_key_manager, tmp_path):
    manager = system_key_manager
    try:
        db = await storage._get_connection()
        await db.execute(
            "INSERT INTO task_attempts(task_id,attempt,owner,state,started_at) VALUES(99999,1,'audit','running',0)"
        )
        await db.commit()
        with pytest.raises(ValueError, match="foreign key"):
            await InstanceBackup(storage, manager, tmp_path / "backups").create()
        assert not list((tmp_path / "backups").glob("*.zip"))
    finally:
        await storage.close()


async def test_lost_restore_points_records_failure_and_deduplicated_alert(
    storage, system_key_manager, tmp_path
):
    manager = system_key_manager
    try:
        await notifier(storage, events=("error",))
        backup = InstanceBackup(storage, manager, tmp_path / "backups")
        archive = await backup.create()
        archive.unlink()
        assert not await backup.verify_latest()
        assert not await backup.verify_latest()
        state = await backup.status()
        assert state["last_error_code"] == "backup_missing" and state["consecutive_failures"] == 2
        assert len(await rows(storage)) == 1
    finally:
        await storage.close()


async def test_projection_commit_rolls_back_with_notification_failure(storage, monkeypatch):
    tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="channels",
            sources=[
                TrackerSource(
                    source_key="repo", source_type="github", source_config={"repo": "acme/app"}
                )
            ],
        )
    )
    scheduler = ReleaseScheduler(storage)
    releases = [upstream_release("1.0.0", 0, False)]
    monkeypatch.setattr(
        scheduler, "_create_tracker", AsyncMock(return_value=Upstream("channels", releases))
    )
    monkeypatch.setattr(storage.webhooks, "last_source_run_at", AsyncMock(return_value=None))
    monkeypatch.setattr("releasetracker.scheduler_manual_checks.MANUAL_CHECK_COOLDOWN_SECONDS", 0)
    await notifier(storage)
    original = scheduler._send_notifications
    monkeypatch.setattr(
        scheduler,
        "_send_notifications",
        AsyncMock(side_effect=RuntimeError("injected outbox error")),
    )
    await scheduler.check_tracker_now_v2("channels")
    assert await storage.get_tracker_current_releases(tracker.id) == []
    assert await rows(storage) == []
    monkeypatch.setattr(scheduler, "_send_notifications", original)
    await scheduler.check_tracker_now_v2("channels")
    assert len(await storage.get_tracker_current_releases(tracker.id)) == 1
    assert len(await rows(storage)) == 1
