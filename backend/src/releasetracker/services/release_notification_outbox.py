"""Durable, de-duplicated release notifications (new release / republish)."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from ..models import Release
from ..notifiers import SUPPORTED_NOTIFIER_TYPES, build_notifier
from ..notifiers.template_store import get_template

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 4
RETRY_DELAYS = (30, 120, 600, 1800)
SENDING_LEASE = 120


def release_dedupe_key(event: str, release: Release) -> str:
    digest = str(release.artifact_digest or "").strip().lower()
    identity = f"id:{release.id}" if release.id is not None else f"v:{release.version}"
    return f"{release.tracker_name}|{event}|{identity}|{digest}"


async def enqueue_release_notification(storage, event: str, release: Release) -> int:
    """Record one pending delivery per subscribed notifier; duplicates are ignored."""
    return await _enqueue(
        storage, event, release_dedupe_key(event, release), release.model_dump_json()
    )


async def enqueue_system_alert(storage, dedupe_key: str, payload: dict) -> int:
    """Durable operational alert using the existing ``error`` notifier event."""
    return await _enqueue(
        storage, "error", f"system|{dedupe_key}", json.dumps({"_system": True, **payload})
    )


async def _enqueue(storage, event: str, key: str, payload: str) -> int:
    notifiers = [
        n
        for n in await storage.get_notifiers()
        if n.enabled and n.type in SUPPORTED_NOTIFIER_TYPES and event in n.events
    ]
    if not notifiers:
        return 0
    db = await storage._get_connection()
    now = time.time()
    inserted = 0
    for notifier in notifiers:
        cursor = await db.execute(
            """INSERT OR IGNORE INTO release_notification_outbox
               (notifier_id,event,dedupe_key,release,available_at,created_at)
               VALUES (?,?,?,?,?,?)""",
            (notifier.id, event, key, payload, now, now),
        )
        inserted += max(cursor.rowcount, 0)
    await db.commit()
    return inserted


class ReleaseNotificationOutbox:
    def __init__(self, storage, scheduler_host=None, *, clock=time.time):
        self.storage = storage
        self.scheduler_host = scheduler_host
        self.clock = clock
        self._db = None
        self._worker: asyncio.Task | None = None
        self._stopping = False
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        if not hasattr(self.storage, "_open_connection"):
            return
        self._db = await self.storage._open_connection()
        if self.scheduler_host:
            self.scheduler_host.add_interval_job(
                "release_notifications", "tick", self.tick, seconds=5
            )

    async def tick(self) -> None:
        if self._stopping or (self._worker is not None and not self._worker.done()):
            return
        self._worker = asyncio.create_task(self._work())

    async def _work(self) -> None:
        try:
            for _ in range(20):
                if self._stopping or not await self.deliver_one():
                    break
        except Exception:
            # Never log exception text: notifier URLs may contain tokens.
            logger.error("Release notification worker failed")

    async def deliver_one(self) -> bool:
        if self._db is None:
            return False
        now = self.clock()
        async with self._lock:
            # Reclaim deliveries interrupted mid-send (crash or cancellation).
            await self._db.execute(
                """UPDATE release_notification_outbox SET status=CASE WHEN attempts>=?
                   THEN 'failed' ELSE 'pending' END WHERE status='sending' AND available_at<=?""",
                (MAX_ATTEMPTS, now),
            )
            row = await (
                await self._db.execute(
                    """SELECT * FROM release_notification_outbox WHERE status='pending'
                       AND available_at<=? ORDER BY id LIMIT 1""",
                    (now,),
                )
            ).fetchone()
            if row is None:
                await self._db.commit()
                return False
            await self._db.execute(
                """UPDATE release_notification_outbox SET status='sending',
                   attempts=attempts+1,available_at=? WHERE id=?""",
                (now + SENDING_LEASE, row["id"]),
            )
            await self._db.commit()
        status = "pending"
        try:
            item = await self.storage.get_notifier(row["notifier_id"])
            if (
                item is None
                or not item.enabled
                or item.type not in SUPPORTED_NOTIFIER_TYPES
                or row["event"] not in item.events
            ):
                status = "discarded"
            else:
                notifier = build_notifier(
                    notifier_type=item.type,
                    name=item.name,
                    url=item.url,
                    events=item.events,
                    language=item.language,
                    template=await get_template(self.storage, getattr(item, "template_id", None)),
                )
                raw = json.loads(row["release"])
                payload = raw if raw.pop("_system", False) else Release.model_validate(raw)
                async with asyncio.timeout(60):
                    if await notifier.notify(row["event"], payload) is True:
                        status = "delivered"
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Release notification delivery failed")
        attempts = row["attempts"] + 1
        if status == "pending" and attempts >= MAX_ATTEMPTS:
            status = "failed"
        done = self.clock()
        delay = RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)]
        async with self._lock:
            await self._db.execute(
                """UPDATE release_notification_outbox SET status=?,available_at=?,delivered_at=?
                   WHERE id=?""",
                (status, done + delay, done if status == "delivered" else None, row["id"]),
            )
            await self._db.commit()
        return True

    async def shutdown(self) -> None:
        self._stopping = True
        if self.scheduler_host:
            self.scheduler_host.remove_job("release_notifications", "tick")
        if self._worker:
            await asyncio.gather(self._worker, return_exceptions=True)
        if self._db:
            await self._db.close()
            self._db = None
