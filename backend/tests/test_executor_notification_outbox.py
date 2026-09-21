"""Outbox tests use only isolated SQLite and fictional notifier identities."""

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.notifiers.base import NotificationEvent as E
from releasetracker.notifiers.webhook import _build_webhook_payload
from releasetracker.notifiers.wecom import _build_wecom_markdown
from releasetracker.services.executor_notification_outbox import (
    ExecutorNotificationOutbox,
    sanitize_payload,
    select_notification_event,
)


def notifier(events):
    return SimpleNamespace(
        id=1,
        enabled=True,
        type="webhook",
        events=events,
        name="example notifier",
        url="https://notify.example.test/hook",
        language="en",
    )


def payload(**health):
    return dict(
        entity="executor_run",
        run_id=42,
        executor_name="service-a",
        status="success",
        health_check={
            "strategy": "runtime_native",
            "performed": True,
            "outcome": "healthy",
            "services": [
                {
                    "service": "service-a",
                    "status": "healthy",
                    "method": "runtime_native",
                    "message": "Runtime ready",
                }
            ],
            "duration_seconds": 60,
            "elapsed_seconds": 2.5,
            "native_health_absent": False,
            **health,
        },
    )


@pytest.mark.parametrize(
    "enabled,events,expected",
    [
        (False, [E.EXECUTOR_HEALTH_CHECK_RESULT], None),
        (True, [E.EXECUTOR_HEALTH_CHECK_RESULT], E.EXECUTOR_HEALTH_CHECK_RESULT),
        (True, [E.EXECUTOR_HEALTH_CHECK_RESULT, E.EXECUTOR_RUN_SUCCESS], E.EXECUTOR_RUN_SUCCESS),
        (False, [E.EXECUTOR_RUN_SUCCESS], E.EXECUTOR_RUN_SUCCESS),
    ],
)
def test_selection(enabled, events, expected):
    assert select_notification_event(notifier(events), payload(), enabled) == expected


@pytest.mark.parametrize(
    "health",
    [
        None,
        {},
        {"strategy": "none", "outcome": "healthy"},
        {"strategy": "http", "outcome": "skipped"},
    ],
)
def test_no_health_event_without_performed_check(health):
    data = payload()
    data["health_check"] = health
    assert select_notification_event(notifier([E.EXECUTOR_HEALTH_CHECK_RESULT]), data, True) is None


def test_timeout_rendering_and_sanitization():
    data = payload()
    data["health_check"].update(
        outcome="unhealthy",
        last_error="attempt timeout secret=fictional",
        probe_diagnostics={"body": "fictional-secret", "authorization": "Bearer fictional"},
    )
    data["message"] = "password=fictional-secret"
    safe = sanitize_payload(data)
    assert "fictional" not in json.dumps(safe)
    rendered = _build_webhook_payload(E.EXECUTOR_HEALTH_CHECK_RESULT, safe)
    assert "timeout" in rendered["text"]
    assert "business health not verified" in rendered["text"]
    assert "timeout" in _build_wecom_markdown(rendered)
    assert (
        "超时"
        in _build_webhook_payload(E.EXECUTOR_HEALTH_CHECK_RESULT, safe, language="zh")["text"]
    )


def test_zero_attempts_not_a_health_result():
    data = payload()
    data["health_check"].pop("performed")
    data["health_check"].update(attempt_count=0, outcome="error")
    assert select_notification_event(notifier([E.EXECUTOR_HEALTH_CHECK_RESULT]), data, True) is None


def test_native_health_absence_not_business_healthy():
    data = payload(native_health_absent=True)
    data["health_check"]["services"][0]["method"] = "runtime_state"
    safe = sanitize_payload(data)
    rendered = _build_webhook_payload(E.EXECUTOR_RUN_SUCCESS, safe)
    assert "native health check absent" in rendered["text"]
    assert "business health not verified" in rendered["text"]


