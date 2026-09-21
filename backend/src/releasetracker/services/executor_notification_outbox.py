"""Durable, single-instance notification delivery, independent of deployment retries.

Internal rows are unique; a crash after HTTP acceptance but before the delivered
commit can repeat external delivery (at least once, never exactly once).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite

from ..executors.health_check.types import redact_for_log
from ..notifiers import SUPPORTED_NOTIFIER_TYPES, build_notifier
from ..notifiers.base import NotificationEvent

logger = logging.getLogger(__name__)


def health_check_performed(payload: dict) -> bool:
    health = payload.get("health_check")
    return bool(
        isinstance(health, dict)
        and health.get("strategy") not in (None, "none")
        and health.get("outcome") not in (None, "skipped", "not_performed")
        and health.get("performed") is not False
        and (health.get("performed") is True or health.get("attempt_count") != 0)
    )


def select_notification_event(notifier, payload: dict, notify_health_result: bool) -> str | None:
    """Deployment subscriptions take precedence, including combined health results."""
    if not notifier.enabled or notifier.type not in SUPPORTED_NOTIFIER_TYPES:
        return None
    event = {
        "success": NotificationEvent.EXECUTOR_RUN_SUCCESS,
        "failed": NotificationEvent.EXECUTOR_RUN_FAILED,
        "skipped": NotificationEvent.EXECUTOR_RUN_SKIPPED,
    }.get(payload.get("status"))
    if event and event in notifier.events:
        return event
    health_event = NotificationEvent.EXECUTOR_HEALTH_CHECK_RESULT
    if notify_health_result and health_check_performed(payload) and health_event in notifier.events:
        return health_event
    return None


def _safe_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value if value is None or isinstance(value, (bool, int, float)) else None
    value = redact_for_log(value)
    value = re.sub(r"[a-zA-Z][a-zA-Z0-9+.-]*://\S+", "[redacted URL]", value)
    value = re.sub(r"(?i)(password|secret|token|api[_-]?key)\s*[:=]\s*\S+", r"\1=[redacted]", value)
    return value[:500]


def sanitize_payload(payload: dict) -> dict:
    """Allowlist metadata; never persist freeform probe errors, bodies, or config."""
    keys = (
        "entity",
        "executor_id",
        "executor_name",
        "tracker_name",
        "tracker_source_id",
        "runtime_type",
        "target_mode",
        "run_id",
        "status",
        "started_at",
        "finished_at",
        "from_version",
        "to_version",
    )
    result = {key: _safe_text(payload[key]) for key in keys if key in payload}
    prefix = (
        "Read-only readiness recheck: "
        if payload.get("entity") == "executor_health_recheck"
        else "Deployment result: "
    )
    result["message"] = prefix + str(result.get("status", "unknown"))
    if isinstance(payload.get("services"), list):
        result["services"] = []
        for row in payload["services"][:100]:
            if not isinstance(row, dict):
                continue
            result["services"].append(
                {
                    "service": _safe_text(row.get("service")),
                    "from_version": _safe_text(row.get("from_version")),
                    "to_version": _safe_text(row.get("to_version")),
                    "status": (
                        row.get("status")
                        if row.get("status")
                        in {
                            "success",
                            "failed",
                            "skipped",
                            "healthy",
                            "unhealthy",
                            "pending",
                            "timeout",
                        }
                        else "unknown"
                    ),
                    "method": (
                        row.get("method")
                        if row.get("method")
                        in {
                            "runtime_native",
                            "runtime_state",
                            "kubernetes_rollout",
                            "helm_status",
                            "http",
                            "tcp",
                        }
                        else "unknown"
                    ),
                }
            )
    if health_check_performed(payload):
        health = payload["health_check"]
        outcome = health.get("outcome")
        if outcome not in {
            "healthy",
            "unhealthy",
            "error",
            "unknown",
            "timeout",
            "unsupported",
            "superseded",
        }:
            outcome = "unknown"
        # Only classify freeform diagnostics; never copy them into the outbox.
        detail = health.get("probe_diagnostics")
        diagnostic_text = " ".join(
            str(value)
            for value in (
                outcome,
                health.get("last_error"),
                health.get("error_category"),
                detail.get("error_category") if isinstance(detail, dict) else None,
            )
        ).lower()
        if outcome in {"unhealthy", "error", "unknown"} and (
            "timeout" in diagnostic_text or "timed out" in diagnostic_text
        ):
            outcome = "timeout"
        summary = {"outcome": outcome, "performed": True}
        services = health.get("services")
        if isinstance(services, list):
            # Freeform messages can contain credentials, probe bodies or remote output.
            # Preserve only identifiers and known structured classifications.
            summary["services"] = []
            for service in services:
                if not isinstance(service, dict):
                    continue
                name = service.get("service")
                safe_name = (
                    name
                    if isinstance(name, str)
                    and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,127}", name)
                    else "[redacted service]"
                )
                status = service.get("status")
                method = service.get("method")
                summary["services"].append(
                    {
                        "service": safe_name,
                        "status": (
                            status
                            if status
                            in (
                                "healthy",
                                "unhealthy",
                                "pending",
                                "timeout",
                                "unknown",
                                "unsupported",
                                "superseded",
                                "skipped",
                                "error",
                            )
                            else "unknown"
                        ),
                        "method": (
                            method
                            if method
                            in (
                                "runtime_native",
                                "runtime_state",
                                "kubernetes_rollout",
                                "helm_status",
                                "http",
                                "tcp",
                            )
                            else "unknown"
                        ),
                    }
                )
            summary["service_count"] = len(summary["services"])
        for key in ("strategy", "failure_policy"):
            allowed = {"auto", "runtime_native", "http", "tcp", "mark_failed", "mark_degraded"}
            summary[key] = health.get(key) if health.get(key) in allowed else "unknown"
        for key in (
            "attempt_count",
            "duration_seconds",
            "elapsed_seconds",
            "update_duration_seconds",
            "total_duration_seconds",
        ):
            value = health.get(key)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value >= 0
            ):
                summary[key] = value
        # Runtime readiness is not an application/business-health assertion.
        summary["scope"] = (
            "runtime readiness only; business health not verified"
            if summary["strategy"] == "runtime_native"
            else "configured readiness probe"
        )
        detail = health.get("probe_diagnostics") or {}
        summary["native_health_absent"] = bool(
            health.get("native_health_absent") is True
            or (
                summary["strategy"] == "runtime_native"
                and isinstance(detail, dict)
                and "health" in detail
                and detail["health"] is None
            )
        )
        result["health_check"] = summary
    return result


def notification_result_key(payload: dict) -> str:
    return (
        str(payload.get("status", "unknown"))
        + ":"
        + str(payload.get("health_check", {}).get("outcome", "not_performed"))
    )


async def write_notification_intent(
    db, payload: dict, *, notify_health_result: bool, timezone_name: str = "UTC"
) -> None:
    """Called inside the result transaction; no channel lookup, network or commit."""
    payload = dict(payload)
    if not payload.get("started_at"):
        row = await (
            await db.execute(
                "SELECT started_at FROM executor_run_history WHERE id=?", (payload["run_id"],)
            )
        ).fetchone()
        if row is None:
            raise ValueError("notification_run_missing")
        started = datetime.fromisoformat(row[0])
        try:
            zone = ZoneInfo(timezone_name)
        except Exception:
            zone = ZoneInfo("UTC")
        if started.tzinfo is None:
            started = started.replace(tzinfo=zone)
        payload["started_at"] = (
            started.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
        )
    safe = sanitize_payload(payload)
    now = time.time()
    await db.execute(
        """INSERT OR IGNORE INTO executor_notification_intents
        (run_id,final_result,payload,notify_health_result,available_at,created_at)
        VALUES (?,?,?,?,?,?)""",
        (
            safe["run_id"],
            notification_result_key(safe),
            json.dumps(safe, ensure_ascii=False),
            int(notify_health_result),
            now,
            now,
        ),
    )


class ExecutorNotificationOutbox:
    MAX_ATTEMPTS = 4
    RETRY_DELAYS = (30, 120, 600)

    def __init__(self, storage, scheduler_host=None, *, clock=time.time):
        self.storage = storage
        self.scheduler_host = scheduler_host
        self.clock = clock
        self._db = None
        self._lock = asyncio.Lock()
        self._worker = None
        self._stopping = False

    async def initialize(self) -> None:
        """Call once after database migrations, before scheduler host starts."""
        self._db = await aiosqlite.connect(self.storage.db_path)
        self._db.row_factory = aiosqlite.Row
        try:
            await self._db.execute("PRAGMA busy_timeout=5000")
            # Single-instance startup: abandoned sends may already have reached HTTP.
            await self._db.execute(
                "UPDATE executor_notification_outbox SET status=CASE WHEN attempts >= ? "
                "THEN 'failed' ELSE 'pending' END, available_at=? WHERE status='sending'",
                (self.MAX_ATTEMPTS, self.clock()),
            )
            await self._db.commit()
        except BaseException:
            await self._db.close()
            self._db = None
            raise
        self._stopping = False
        if self.scheduler_host:
            self.scheduler_host.add_interval_job(
                "executor_notifications", "tick", self.tick, seconds=2
            )

    async def _snapshot(self, item, event, payload):
        from ..notifiers.factory import build_notifier
        from ..notifiers.template_store import get_template

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

    async def enqueue(self, payload: dict, *, notify_health_result: bool = False) -> None:
        selected = [
            (item, select_notification_event(item, payload, notify_health_result))
            for item in await self.storage.get_notifiers()
        ]
        safe = sanitize_payload(payload)
        final_result = notification_result_key(safe)
        snapshots = {
            item.id: await self._snapshot(item, event, safe) for item, event in selected if event
        }
        selected = [(item.id, event) for item, event in selected]
        now = self.clock()
        async with self._lock:
            try:
                for notifier_id, event in selected:
                    if event:
                        await self._db.execute(
                            "INSERT OR IGNORE INTO executor_notification_outbox "
                            "(run_id,notifier_id,event,final_result,payload,available_at,created_at) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (
                                safe["run_id"],
                                notifier_id,
                                event,
                                final_result,
                                json.dumps(snapshots[notifier_id], ensure_ascii=False),
                                now,
                                now,
                            ),
                        )
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def expand_one(self) -> bool:
        """Atomically fan out one durable intent; failures never affect deployment."""
        async with self._lock:
            row = await (
                await self._db.execute(
                    "SELECT * FROM executor_notification_intents WHERE status='pending' "
                    "AND available_at<=? ORDER BY id LIMIT 1",
                    (self.clock(),),
                )
            ).fetchone()
            if row is None:
                return False
            try:
                payload = json.loads(row["payload"])
                selected = [
                    (
                        item,
                        select_notification_event(item, payload, bool(row["notify_health_result"])),
                    )
                    for item in await self.storage.get_notifiers()
                ]
                snapshots = {
                    item.id: await self._snapshot(item, event, payload)
                    for item, event in selected
                    if event
                }
                selected = [(item.id, event) for item, event in selected]
                await self._db.execute("BEGIN IMMEDIATE")
                for notifier_id, event in selected:
                    if event:
                        await self._db.execute(
                            "INSERT OR IGNORE INTO executor_notification_outbox "
                            "(run_id,notifier_id,event,final_result,payload,available_at,created_at) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (
                                row["run_id"],
                                notifier_id,
                                event,
                                row["final_result"],
                                json.dumps(snapshots[notifier_id], ensure_ascii=False),
                                self.clock(),
                                self.clock(),
                            ),
                        )
                await self._db.execute(
                    "UPDATE executor_notification_intents SET status='expanded',expanded_at=? WHERE id=?",
                    (self.clock(), row["id"]),
                )
                await self._db.commit()
            except Exception:
                await self._db.rollback()
                # Admission retries are durable and capped in delay, not silently discarded.
                delay = self.RETRY_DELAYS[min(row["attempts"], len(self.RETRY_DELAYS) - 1)]
                await self._db.execute(
                    "UPDATE executor_notification_intents SET attempts=attempts+1,available_at=? WHERE id=?",
                    (self.clock() + delay, row["id"]),
                )
                await self._db.commit()
                logger.error("Executor notification intent expansion deferred")
            except BaseException:
                await self._db.rollback()
                raise
            return True

    async def tick(self) -> None:
        """Short scheduler callback; network delivery never blocks scheduler ticks."""
        if not self._stopping and (self._worker is None or self._worker.done()):
            self._worker = asyncio.create_task(self._work())

    async def _work(self) -> None:
        try:
            # Bound each batch to avoid monopolizing shutdown or the event loop.
            for _ in range(20):
                if self._stopping:
                    break
                expanded = await self.expand_one()
                delivered = await self.deliver_one()
                if not expanded and not delivered:
                    break
        except Exception:
            # Do not log exception strings: transport errors can contain credentials.
            logger.error("Executor notification outbox worker failed")

    async def deliver_one(self) -> bool:
        async with self._lock:
            # Reclaim a worker interrupted after claiming (also without a restart).
            await self._db.execute(
                "UPDATE executor_notification_outbox SET status=CASE WHEN attempts >= ? "
                "THEN 'failed' ELSE 'pending' END WHERE status='sending' AND available_at<=?",
                (self.MAX_ATTEMPTS, self.clock()),
            )
            await self._db.commit()
            cursor = await self._db.execute(
                "SELECT * FROM executor_notification_outbox WHERE status='pending' "
                "AND available_at<=? ORDER BY id LIMIT 1",
                (self.clock(),),
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            await self._db.execute(
                "UPDATE executor_notification_outbox SET status='sending',attempts=attempts+1, "
                "available_at=? WHERE id=?",
                (self.clock() + 120, row["id"]),
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
                payload = json.loads(row["payload"])
                if "_prepared_notification" not in payload:
                    payload = await self._snapshot(item, row["event"], payload)
                    async with self._lock:
                        await self._db.execute(
                            "UPDATE executor_notification_outbox SET payload=? WHERE id=?",
                            (json.dumps(payload, ensure_ascii=False), row["id"]),
                        )
                        await self._db.commit()
                async with asyncio.timeout(60):
                    delivered = await notifier.notify(row["event"], payload)
                if delivered is True:
                    status = "delivered"
        except Exception:
            pass  # Retry only notification delivery, never deployment.
        attempts = row["attempts"] + 1
        if status == "pending" and attempts >= self.MAX_ATTEMPTS:
            status = "failed"
        now = self.clock()
        delay = self.RETRY_DELAYS[min(attempts - 1, len(self.RETRY_DELAYS) - 1)]
        async with self._lock:
            await self._db.execute(
                "UPDATE executor_notification_outbox SET status=?,available_at=?,delivered_at=? WHERE id=?",
                (status, now + delay, now if status == "delivered" else None, row["id"]),
            )
            await self._db.commit()
        return True

    async def shutdown(self) -> None:
        self._stopping = True
        if self.scheduler_host:
            self.scheduler_host.remove_job("executor_notifications", "tick")
        if self._worker:
            await self._worker
        if self._db:
            await self._db.close()
            self._db = None
