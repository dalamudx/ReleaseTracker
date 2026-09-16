from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from releasetracker.models import AggregateTracker, TrackerSource
from releasetracker.services.repository_webhooks import RepositoryWebhookInput, normalize_event
from releasetracker.webhook_scheduler import RepositoryWebhookScheduler


async def setup_hook(storage, name: str):
    tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name=name,
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_config={"repo": f"acme/{name}", "fetch_mode": "rest_first"},
                )
            ],
        )
    )
    source = tracker.sources[0]
    hook = await storage.webhooks.save(
        RepositoryWebhookInput(
            tracker_source_id=source.id,
            provider="github",
            enabled=True,
            secret="0123456789abcdef",
        )
    )
    payload = {
        "action": "published",
        "repository": {"html_url": f"https://github.com/acme/{name}"},
        "release": {"tag_name": "v1.0.0", "draft": False},
    }
    event = normalize_event("github", {"x-github-event": "release"}, payload)
    return source, hook, event


def fake_scheduler(result):
    scheduler = AsyncMock()
    scheduler._manual_checks_in_progress = set()
    scheduler._process_aggregate_tracker_check.return_value = result
    return scheduler


@pytest.mark.asyncio
async def test_worker_registers_polling_and_daily_cleanup(storage, monkeypatch):
    cleanup = AsyncMock()
    monkeypatch.setattr(storage.webhooks, "cleanup", cleanup)
    host = MagicMock()
    worker = RepositoryWebhookScheduler(storage, fake_scheduler({}), host)
    await worker.initialize()
    assert [call.args[1] for call in host.add_interval_job.call_args_list] == [
        "worker",
        "cleanup",
    ]
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_dispatches_long_refresh_without_occupying_interval_job():
    started = asyncio.Event()
    release = asyncio.Event()
    storage = MagicMock()
    storage.webhooks.claim = AsyncMock(return_value=[{"id": 1}])
    worker = RepositoryWebhookScheduler(storage, fake_scheduler({}), MagicMock())

    async def process(_requests):
        started.set()
        await release.wait()

    worker._process = process

    await worker.tick()
    await asyncio.wait_for(started.wait(), timeout=0.1)
    assert worker._worker_task is not None

    await worker.tick()
    storage.webhooks.claim.assert_awaited_once()

    shutdown = asyncio.create_task(worker.shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()
    release.set()
    await asyncio.wait_for(shutdown, timeout=0.1)
    assert worker._worker_task is None

    await worker.tick()
    storage.webhooks.claim.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_refresh_is_retried_with_bounded_backoff(storage):
    source, hook, event = await setup_hook(storage, "retry-app")
    await storage.webhooks.receive(hook, "id:first", "hash-one", event, "", now=1000)
    requests = await storage.webhooks.claim(now=1006)
    scheduler = fake_scheduler(
        {
            "releases": [],
            "latest_version": None,
            "error": "upstream unavailable",
            "source_fetch_run_ids": {source.id: 21},
        }
    )
    await RepositoryWebhookScheduler(storage, scheduler, AsyncMock())._process(requests)
    deliveries = await storage.webhooks.deliveries(hook["id"])
    request = deliveries[0]["requests"][0]
    assert request["state"] == "deferred"
    assert request["attempts"] == 1
    assert request["reason"] == "upstream unavailable"
    assert request["source_fetch_run_id"] == 21


@pytest.mark.asyncio
async def test_recent_scheduled_fetch_defers_webhook_without_dropping_it(storage):
    source, hook, event = await setup_hook(storage, "cooldown-app")
    await storage.webhooks.receive(hook, "id:cooldown", "hash-cooldown", event, "", now=1000)
    requests = await storage.webhooks.claim(now=1006)
    await storage.create_source_fetch_run(source.id, trigger_mode="scheduled")
    scheduler = fake_scheduler({})
    await RepositoryWebhookScheduler(storage, scheduler, MagicMock())._process(requests)
    scheduler._process_aggregate_tracker_check.assert_not_awaited()
    deliveries = await storage.webhooks.deliveries(hook["id"])
    assert deliveries[0]["requests"][0]["state"] == "deferred"
    assert deliveries[0]["requests"][0]["reason"] == "source_cooldown"


@pytest.mark.asyncio
async def test_expired_running_lease_is_recovered_after_restart(storage):
    _source, hook, event = await setup_hook(storage, "lease-app")
    await storage.webhooks.receive(hook, "id:lease", "hash-lease", event, "", now=1000)
    claimed = await storage.webhooks.claim(now=1006)
    assert claimed and claimed[0]["state"] == "pending"
    db = await storage._get_connection()
    await db.execute(
        "UPDATE source_refresh_requests SET lease_until=? WHERE id=?", (1007, claimed[0]["id"])
    )
    await db.commit()
    recovered = await storage.webhooks.claim(now=1008)
    assert [request["id"] for request in recovered] == [claimed[0]["id"]]


@pytest.mark.asyncio
async def test_interrupted_source_fetch_runs_are_failed_on_startup(storage):
    source, _hook, _event = await setup_hook(storage, "interrupted-run-app")
    run_id = await storage.create_source_fetch_run(source.id, trigger_mode="webhook")
    finished_at = datetime(2026, 9, 16, 18, 50, tzinfo=timezone.utc)

    assert await storage.reconcile_interrupted_source_fetch_runs(finished_at=finished_at) == 1
    assert await storage.reconcile_interrupted_source_fetch_runs() == 0

    db = await storage._get_connection()
    run = await (
        await db.execute(
            "SELECT status,error_message,finished_at FROM source_fetch_runs WHERE id=?",
            (run_id,),
        )
    ).fetchone()
    assert dict(run) == {
        "status": "failed",
        "error_message": "Source fetch interrupted by application restart",
        "finished_at": finished_at.isoformat(),
    }


@pytest.mark.asyncio
async def test_restart_immediately_releases_running_webhook_requests(storage):
    _source, hook, event = await setup_hook(storage, "restart-request-app")
    await storage.webhooks.receive(hook, "id:restart", "hash-restart", event, "", now=1000)
    claimed = await storage.webhooks.claim(now=1006)

    assert await storage.webhooks.recover_interrupted_requests(now=1007) == 1
    deliveries = await storage.webhooks.deliveries(hook["id"])
    request = deliveries[0]["requests"][0]
    assert request["state"] == "deferred"
    assert request["reason"] == "worker_restarted"
    assert request["due_at"] == 1007

    reclaimed = await storage.webhooks.claim(now=1007)
    assert [request["id"] for request in reclaimed] == [claimed[0]["id"]]


@pytest.mark.asyncio
async def test_deliveries_remain_responsive_while_background_refresh_waits(storage):
    _source, hook, event = await setup_hook(storage, "responsive-page-app")
    await storage.webhooks.receive(hook, "id:responsive", "hash-responsive", event, "", now=1000)
    requests = await storage.webhooks.claim(now=1006)
    started = asyncio.Event()
    release = asyncio.Event()
    worker = RepositoryWebhookScheduler(storage, fake_scheduler({}), MagicMock())

    async def process(_requests):
        started.set()
        await release.wait()

    worker._process = process
    worker._worker_task = asyncio.create_task(worker._run_claimed(requests))
    await asyncio.wait_for(started.wait(), timeout=0.1)

    deliveries = await asyncio.wait_for(storage.webhooks.deliveries(hook["id"]), timeout=0.1)
    assert deliveries[0]["state"] == "running"

    release.set()
    await asyncio.wait_for(worker.shutdown(), timeout=0.1)


@pytest.mark.asyncio
async def test_event_received_during_running_refresh_is_not_lost(storage):
    _source, hook, event = await setup_hook(storage, "follow-up-app")
    await storage.webhooks.receive(hook, "id:one", "hash-one", event, "", now=1000)
    first = await storage.webhooks.claim(now=1006)
    await storage.webhooks.receive(hook, "id:two", "hash-two", event, "", now=1007)
    await storage.webhooks.finish(first, "no_change", attempt=True)
    second = await storage.webhooks.claim(now=1013)
    assert len(second) == 1
    assert second[0]["delivery_id"] != first[0]["delivery_id"]


@pytest.mark.asyncio
async def test_configuration_change_cancels_pending_refresh(storage):
    source, hook, event = await setup_hook(storage, "edit-app")
    await storage.webhooks.receive(hook, "id:edit", "hash-edit", event, "", now=1000)
    await storage.webhooks.save(
        RepositoryWebhookInput(
            tracker_source_id=source.id,
            provider="github",
            enabled=True,
            secret=None,
            release_published=True,
            workflow_success=False,
        ),
        hook["id"],
    )
    deliveries = await storage.webhooks.deliveries(hook["id"])
    assert deliveries[0]["state"] == "ignored"
    assert deliveries[0]["requests"][0]["reason"] == "configuration_changed"
    assert await storage.webhooks.claim(now=1010) == []
