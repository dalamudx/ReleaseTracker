import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from releasetracker.config import ExecutorConfig
from releasetracker.executor_scheduler import ExecutorScheduler
from releasetracker.services.deployment_readiness import DeploymentReadiness
from releasetracker.services.deploy_tasks import DeployTasks
from releasetracker.services.task_queue import TaskQueue
from helpers.executor_runtime import (
    create_runtime_connection,
    save_docker_tracker_config,
    seed_docker_release,
)

pytestmark = pytest.mark.asyncio


async def setup(storage, monkeypatch, *, stable=10, timeout=30):
    await save_docker_tracker_config(storage, name="readiness-example", image="example/service-a")
    await seed_docker_release(storage, tracker_name="readiness-example", version="2.0.0")
    tracker = await storage.get_aggregate_tracker("readiness-example")
    connection_id = await create_runtime_connection(storage)
    executor_id = await storage.create_executor_config(
        ExecutorConfig(
            name="Example deployment",
            tracker_name=tracker.name,
            tracker_source_id=tracker.sources[0].id,
            channel_name="stable",
            runtime_connection_id=connection_id,
            runtime_type="docker",
            target_ref={"mode": "container", "container_id": "example-container"},
            health_check={
                "use_system_readiness_defaults": False,
                "readiness_stable_seconds": stable,
                "readiness_timeout_seconds": timeout,
            },
        )
    )
    executor = await storage.get_executor_config(executor_id)
    scheduler = ExecutorScheduler(storage)
    handler = DeployTasks(storage, scheduler)
    scheduler.deploy_tasks = handler
    now = [time.time()]
    probe = AsyncMock(
        return_value={
            "outcome": "healthy",
            "services": [{"service": "service-a", "status": "healthy", "method": "runtime_native"}],
        }
    )
    observer = DeploymentReadiness(
        storage, scheduler, MagicMock(), probe=probe, clock=lambda: now[0]
    )
    scheduler.readiness = observer
    monkeypatch.setattr(
        "releasetracker.services.deployment_readiness_probes.capture_deployment_target",
        AsyncMock(return_value={"kind": "container"}),
    )
    receipt = await handler.enqueue(executor.id, manual=True)
    task = await storage.tasks.claim("deploy")
    await storage.tasks.start_attempt(task)
    run_id = await scheduler._claim_executor_run(executor.id, trigger="manual")
    await storage.tasks.checkpoint(task, {"run_id": run_id, "mutation_started": True})
    await observer.handoff(
        task,
        executor,
        run_id,
        {"kind": "container"},
        status="success",
        from_version="registry.example.test/team/service-a:1.0.0",
        to_version="registry.example.test/team/service-a:2.0.0",
        message="updated",
        last_error=None,
        diagnostics={"services": []},
    )
    assert receipt["task_id"] == task["id"]
    return executor, scheduler, observer, probe, now, task, run_id


async def intent_count(storage):
    db = await storage._get_connection()
    return (
        await (await db.execute("SELECT COUNT(*) FROM executor_notification_intents")).fetchone()
    )[0]


async def observe(storage, observer, task_id):
    db = await storage._get_connection()
    row = await (
        await db.execute("SELECT * FROM deployment_observations WHERE task_id=?", (task_id,))
    ).fetchone()
    await observer._observe(dict(row))


async def test_handoff_preserves_identity_and_releases_worker(storage, monkeypatch):
    executor, scheduler, observer, probe, now, task, run_id = await setup(storage, monkeypatch)
    before = await intent_count(storage)
    saved = await storage.tasks.get(task["id"])
    assert saved["state"] == "running"
    assert saved["owner"] is None
    assert saved["attempts"] == 1
    assert saved["result"]["phase"] == "health_checking"
    run = await storage.get_executor_run(run_id)
    assert run.status == "health_checking" and run.finished_at is None
    assert await intent_count(storage) == before
    assert await observer.conflicts(executor)
    await storage.tasks.recover(startup=True)
    assert (await storage.get_executor_run(run_id)).status == "health_checking"
    await observe(storage, observer, task["id"])
    assert (await storage.tasks.get(task["id"]))["state"] == "running"
    now[0] += 10
    await observe(storage, observer, task["id"])
    saved = await storage.tasks.get(task["id"])
    assert saved["state"] == "succeeded" and saved["attempts"] == 1
    assert (await storage.get_executor_run(run_id)).status == "success"
    assert await intent_count(storage) == before + 1
    assert not await observer.conflicts(executor)


