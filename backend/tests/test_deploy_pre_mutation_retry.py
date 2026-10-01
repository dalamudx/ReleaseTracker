"""Automatic deployments retry only failures that happened before any mutation."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from releasetracker.config import ExecutorConfig
from releasetracker.executor_scheduler import ExecutorScheduler
from releasetracker.executor_trigger import enqueue_executor_binding_targets
from releasetracker.services.deploy_tasks import DeployTasks
from releasetracker.services.deployment_plan import TargetEvidence
from releasetracker.services.task_effects import MUTATION_GUARD
from releasetracker.services.task_queue import TaskQueue
from releasetracker.storage.sqlite_deployment_admission import DeploymentAdmissionStore
from helpers.executor_runtime import (
    create_runtime_connection,
    save_docker_tracker_config,
    seed_docker_release,
)

pytestmark = pytest.mark.asyncio


async def setup(storage, monkeypatch, *, mutate=False, error=None):
    await save_docker_tracker_config(storage, name="retry-deploy", image="acme/app")
    await seed_docker_release(storage, tracker_name="retry-deploy", version="1.0.0")
    tracker = await storage.get_aggregate_tracker("retry-deploy")
    executor_id = await storage.create_executor_config(
        ExecutorConfig(
            name="retry-deploy",
            tracker_name=tracker.name,
            tracker_source_id=tracker.sources[0].id,
            channel_name="stable",
            runtime_connection_id=await create_runtime_connection(storage),
            runtime_type="docker",
            target_ref={"mode": "container", "container_id": "app"},
            update_mode="immediate",
        )
    )
    executor = await storage.get_executor_config(executor_id)
    assert await enqueue_executor_binding_targets(storage, executor)
    scheduler = ExecutorScheduler(storage)
    handler = DeployTasks(storage, scheduler)
    evidence = TargetEvidence("runtime", "target", {"kind": "container"}, (), "container_config")
    monkeypatch.setattr(handler, "_collect_admission_evidence", AsyncMock(return_value=evidence))
    calls = []

    async def run(executor, **kwargs):
        calls.append(kwargs["run_id"])
        if mutate:
            await MUTATION_GUARD.get()()
        if error:
            raise error
        await storage.set_executor_run_status(kwargs["run_id"], "failed")
        return SimpleNamespace(status="failed")

    monkeypatch.setattr(scheduler, "_run_executor_with_overlap_guard", run)
    queue = TaskQueue(storage.tasks, MagicMock())
    queue.register("deploy", handler)
    return executor_id, handler, queue, calls


async def dispatch_and_run(storage, handler, queue, *, now=None):
    """One scheduler pass: dispatch due desired state, then run its new task."""
    db = await storage._get_connection()
    await db.execute("UPDATE executor_desired_state SET next_eligible_at=NULL")
    await db.commit()
    await handler.dispatch_pending()
    task = await storage.tasks.claim("deploy")
    if task is None:
        return None
    await queue._run(task)
    current = await storage.tasks.get(task["id"])
    if current["approval_pending"]:
        store = DeploymentAdmissionStore(storage)
        plan = await store.latest(task["id"])
        await store.approve(task["id"], plan["id"], plan["fingerprint"], "admin")
        task = await storage.tasks.claim("deploy")
        await queue._run(task)
        current = await storage.tasks.get(task["id"])
    return current


async def test_pre_mutation_failure_creates_bounded_fresh_retries(storage, monkeypatch):
    executor_id, handler, queue, calls = await setup(storage, monkeypatch)
    seen = []
    for _ in range(3):
        before = datetime.now()
        task = await dispatch_and_run(storage, handler, queue)
        assert task["state"] == "failed", task
        assert task["result"]["mutation_started"] is False
        seen.append(task["id"])
        state = await storage.get_executor_desired_state(executor_id)
        if len(seen) < 3:
            assert state.pending is True
            assert state.next_eligible_at >= before + timedelta(seconds=290)
    assert len(set(seen)) == 3 and len(calls) == 3
    assert (await storage.get_executor_desired_state(executor_id)).pending is False
    assert await dispatch_and_run(storage, handler, queue) is None


async def test_unexpected_error_before_mutation_is_also_retried(storage, monkeypatch):
    executor_id, handler, queue, _ = await setup(storage, monkeypatch, error=RuntimeError("api"))
    task = await dispatch_and_run(storage, handler, queue)
    assert task["state"] == "failed" and task["error_code"] == "deployment_interrupted"
    assert (await storage.get_executor_desired_state(executor_id)).pending is True


async def test_started_mutation_is_never_retried(storage, monkeypatch):
    executor_id, handler, queue, calls = await setup(storage, monkeypatch, mutate=True)
    task = await dispatch_and_run(storage, handler, queue)
    assert task["state"] == "needs_attention", task
    assert task["result"]["mutation_started"] is True
    assert (await storage.get_executor_desired_state(executor_id)).pending is False
    assert await dispatch_and_run(storage, handler, queue) is None
    assert len(calls) == 1


async def test_unreachable_target_stops_waiting_after_patience(storage, monkeypatch):
    from releasetracker.services import deploy_tasks

    executor_id, handler, queue, calls = await setup(storage, monkeypatch)
    handler._collect_admission_evidence = AsyncMock(side_effect=OSError("unreachable"))
    monkeypatch.setattr(deploy_tasks, "ADMISSION_EVIDENCE_PATIENCE", 3)
    await handler.dispatch_pending()
    states = []
    for _ in range(3):
        async with storage.tasks.transaction() as db:
            await db.execute("UPDATE tasks SET due_at=0")
        task = await storage.tasks.claim("deploy")
        await queue._run(task)
        current = await storage.tasks.get(task["id"])
        states.append((current["state"], current["error_code"]))
    assert states == [
        ("queued", "admission_evidence_unavailable"),
        ("queued", "admission_evidence_unavailable"),
        ("needs_attention", "admission_evidence_stalled"),
    ]
    assert current["attempts"] == 0 and calls == []
    # The operator now owns this target; automatic work stops instead of looping.
    assert (await storage.get_executor_desired_state(executor_id)).pending is False


async def test_manual_failure_is_not_retried(storage, monkeypatch):
    executor_id, handler, queue, calls = await setup(storage, monkeypatch)
    db = await storage._get_connection()
    await db.execute("UPDATE executor_desired_state SET pending=0")
    await db.commit()
    receipt = await handler.enqueue(executor_id, manual=True)
    task = await storage.tasks.claim("deploy")
    await queue._run(task)
    current = await storage.tasks.get(receipt["task_id"])
    if current["approval_pending"]:
        store = DeploymentAdmissionStore(storage)
        plan = await store.latest(current["id"])
        await store.approve(current["id"], plan["id"], plan["fingerprint"], "admin")
        await queue._run(await storage.tasks.claim("deploy"))
    assert (await storage.tasks.get(receipt["task_id"]))["state"] == "failed"
    assert await dispatch_and_run(storage, handler, queue) is None
    assert len(calls) == 1
