"""Crash boundaries use migrated temporary SQLite and fictional identities only."""

import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from helpers.executor_runtime import (
    create_runtime_connection,
    save_docker_tracker_config,
    seed_docker_release,
)
from releasetracker.config import ExecutorConfig
from releasetracker.executor_scheduler import ExecutorScheduler
from releasetracker.models import ExecutorStatus
from releasetracker.services.executor_notification_outbox import ExecutorNotificationOutbox

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def deployment(storage):
    await save_docker_tracker_config(storage, name="example-tracker", image="example/service-a")
    await seed_docker_release(storage, tracker_name="example-tracker", version="2.0.0")
    tracker = await storage.get_aggregate_tracker("example-tracker")
    connection = await create_runtime_connection(storage)
    executor_id = await storage.create_executor_config(
        ExecutorConfig(
            name="Example executor",
            tracker_name=tracker.name,
            tracker_source_id=tracker.sources[0].id,
            channel_name="stable",
            runtime_connection_id=connection,
            runtime_type="docker",
            target_ref={"mode": "container", "container_id": "example-container"},
            health_check={"notify_result": True, "readiness_enabled": False},
        )
    )
    executor = await storage.get_executor_config(executor_id)
    scheduler = ExecutorScheduler(storage)
    run_id = await scheduler._claim_executor_run(executor_id, trigger="manual")
    await storage.set_executor_run_status(run_id, "running")
    return scheduler, executor, run_id


async def finish(deployment, status="success", *, health=False):
    scheduler, executor, run_id = deployment
    return await scheduler._finalize_run(
        executor,
        run_id,
        status=status,
        from_version="registry.example.test/team/service-a:1.0.0",
        to_version="registry.example.test/team/service-a:2.0.0",
        message="token=fixture-secret",
        last_error=None,
        diagnostics=(
            {
                "health_check": {
                    "strategy": "runtime_native",
                    "outcome": "healthy",
                    "performed": True,
                    "last_error": "token=fixture-secret",
                }
            }
            if health
            else None
        ),
    )


async def rows(storage, table):
    assert table in {"executor_notification_intents", "executor_notification_outbox"}
    db = await storage._get_connection()
    return [
        dict(row)
        for row in await (await db.execute(f"SELECT * FROM {table} ORDER BY id")).fetchall()
    ]


async def channel(storage, name="channel-a", events=None):
    return await storage.create_notifier(
        {
            "name": name,
            "type": "webhook",
            "url": "https://hooks.example.test/notify",
            "enabled": True,
            "language": "en",
            "events": events or ["executor_run_success"],
        }
    )