@pytest.mark.parametrize("outcome", ["unhealthy", "unknown", "error"])
def test_health_outcome_remains_readable(outcome):
    data = payload()
    data["health_check"]["outcome"] = outcome
    rendered = _build_webhook_payload(E.EXECUTOR_HEALTH_CHECK_RESULT, sanitize_payload(data))
    assert ("probe error" if outcome == "error" else outcome) in rendered["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("notify_result", [False, True])
async def test_lifecycle_routes_snapshot_policy_to_outbox(notify_result):
    from datetime import datetime, timezone
    from releasetracker.executor_scheduler_run_lifecycle import ExecutorSchedulerRunLifecycle

    scheduler = ExecutorSchedulerRunLifecycle()
    scheduler._system_timezone = "UTC"
    scheduler._now_provider = lambda: datetime.now(timezone.utc)
    scheduler.notification_outbox = SimpleNamespace(enqueue=AsyncMock())
    scheduler.storage = SimpleNamespace(
        finalize_executor_run=AsyncMock(),
        get_executor_run=AsyncMock(
            return_value=SimpleNamespace(
                started_at=datetime.now(timezone.utc),
                diagnostics={"health_check": payload()["health_check"]},
            )
        ),
    )
    executor = SimpleNamespace(
        id=7,
        name="service-a",
        tracker_name="example tracker",
        tracker_source_id=1,
        runtime_type="docker",
        target_ref={"mode": "container"},
        health_check=SimpleNamespace(notify_result=notify_result),
    )
    await scheduler._finalize_run(
        executor,
        run_id=42,
        status="success",
        from_version="1.0.0",
        to_version="1.1.0",
        message="updated",
        last_error=None,
        diagnostics={"health_check": payload()["health_check"]},
    )
    intent = scheduler.storage.finalize_executor_run.await_args.kwargs["notification_intent"]
    assert intent["notify_health_result"] is notify_result
    assert intent["payload"]["health_check"]["outcome"] == "healthy"
    assert intent["payload"]["run_id"] == 42
    scheduler.storage.get_executor_run.assert_not_awaited()
    scheduler.notification_outbox.enqueue.assert_not_awaited()


@pytest.mark.parametrize(
    "outcome", ["healthy", "unhealthy", "timeout", "unknown", "unsupported", "superseded"]
)
def test_exact_observer_shape_survives_sanitizing_and_rendering(outcome):
    data = payload(outcome=outcome)
    data["health_check"]["services"][0]["status"] = outcome
    safe = sanitize_payload(data)
    health = safe["health_check"]
    assert health["performed"] is True
    assert health["outcome"] == outcome
    assert health["services"] == [
        {"service": "service-a", "status": outcome, "method": "runtime_native"}
    ]
    assert health["service_count"] == 1
    assert health["duration_seconds"] == 60
    assert health["elapsed_seconds"] == 2.5
    assert "attempt_count" not in health
    assert sanitize_payload(safe) == safe
    rendered = _build_webhook_payload(E.EXECUTOR_HEALTH_CHECK_RESULT, safe)
    text = rendered["text"]
    assert "0 attempts" not in text
    assert "2.5s" in text and "Services (1): service-a:" in text
    assert ("ready" if outcome == "healthy" else outcome) in text
    assert "service-a:" in _build_wecom_markdown(rendered)
    if outcome in {"unsupported", "superseded"}:
        translated = _build_webhook_payload(E.EXECUTOR_HEALTH_CHECK_RESULT, safe, language="zh")
        assert ("不支持" if outcome == "unsupported" else "已被后续变更取代") in translated["text"]
        data["health_check"]["last_error"] = "previous timeout"
        assert sanitize_payload(data)["health_check"]["outcome"] == outcome


def test_service_allowlist_drops_freeform_secrets_and_bounds_chat_summary():
    services = [
        {
            "service": f"service-{i}",
            "status": "pending",
            "method": "kubernetes_rollout",
            "message": "token=fictional-sensitive",
            "body": "fictional-sensitive",
        }
        for i in range(7)
    ]
    services.append(
        {
            "service": "https://user:fictional-sensitive@example.test",
            "status": "fictional-sensitive",
            "method": "fictional-sensitive",
        }
    )
    safe = sanitize_payload(payload(services=services))
    assert "fictional-sensitive" not in json.dumps(safe)
    assert safe["health_check"]["service_count"] == 8
    assert safe["health_check"]["services"][-1] == {
        "service": "[redacted service]",
        "status": "unknown",
        "method": "unknown",
    }
    assert sanitize_payload(safe) == safe
    text = _build_webhook_payload(E.EXECUTOR_RUN_SUCCESS, safe)["text"]
    assert "Services (8)" in text and "+3" in text
    assert "service-4:" in text and "service-5:" not in text


def test_explicit_performed_flag_is_authoritative():
    assert "health_check" not in sanitize_payload(payload(performed=False))
    assert "health_check" in sanitize_payload(payload(attempt_count=0))


@pytest.mark.asyncio
async def test_delivery_exception_does_not_change_deployment_or_block_next_event(
    tmp_path, monkeypatch
):
    outbox, _, clock = await make_outbox(
        tmp_path, [E.EXECUTOR_RUN_SUCCESS, E.EXECUTOR_HEALTH_CHECK_RESULT]
    )
    delivery = AsyncMock(side_effect=[RuntimeError("fictional transport failure"), True, True])
    monkeypatch.setattr(
        "releasetracker.services.executor_notification_outbox.build_notifier",
        lambda **kw: SimpleNamespace(notify=delivery),
    )
    data = payload(outcome="unhealthy")
    try:
        await outbox.enqueue(data, notify_health_result=True)
        await outbox.enqueue({**payload(), "run_id": 43}, notify_health_result=True)
        assert len(await rows(outbox)) == 2  # One combined event per run, not two.
        await outbox.deliver_one()
        await outbox.deliver_one()
        first, second = await rows(outbox)
        assert first["status"] == "pending" and second["status"] == "delivered"
        stored = json.loads(first["payload"])
        assert stored["status"] == data["status"] == "success"
        assert stored["health_check"]["outcome"] == "unhealthy"
        assert first["event"] == E.EXECUTOR_RUN_SUCCESS
        clock[0] += 30
        await outbox.deliver_one()
        assert all(row["status"] == "delivered" for row in await rows(outbox))
        assert delivery.await_count == 3
    finally:
        await outbox.shutdown()


async def make_outbox(tmp_path, events):
    path = tmp_path / "isolated.db"
    sql = (
        Path(__file__).parents[1]
        / "dbmate/migrations/20260920000002_executor_notification_outbox.sql"
    ).read_text()
    with sqlite3.connect(path) as db:
        db.executescript(sql.split("-- migrate:down")[0])
        intents = (
            Path(__file__).parents[1]
            / "dbmate/migrations/20260920000003_executor_notification_intents.sql"
        ).read_text()
        db.executescript(intents.split("-- migrate:down")[0])
    item = notifier(events)
    storage = SimpleNamespace(
        db_path=str(path),
        get_notifiers=AsyncMock(return_value=[item]),
        get_notifier=AsyncMock(return_value=item),
    )
    clock = [1000.0]
    outbox = ExecutorNotificationOutbox(storage, clock=lambda: clock[0])
    await outbox.initialize()
    return outbox, item, clock


@pytest.mark.asyncio
async def test_health_toggle_and_worker_tick(tmp_path, monkeypatch):
    import asyncio

    outbox, _, _ = await make_outbox(tmp_path, [E.EXECUTOR_HEALTH_CHECK_RESULT])
    entered, release = asyncio.Event(), asyncio.Event()

    async def deliver(event, data):
        entered.set()
        await release.wait()
        return True

    monkeypatch.setattr(
        "releasetracker.services.executor_notification_outbox.build_notifier",
        lambda **kw: SimpleNamespace(notify=deliver),
    )
    try:
        await outbox.enqueue(payload(), notify_health_result=False)
        assert await rows(outbox) == []
        await outbox.enqueue(payload(), notify_health_result=True)
        assert (await rows(outbox))[0]["event"] == E.EXECUTOR_HEALTH_CHECK_RESULT
        await asyncio.wait_for(outbox.tick(), timeout=1)
        await asyncio.wait_for(entered.wait(), timeout=1)
        worker = outbox._worker
        await outbox.tick()
        assert worker is outbox._worker
        release.set()
        await worker
        assert (await rows(outbox))[0]["status"] == "delivered"
    finally:
        release.set()
        await outbox.shutdown()


async def rows(outbox):
    cursor = await outbox._db.execute("SELECT * FROM executor_notification_outbox")
    return [dict(row) for row in await cursor.fetchall()]


@pytest.mark.asyncio
async def test_dedupe_retry_current_credentials_and_restart(tmp_path, monkeypatch):
    outbox, item, clock = await make_outbox(
        tmp_path, [E.EXECUTOR_RUN_SUCCESS, E.EXECUTOR_HEALTH_CHECK_RESULT]
    )
    delivery = AsyncMock(side_effect=[False, True])
    builds = []

    def build(**kwargs):
        builds.append(kwargs)
        return SimpleNamespace(notify=delivery)

    monkeypatch.setattr(
        "releasetracker.services.executor_notification_outbox.build_notifier", build
    )
    try:
        await outbox.enqueue(payload(), notify_health_result=True)
        await outbox.enqueue(payload(), notify_health_result=True)
        assert len(await rows(outbox)) == 1
        assert await outbox.deliver_one()
        assert (await rows(outbox))[0]["status"] == "pending"
        assert not await outbox.deliver_one()
        item.url = "https://notify.example.test/rotated"
        storage = outbox.storage
        await outbox.shutdown()
        outbox = ExecutorNotificationOutbox(storage, clock=lambda: clock[0])
        await outbox.initialize()
        clock[0] += 30
        assert await outbox.deliver_one()
        row = (await rows(outbox))[0]
        assert row["status"] == "delivered" and row["attempts"] == 2
        assert row["event"] == E.EXECUTOR_RUN_SUCCESS
        assert builds[-1]["url"].endswith("/rotated")
        assert delivery.await_count == 2
        # Storage has no deployment API: retries cannot re-run an executor.
    finally:
        await outbox.shutdown()


@pytest.mark.asyncio
async def test_abandoned_send_recovery_and_bounded_retries(tmp_path, monkeypatch):
    outbox, _, clock = await make_outbox(tmp_path, [E.EXECUTOR_RUN_SUCCESS])
    monkeypatch.setattr(
        "releasetracker.services.executor_notification_outbox.build_notifier",
        lambda **kw: SimpleNamespace(notify=AsyncMock(return_value=False)),
    )
    try:
        await outbox.enqueue(payload())
        await outbox._db.execute(
            "UPDATE executor_notification_outbox SET status='sending', attempts=1"
        )
        await outbox._db.commit()
        storage = outbox.storage
        await outbox.shutdown()
        outbox = ExecutorNotificationOutbox(storage, clock=lambda: clock[0])
        await outbox.initialize()
        assert (await rows(outbox))[0]["status"] == "pending"
        for _ in range(3):
            await outbox.deliver_one()
            clock[0] += 1000
        assert (await rows(outbox))[0]["status"] == "failed"
        assert (await rows(outbox))[0]["attempts"] == 4
        assert not await outbox.deliver_one()
    finally:
        await outbox.shutdown()


@pytest.mark.asyncio
async def test_disabled_notifier_discarded_and_plain_deployment_queued(tmp_path):
    outbox, item, _ = await make_outbox(tmp_path, [E.EXECUTOR_RUN_SUCCESS])
    try:
        data = payload()
        del data["health_check"]
        await outbox.enqueue(data)
        assert len(await rows(outbox)) == 1
        item.enabled = False
        await outbox.deliver_one()
        assert (await rows(outbox))[0]["status"] == "discarded"
    finally:
        await outbox.shutdown()