@pytest.mark.parametrize(
    "outcome,expected", [("pending", "failed"), ("unknown", "needs_attention")]
)
async def test_deadline_is_not_a_deployment_retry(storage, monkeypatch, outcome, expected):
    executor, scheduler, observer, probe, now, task, run_id = await setup(storage, monkeypatch)
    probe.return_value = {"outcome": outcome, "services": []}
    await observe(storage, observer, task["id"])
    now[0] += 31
    await observe(storage, observer, task["id"])
    saved = await storage.tasks.get(task["id"])
    assert saved["state"] == expected
    assert saved["attempts"] == 1
    assert probe.await_count == 1
    assert (await storage.get_executor_run(run_id)).status == "failed"
    await storage.tasks.recover(startup=True)
    assert (await storage.tasks.get(task["id"]))["state"] == expected


async def test_stability_resets_after_readiness_regression(storage, monkeypatch):
    _, _, observer, probe, now, task, _ = await setup(storage, monkeypatch)
    await observe(storage, observer, task["id"])
    now[0] += 5
    probe.return_value = {"outcome": "pending", "services": []}
    await observe(storage, observer, task["id"])
    now[0] += 5
    probe.return_value = {"outcome": "healthy", "services": []}
    await observe(storage, observer, task["id"])
    assert (await storage.tasks.get(task["id"]))["state"] == "running"
    now[0] += 10
    await observe(storage, observer, task["id"])
    assert (await storage.tasks.get(task["id"]))["state"] == "succeeded"


async def test_observer_tick_does_not_wait_on_io(storage, monkeypatch):
    _, _, observer, probe, _, task, _ = await setup(storage, monkeypatch)
    gate = asyncio.Event()

    async def suspended(*args):
        await gate.wait()
        return {"outcome": "pending", "services": []}

    probe.side_effect = suspended
    await asyncio.wait_for(observer.tick(), 0.5)
    assert len(observer.workers) == 1
    assert (await storage.tasks.get(task["id"]))["state"] == "running"
    gate.set()
    await observer.shutdown()


async def test_health_policy_snapshot_not_changed_by_system_defaults(storage, monkeypatch):
    executor, scheduler, _, _, _, task, _ = await setup(storage, monkeypatch)
    before = task["payload"]["health_check"]
    await storage.set_setting("system.readiness_timeout_seconds", "900")
    assert (await storage.tasks.get(task["id"]))["payload"]["health_check"] == before
    changed = executor.model_copy(
        update={"health_check": executor.health_check.model_copy(update={"notify_result": True})}
    )
    assert await scheduler.deploy_tasks.identity(changed) == await scheduler.deploy_tasks.identity(
        executor
    )


async def test_durable_queue_path_defers_success_until_native_ready(storage, monkeypatch):
    from test_executor_scheduler import FakeAdapter

    executor, scheduler, observer, probe, now, first_task, _ = await setup(
        storage, monkeypatch, stable=0
    )
    await observe(storage, observer, first_task["id"])
    before = await intent_count(storage)
    scheduler._adapters[executor.id] = FakeAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id),
        current_image="example/service-a:1.0.0",
        storage=storage,
        executor_id=executor.id,
    )
    monkeypatch.setattr(
        "releasetracker.services.deployment_readiness_probes.capture_deployment_baseline",
        AsyncMock(return_value={"kind": "container"}),
    )
    receipt = await scheduler.deploy_tasks.enqueue(executor.id, manual=True)
    queue = TaskQueue(storage.tasks, MagicMock())
    queue.register("deploy", scheduler.deploy_tasks)
    task = await storage.tasks.claim("deploy")
    await queue._run(task)
    saved = await storage.tasks.get(receipt["task_id"])
    assert saved["state"] == "running", saved
    assert saved["result"]["phase"] == "health_checking"
    assert await intent_count(storage) == before
    await observe(storage, observer, task["id"])
    assert (await storage.tasks.get(task["id"]))["state"] == "succeeded"
    assert await intent_count(storage) == before + 1