@pytest.mark.parametrize("status", ["success", "failed", "skipped"])
async def test_finalize_does_not_read_runs_channels_or_send(
    storage, deployment, monkeypatch, status
):
    scheduler, executor, run_id = deployment
    scheduler.notification_outbox = AsyncMock()
    scheduler.notification_outbox.enqueue.side_effect = RuntimeError("offline")
    original_get_run = storage.get_executor_run
    monkeypatch.setattr(storage, "get_executor_run", AsyncMock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr(storage, "get_notifiers", AsyncMock(side_effect=RuntimeError("offline")))
    outcome = await finish(deployment, status)
    assert outcome.status == status
    assert (await original_get_run(run_id)).status == status
    assert (await storage.get_executor_status(executor.id)).last_result == status
    intent = (await rows(storage, "executor_notification_intents"))[0]
    assert intent["status"] == "pending" and intent["notify_health_result"] == 1
    assert "fixture-secret" not in intent["payload"]
    assert json.loads(intent["payload"])["started_at"].endswith("Z")
    storage.get_executor_run.assert_not_awaited()
    storage.get_notifiers.assert_not_awaited()
    scheduler.notification_outbox.enqueue.assert_not_awaited()


async def test_failed_intent_insert_rolls_back_result_and_status(storage, deployment):
    _, executor, run_id = deployment
    await storage.update_executor_status(
        ExecutorStatus(executor_id=executor.id, last_result="skipped")
    )
    db = await storage._get_connection()
    await db.execute(
        "CREATE TRIGGER reject_intent BEFORE INSERT ON executor_notification_intents BEGIN SELECT RAISE(ABORT,'simulated failure'); END"
    )
    await db.commit()
    with pytest.raises(Exception, match="simulated failure"):
        await finish(deployment)
    assert (await storage.get_executor_run(run_id)).status == "running"
    assert (await storage.get_executor_status(executor.id)).last_result == "skipped"
    assert not await rows(storage, "executor_notification_intents")
    await db.execute("DROP TRIGGER reject_intent")
    await db.commit()
    await finish(deployment)
    assert (await storage.get_executor_run(run_id)).status == "success"
    assert len(await rows(storage, "executor_notification_intents")) == 1


async def test_restart_after_result_commit_and_after_fanout_is_idempotent(
    storage, deployment, monkeypatch
):
    await channel(storage, events=["executor_run_success", "executor_health_check_result"])
    delivery = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "releasetracker.services.executor_notification_outbox.build_notifier",
        lambda **kw: type("Notifier", (), {"notify": delivery})(),
    )
    await finish(deployment, health=True)  # Process may stop here; no worker has been started.
    outbox = ExecutorNotificationOutbox(storage)
    await outbox.initialize()
    try:
        assert await outbox.expand_one()
        assert len(await rows(storage, "executor_notification_outbox")) == 1
        await finish(deployment, health=True)  # Replayed finalization must not repeat delivery.
    finally:
        await outbox.shutdown()
    restarted = ExecutorNotificationOutbox(storage)
    await restarted.initialize()
    try:
        assert not await restarted.expand_one()
        assert await restarted.deliver_one()
        assert not await restarted.deliver_one()
        assert delivery.await_count == 1
        assert delivery.await_args.args[0] == "executor_run_success"
    finally:
        await restarted.shutdown()


async def test_partial_fanout_rolls_back_and_retries_after_restart(storage, deployment, caplog):
    await channel(storage)
    second = await channel(storage, "channel-b")
    await finish(deployment)
    clock = [time.time() + 1]
    db = await storage._get_connection()
    await db.execute(
        f"CREATE TRIGGER reject_second BEFORE INSERT ON executor_notification_outbox WHEN NEW.notifier_id={int(second.id)} BEGIN SELECT RAISE(ABORT,'token=fixture-secret'); END"
    )
    await db.commit()
    outbox = ExecutorNotificationOutbox(storage, clock=lambda: clock[0])
    await outbox.initialize()
    try:
        assert await outbox.expand_one()
        assert not await rows(storage, "executor_notification_outbox")
        intent = (await rows(storage, "executor_notification_intents"))[0]
        assert intent["status"] == "pending" and intent["attempts"] == 1
        assert intent["available_at"] == clock[0] + 30
        assert (await storage.get_executor_run(deployment[2])).status == "success"
    finally:
        await outbox.shutdown()
    await db.execute("DROP TRIGGER reject_second")
    await db.commit()
    restarted = ExecutorNotificationOutbox(storage, clock=lambda: clock[0])
    await restarted.initialize()
    try:
        assert not await restarted.expand_one()
        clock[0] += 30
        assert await restarted.expand_one()
        assert len(await rows(storage, "executor_notification_outbox")) == 2
        assert not await restarted.expand_one()
    finally:
        await restarted.shutdown()
    assert "fixture-secret" not in caplog.text


async def test_cancellation_mid_fanout_rolls_back(storage, deployment, monkeypatch):
    await channel(storage)
    await finish(deployment)
    outbox = ExecutorNotificationOutbox(storage)
    await outbox.initialize()
    try:
        commit = outbox._db.commit
        monkeypatch.setattr(outbox._db, "commit", AsyncMock(side_effect=asyncio.CancelledError()))
        with pytest.raises(asyncio.CancelledError):
            await outbox.expand_one()
        assert not await rows(storage, "executor_notification_outbox")
        assert (await rows(storage, "executor_notification_intents"))[0]["status"] == "pending"
        monkeypatch.setattr(outbox._db, "commit", commit)
        assert await outbox.expand_one()
        assert len(await rows(storage, "executor_notification_outbox")) == 1
    finally:
        await outbox.shutdown()


