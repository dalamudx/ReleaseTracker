"""Release notifications are durable, de-duplicated, retried, and per channel."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from releasetracker.config import Channel
from releasetracker.models import AggregateTracker, Release, ReleaseChannel, TrackerSource
from releasetracker.scheduler import ReleaseScheduler
from releasetracker.services import release_notification_outbox as outbox_module
from releasetracker.services.release_notification_outbox import (
    MAX_ATTEMPTS,
    ReleaseNotificationOutbox,
    enqueue_release_notification,
)
from releasetracker.trackers.base import BaseTracker

pytestmark = pytest.mark.asyncio


async def notifier(storage, events=("new_release", "republish"), enabled=True):
    created = await storage.create_notifier(
        {
            "name": f"hook-{len(events)}-{events[0]}",
            "type": "webhook",
            "url": "https://hooks.example/abc",
            "events": list(events),
            "enabled": enabled,
        }
    )
    return created.id


def release(version="1.0.0", rid=1, digest=None, channel=None):
    return Release(
        id=rid,
        tracker_name="app",
        version=version,
        name=version,
        tag_name=version,
        url=f"https://example.org/{version}",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        artifact_digest=digest,
        channel_name=channel,
    )


async def rows(storage):
    db = await storage._get_connection()
    cursor = await db.execute(
        "SELECT status,attempts,event FROM release_notification_outbox ORDER BY id"
    )
    return [tuple(r) for r in await cursor.fetchall()]


async def test_enqueue_is_deduplicated_and_skips_unsubscribed(storage):
    await notifier(storage)
    await notifier(storage, events=("executor_failed",))
    assert await enqueue_release_notification(storage, "new_release", release()) == 1
    assert await enqueue_release_notification(storage, "new_release", release()) == 0
    assert (
        await enqueue_release_notification(
            storage, "republish", release(digest="sha256:" + "a" * 64)
        )
        == 1
    )
    assert len(await rows(storage)) == 2


async def test_delivery_retries_then_fails_and_survives_restart(storage, monkeypatch):
    await notifier(storage)
    await enqueue_release_notification(storage, "new_release", release())
    import time

    now = [time.time() + 1]
    sent = AsyncMock(return_value=False)
    monkeypatch.setattr(
        outbox_module, "build_notifier", lambda **kwargs: type("N", (), {"notify": sent})()
    )
    outbox = ReleaseNotificationOutbox(storage, clock=lambda: now[0])
    await outbox.initialize()
    try:
        for _ in range(MAX_ATTEMPTS):
            assert await outbox.deliver_one()
            now[0] += 3600
        assert (await rows(storage))[0][:2] == ("failed", MAX_ATTEMPTS)
        assert not await outbox.deliver_one()
    finally:
        await outbox.shutdown()


async def test_interrupted_send_is_reclaimed_and_delivered(storage, monkeypatch):
    await notifier(storage)
    await enqueue_release_notification(storage, "new_release", release())
    db = await storage._get_connection()
    await db.execute(
        "UPDATE release_notification_outbox SET status='sending',attempts=1,available_at=0"
    )
    await db.commit()
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(
        outbox_module, "build_notifier", lambda **kwargs: type("N", (), {"notify": sent})()
    )
    outbox = ReleaseNotificationOutbox(storage)
    await outbox.initialize()
    try:
        assert await outbox.deliver_one()
    finally:
        await outbox.shutdown()
    assert (await rows(storage))[0][:2] == ("delivered", 2)
    assert sent.await_args.args[1].version == "1.0.0"


async def test_disabled_notifier_is_discarded(storage, monkeypatch):
    item_id = await notifier(storage)
    await enqueue_release_notification(storage, "new_release", release())
    await storage.update_notifier(item_id, {"enabled": False})
    outbox = ReleaseNotificationOutbox(storage)
    await outbox.initialize()
    try:
        await outbox.deliver_one()
    finally:
        await outbox.shutdown()
    assert (await rows(storage))[0][0] == "discarded"


class Upstream(BaseTracker):
    def __init__(self, name, releases):
        super().__init__(
            name,
            channels=[
                Channel(name="stable", type="release"),
                Channel(name="beta", type="prerelease"),
            ],
        )
        self.releases = releases

    async def fetch_all(self, limit=20, fallback_tags=False):
        return list(self.releases)

    async def fetch_latest(self, fallback_tags=False):
        return self.releases[-1]


def upstream_release(version, days, prerelease):
    return Release(
        tracker_name="channels",
        version=version,
        name=version,
        tag_name=version,
        url=f"https://example.org/{version}",
        prerelease=prerelease,
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=days),
    )


async def test_stable_update_masked_by_newer_prerelease_is_still_notified(storage, monkeypatch):
    await storage.create_aggregate_tracker(
        AggregateTracker(
            name="channels",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_config={"repo": "acme/app"},
                    release_channels=[
                        ReleaseChannel(release_channel_key="stable", name="stable", type="release"),
                        ReleaseChannel(release_channel_key="beta", name="beta", type="prerelease"),
                    ],
                )
            ],
        )
    )
    releases = [upstream_release("1.0.0", 0, False), upstream_release("2.0.0-beta.1", 2, True)]
    adapter = Upstream("channels", releases)
    scheduler = ReleaseScheduler(storage)
    monkeypatch.setattr(scheduler, "_create_tracker", AsyncMock(return_value=adapter))
    monkeypatch.setattr(storage.webhooks, "last_source_run_at", AsyncMock(return_value=None))
    monkeypatch.setattr("releasetracker.scheduler_manual_checks.MANUAL_CHECK_COOLDOWN_SECONDS", 0)
    events = []

    async def capture(event, item):
        events.append((event, item.version))

    monkeypatch.setattr(scheduler, "_send_notifications", capture)
    await scheduler.check_tracker_now_v2("channels")
    assert sorted(events) == [("new_release", "1.0.0"), ("new_release", "2.0.0-beta.1")]
    events.clear()
    await scheduler.check_tracker_now_v2("channels")
    assert events == []

    releases.insert(1, upstream_release("1.1.0", 1, False))
    await scheduler.check_tracker_now_v2("channels")
    # The beta stays the tracker-wide winner; the stable channel still moved.
    assert events == [("new_release", "1.1.0")]
