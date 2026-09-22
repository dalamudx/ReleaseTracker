"""Durable delivery for deployment-admission notifications.

Admission events are created transactionally with the deployment plan. This worker
only expands and delivers them; it never approves, mutates, or retries a deployment.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from ..notifiers.factory import SUPPORTED_NOTIFIER_TYPES, build_notifier
from ..notifiers.template_store import get_template

logger = logging.getLogger(__name__)


class DeploymentAdmissionNotificationOutbox:
    MAX_ATTEMPTS = 4
    RETRY_DELAYS = (30, 120, 600, 1800)

    def __init__(self, storage, scheduler_host=None):
        self.storage = storage
        self.scheduler_host = scheduler_host
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
                "deployment_admission_notifications", "tick", self.tick, seconds=2
            )

    async def tick(self) -> None:
        if self._stopping or self._worker is not None and not self._worker.done():
            return
        self._worker = asyncio.create_task(self._run_once())

    async def _run_once(self) -> None:
        try:
            if await self._expand_one():
                return
            await self._deliver_one()
        except Exception:
            logger.exception("Deployment admission notification worker failed")

    async def _snapshot(self, item, event, payload):
        base_url = (
            await self.storage.get_system_base_url()
            if hasattr(self.storage, "get_system_base_url")
            else ""
        )
        notifier = build_notifier(
            notifier_type=item.type,
            name=item.name,
            url=item.url,
            events=item.events,
            language=item.language,
            template=await get_template(self.storage, getattr(item, "template_id", None)),
            detail_url=base_url + "/executors" if base_url else None,
        )
        return {**payload, "_prepared_notification": await notifier.prepare(event, payload)}

    async def _expand_one(self) -> bool:
        if self._db is None:
            return False
        now = time.time()
        async with self._lock:
            cursor = await self._db.execute(
                "SELECT * FROM deployment_admission_events WHERE expanded_at IS NULL AND due_at<=? ORDER BY id LIMIT 1",
                (now,),
            )
            event = await cursor.fetchone()
            if event is None:
                return False
            payload = json.loads(event["payload"])
            notifiers = await self.storage.get_notifiers()
            try:
                for item in notifiers:
                    if not item.enabled or item.type not in SUPPORTED_NOTIFIER_TYPES:
                        continue
                    if event["event"] not in item.events:
                        continue
                    rendered = await self._snapshot(item, event["event"], payload)
                    await self._db.execute(
                        "INSERT OR IGNORE INTO deployment_admission_notification_outbox "
                        "(admission_event_id,notifier_id,event,payload,status,attempts,available_at,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (
                            event["id"],
                            item.id,
                            event["event"],
                            json.dumps(rendered, ensure_ascii=False),
                            "pending",
                            0,
                            now,
                            now,
                        ),
                    )
                await self._db.execute(
                    "UPDATE deployment_admission_events SET expanded_at=? WHERE id=?",
                    (now, event["id"]),
                )
                await self._db.commit()
                return True
            except Exception:
                await self._db.rollback()
                attempts = int(event["attempts"]) + 1
                await self._db.execute(
                    "UPDATE deployment_admission_events SET attempts=?,due_at=? WHERE id=?",
                    (
                        attempts,
                        now + self.RETRY_DELAYS[min(attempts - 1, len(self.RETRY_DELAYS) - 1)],
                        event["id"],
                    ),
                )
                await self._db.commit()
                raise

    async def _deliver_one(self) -> bool:
        if self._db is None:
            return False
        now = time.time()
        async with self._lock:
            await self._db.execute(
                "UPDATE deployment_admission_notification_outbox SET status=CASE WHEN attempts>=? THEN 'failed' ELSE 'pending' END "
                "WHERE status='sending' AND available_at<=?",
                (self.MAX_ATTEMPTS, now),
            )
            await self._db.commit()
            cursor = await self._db.execute(
                "SELECT * FROM deployment_admission_notification_outbox WHERE status='pending' AND available_at<=? ORDER BY id LIMIT 1",
                (now,),
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            await self._db.execute(
                "UPDATE deployment_admission_notification_outbox SET status='sending',attempts=attempts+1,available_at=? WHERE id=?",
                (now + 120, row["id"]),
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
                )
                async with asyncio.timeout(60):
                    if await notifier.notify(row["event"], json.loads(row["payload"])) is True:
                        status = "delivered"
        except Exception:
            logger.exception(
                "Deployment admission notification delivery failed", extra={"outbox_id": row["id"]}
            )
        attempts = int(row["attempts"]) + 1
        if status == "pending" and attempts >= self.MAX_ATTEMPTS:
            status = "failed"
        delay = self.RETRY_DELAYS[min(attempts - 1, len(self.RETRY_DELAYS) - 1)]
        async with self._lock:
            await self._db.execute(
                "UPDATE deployment_admission_notification_outbox SET status=?,available_at=?,delivered_at=? WHERE id=?",
                (status, now + delay, now if status == "delivered" else None, row["id"]),
            )
            await self._db.commit()
        return True

    async def shutdown(self) -> None:
        self._stopping = True
        if self.scheduler_host:
            self.scheduler_host.remove_job("deployment_admission_notifications", "tick")
        if self._worker:
            await self._worker
        if self._db:
            await self._db.close()
            self._db = None
