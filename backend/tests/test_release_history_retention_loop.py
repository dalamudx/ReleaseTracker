"""Retention must not fight with repeated upstream observations."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from releasetracker.config import Channel
from releasetracker.models import AggregateTracker, Release, ReleaseChannel, TrackerSource
from releasetracker.scheduler import ReleaseScheduler
from releasetracker.services.fetch_tasks import FetchTasks
from releasetracker.services.task_queue import TaskQueue
from releasetracker.storage.sqlite import SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY
from releasetracker.trackers.base import BaseTracker

pytestmark = pytest.mark.asyncio
VERSIONS = [f"1.0.{index}" for index in range(8)]


class Upstream(BaseTracker):
    def __init__(self, name, kind):
        super().__init__(name, channels=[Channel(name="stable", type="release")])
        self.kind = kind

    async def fetch_all(self, limit=20, fallback_tags=False):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [
            Release(
                tracker_name=self.name,
                version=version,
                tag_name=version,
                name=f"Release {version}",
                published_at=base + timedelta(days=index),
                url=f"https://example.org/releases/{version}",
                commit_sha=(f"sha256:{index:064x}" if self.kind == "container" else None),
            )
            for index, version in enumerate(VERSIONS)
        ]

    async def fetch_latest(self, fallback_tags=False):
        return (await self.fetch_all())[-1]


async def setup(storage, monkeypatch):
    tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="retention-loop",
            sources=[
                TrackerSource(
                    source_key=kind,
                    source_type=kind,
                    source_config=(
                        {"repo": "acme/app"}
                        if kind == "github"
                        else {"image": "acme/app", "registry": "registry.example"}
                    ),
                    release_channels=[
                        ReleaseChannel(release_channel_key="stable", name="stable", type="release")
                    ],
                )
                for kind in ("github", "container")
            ],
        )
    )
    await storage.set_setting(SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY, "2")
    scheduler = ReleaseScheduler(storage)
    adapters = {kind: Upstream(tracker.name, kind) for kind in ("github", "container")}

    async def factory(config):
        return adapters[config.type]

    monkeypatch.setattr(scheduler, "_create_tracker", factory)
    monkeypatch.setattr(storage.webhooks, "last_source_run_at", AsyncMock(return_value=None))
    handler = FetchTasks(storage, scheduler)
    scheduler.fetch_tasks = handler
    queue = TaskQueue(storage.tasks, MagicMock())
    queue.register("fetch", handler)
    return tracker, handler, queue


async def fetch(storage, handler, queue, name):
    await handler.enqueue(name, trigger_mode="scheduled")
    task = await storage.tasks.claim("fetch")
    await queue._run(task)
    return await storage.tasks.get(task["id"])


async def surface(storage, tracker_id):
    db = await storage._get_connection()
    rows = {}
    for table, column in (
        (
            "source_release_history",
            "tracker_source_id IN (SELECT id FROM aggregate_tracker_sources WHERE aggregate_tracker_id=?)",
        ),
        ("tracker_release_history", "aggregate_tracker_id=?"),
    ):
        row = await (
            await db.execute(
                f"SELECT COUNT(*),COALESCE(MAX(id),0) FROM {table} WHERE {column}", (tracker_id,)
            )
        ).fetchone()
        rows[table] = tuple(row)
    return rows


async def test_retention_does_not_cause_refetch_churn_or_false_changes(storage, monkeypatch):
    tracker, handler, queue = await setup(storage, monkeypatch)
    assert (await fetch(storage, handler, queue, tracker.name))["state"] == "succeeded"
    assert (await fetch(storage, handler, queue, tracker.name))["state"] == "no_change"

    cleanup = await storage.cleanup_release_history()
    assert cleanup["tracker_release_history_deleted"] > 0
    pruned = await surface(storage, tracker.id)
    current = await storage.get_tracker_current_releases(tracker.id)

    for _ in range(2):
        task = await fetch(storage, handler, queue, tracker.name)
        assert task["state"] == "no_change", task
        assert await surface(storage, tracker.id) == pruned
        again = await storage.get_tracker_current_releases(tracker.id)
        assert [(r.version, r.id) for r in again] == [(r.version, r.id) for r in current]
    # A second cleanup has nothing left to prune: the steady state is stable.
    assert (await storage.cleanup_release_history())["tracker_release_history_deleted"] == 0


async def test_new_release_after_retention_is_still_detected(storage, monkeypatch):
    tracker, handler, queue = await setup(storage, monkeypatch)
    await fetch(storage, handler, queue, tracker.name)
    await storage.cleanup_release_history()
    await fetch(storage, handler, queue, tracker.name)
    VERSIONS.append("1.1.0")
    try:
        task = await fetch(storage, handler, queue, tracker.name)
    finally:
        VERSIONS.pop()
    assert task["state"] == "succeeded", task
    assert task["result"]["latest_version"] == "1.1.0"


async def test_larger_retention_or_channel_change_restores_pruned_history(storage, monkeypatch):
    tracker, handler, queue = await setup(storage, monkeypatch)
    await fetch(storage, handler, queue, tracker.name)
    await storage.cleanup_release_history()
    pruned = (await surface(storage, tracker.id))["tracker_release_history"][0]
    await storage.set_setting(SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY, "20")
    await fetch(storage, handler, queue, tracker.name)
    assert (await surface(storage, tracker.id))["tracker_release_history"][0] > pruned

    await storage.set_setting(SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY, "2")
    await storage.cleanup_release_history()
    db = await storage._get_connection()
    assert (
        await (
            await db.execute("SELECT COUNT(*) FROM tracker_release_history_tombstones")
        ).fetchone()
    )[0]
    await storage.update_aggregate_tracker(await storage.get_aggregate_tracker(tracker.name))
    assert (
        await (
            await db.execute("SELECT COUNT(*) FROM tracker_release_history_tombstones")
        ).fetchone()
    )[0] == 0


async def test_release_removed_upstream_is_still_cleaned(storage, monkeypatch):
    tracker, handler, queue = await setup(storage, monkeypatch)
    await fetch(storage, handler, queue, tracker.name)
    removed = VERSIONS.pop(0)
    try:
        await fetch(storage, handler, queue, tracker.name)
        await storage.cleanup_release_history()
    finally:
        VERSIONS.insert(0, removed)
    db = await storage._get_connection()
    remaining = await (
        await db.execute("SELECT COUNT(*) FROM source_release_history WHERE version=?", (removed,))
    ).fetchone()
    rows = await (
        await db.execute(
            """SELECT h.id,h.source_type,
           (SELECT COUNT(*) FROM tracker_release_history t WHERE t.primary_source_release_history_id=h.id),
           (SELECT COUNT(*) FROM tracker_release_history_sources s WHERE s.source_release_history_id=h.id)
           FROM source_release_history h WHERE version=?""",
            (removed,),
        )
    ).fetchall()
    # Unreferenced truth that is no longer listed upstream is still removed;
    # rows kept by tracker history retention are unaffected.
    assert remaining[0] < 2
    assert all(row[2] or row[3] for row in rows), [tuple(r) for r in rows]