async def test_channel_lookup_failure_defers_without_poisoning_existing_delivery(
    storage, deployment, monkeypatch, caplog
):
    await channel(storage)
    await finish(deployment)
    delivery = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "releasetracker.services.executor_notification_outbox.build_notifier",
        lambda **kw: type("Notifier", (), {"notify": delivery})(),
    )
    outbox = ExecutorNotificationOutbox(storage)
    await outbox.initialize()
    try:
        await outbox.enqueue({"run_id": 999, "status": "success"})
        monkeypatch.setattr(
            storage, "get_notifiers", AsyncMock(side_effect=RuntimeError("token=fixture-secret"))
        )
        await outbox._work()
        assert (await rows(storage, "executor_notification_intents"))[0]["attempts"] == 1
        assert (await rows(storage, "executor_notification_outbox"))[0]["status"] == "delivered"
        assert (await storage.get_executor_run(deployment[2])).status == "success"
    finally:
        await outbox.shutdown()
    assert "fixture-secret" not in caplog.text


async def test_intent_survives_history_clear_and_freezes_health_switch(storage, deployment):
    await channel(storage, events=["executor_health_check_result"])
    await finish(deployment, health=True)
    deployment[1].health_check.notify_result = False
    await finish(deployment, health=True)
    db = await storage._get_connection()
    await db.execute("DELETE FROM executor_run_history WHERE id=?", (deployment[2],))
    await db.commit()
    outbox = ExecutorNotificationOutbox(storage)
    await outbox.initialize()
    try:
        assert await outbox.expand_one()
        deliveries = await rows(storage, "executor_notification_outbox")
        assert len(deliveries) == 1 and deliveries[0]["event"] == "executor_health_check_result"
    finally:
        await outbox.shutdown()


async def test_no_subscribed_channels_consumes_intent_once(storage, deployment):
    await finish(deployment)
    outbox = ExecutorNotificationOutbox(storage)
    await outbox.initialize()
    try:
        assert await outbox.expand_one()
        assert not await rows(storage, "executor_notification_outbox")
        await channel(storage)
        assert not await outbox.expand_one()
    finally:
        await outbox.shutdown()


async def test_notification_failure_never_retries_successful_queued_deployment(
    storage, deployment, monkeypatch
):
    from unittest.mock import MagicMock
    from test_executor_scheduler import FakeAdapter
    from releasetracker.services.deploy_tasks import DeployTasks
    from releasetracker.services.task_queue import TaskQueue

    scheduler, executor, _ = deployment
    await finish(deployment, "skipped")
    adapter = FakeAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id),
        current_image="example/service-a:1.0.0",
        storage=storage,
        executor_id=executor.id,
    )
    adapter.update_image = AsyncMock(wraps=adapter.update_image)
    scheduler._adapters[executor.id] = adapter
    handler = DeployTasks(storage, scheduler)
    scheduler.deploy_tasks = handler
    receipt = await handler.enqueue(executor.id, manual=True)
    queue = TaskQueue(storage.tasks, MagicMock())
    queue.register("deploy", handler)
    monkeypatch.setattr(
        storage, "get_notifiers", AsyncMock(side_effect=RuntimeError("channel lookup unavailable"))
    )
    claimed = await storage.tasks.claim("deploy")
    await queue._run(claimed)
    task = await storage.tasks.get(receipt["task_id"])
    assert task["state"] == "succeeded" and task["attempts"] == 1
    adapter.update_image.assert_awaited_once()
    outbox = ExecutorNotificationOutbox(storage)
    await outbox.initialize()
    try:
        await outbox._work()
        await outbox._work()
        assert (await storage.tasks.get(task["id"]))["state"] == "succeeded"
        assert (await storage.get_executor_run(task["result"]["run_id"])).status == "success"
        adapter.update_image.assert_awaited_once()
    finally:
        await outbox.shutdown()