async def test_readonly_recheck_preserves_original_failure(storage, monkeypatch):
    from releasetracker.services.recovery_tasks import RecoveryTasks

    executor, scheduler, observer, probe, now, task, run_id = await setup(
        storage, monkeypatch, stable=0
    )
    probe.return_value = {"outcome": "unknown", "services": []}
    await observe(storage, observer, task["id"])
    now[0] += 31
    await observe(storage, observer, task["id"])
    original = await storage.get_executor_run(run_id)
    assert original.status == "failed"
    receipt = await observer.enqueue_recheck(await storage.tasks.get(task["id"]))
    duplicate = await observer.enqueue_recheck(await storage.tasks.get(task["id"]))
    assert duplicate["task_id"] == receipt["task_id"]
    queue = TaskQueue(storage.tasks, MagicMock())
    queue.register("recover", RecoveryTasks(storage, scheduler))
    recheck = await storage.tasks.claim("recover", now=now[0])
    probe.return_value = {"outcome": "healthy", "services": []}
    scheduler._run_executor_with_overlap_guard = AsyncMock(
        side_effect=AssertionError("must not deploy")
    )
    await queue._run(recheck)
    assert (await storage.tasks.get(recheck["id"]))["state"] == "running"
    await observe(storage, observer, recheck["id"])
    updated = await storage.get_executor_run(run_id)
    assert updated.status == "failed"
    assert updated.finished_at == original.finished_at
    assert updated.diagnostics["health_check"]["outcome"] == "unknown"
    assert updated.diagnostics["health_recheck"]["outcome"] == "healthy"
    assert len(updated.diagnostics["health_rechecks"]) == 1
    assert (await storage.tasks.get(recheck["id"]))["state"] == "succeeded"
    assert (await storage.tasks.get(task["id"]))["error_code"] == "readiness_reconciled"
    assert not await observer.conflicts(executor)
    scheduler._run_executor_with_overlap_guard.assert_not_awaited()


async def test_waiting_observation_blocks_delete_until_terminal(storage, monkeypatch):
    executor, _, observer, _, _, task, _ = await setup(storage, monkeypatch, stable=0)
    assert not await storage.delete_executor_config(executor.id)
    await observe(storage, observer, task["id"])
    assert await storage.delete_executor_config(executor.id)


async def test_observer_rejects_changed_target_before_remote_probe(storage, monkeypatch):
    executor, _, observer, probe, _, task, _ = await setup(storage, monkeypatch)
    db = await storage._get_connection()
    await db.execute(
        "UPDATE executors SET name='Changed example target' WHERE id=?", (executor.id,)
    )
    await db.commit()
    await observe(storage, observer, task["id"])
    probe.assert_not_awaited()
    assert (await storage.tasks.get(task["id"]))["state"] == "needs_attention"


async def test_failed_finalization_is_resumed_without_probe_or_deployment(storage, monkeypatch):
    _, scheduler, observer, probe, _, task, run_id = await setup(storage, monkeypatch, stable=0)
    finalize = storage.finalize_executor_run
    monkeypatch.setattr(
        storage,
        "finalize_executor_run",
        AsyncMock(side_effect=RuntimeError("temporary transaction failure")),
    )
    with pytest.raises(RuntimeError):
        await observe(storage, observer, task["id"])
    assert (await storage.tasks.get(task["id"]))["state"] == "running"
    await storage.tasks.recover(startup=True)
    monkeypatch.setattr(storage, "finalize_executor_run", finalize)
    await observe(storage, observer, task["id"])
    assert probe.await_count == 1
    assert (await storage.tasks.get(task["id"]))["state"] == "succeeded"
    assert (await storage.get_executor_run(run_id)).status == "success"


async def test_prewrite_baseline_failure_prevents_mutation(storage, monkeypatch):
    from releasetracker.services.task_effects import mark_deployment_mutation
    from releasetracker.executor_scheduler_run_lifecycle import ExecutorRunOutcome

    executor, scheduler, observer, _, _, prior, _ = await setup(storage, monkeypatch, stable=0)
    await observe(storage, observer, prior["id"])
    handler = scheduler.deploy_tasks
    receipt = await handler.enqueue(executor.id, manual=True)
    task = await storage.tasks.claim("deploy")
    await storage.tasks.start_attempt(task)
    mutated = AsyncMock()

    async def execute(config, **kwargs):
        try:
            await mark_deployment_mutation()
        except ValueError:
            return ExecutorRunOutcome("failed", None, None, "readiness_baseline_unavailable")
        await mutated()

    scheduler._run_executor_with_overlap_guard = execute
    monkeypatch.setattr(
        "releasetracker.services.deployment_readiness_probes.capture_deployment_baseline",
        AsyncMock(return_value={"error": "native identity unavailable"}),
    )
    result = await handler.execute(task)
    assert result.state == "failed"
    assert not (await storage.tasks.get(receipt["task_id"]))["result"].get("mutation_started")
    mutated.assert_not_awaited()
