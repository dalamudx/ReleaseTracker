"""Persistent repository-webhook refresh worker."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime

from .models import TrackerStatus
from .scheduler_status import _status_type
from .storage.sqlite_webhooks import identity

logger = logging.getLogger(__name__)

_OCI_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def _container_priority_aliases(requests) -> tuple[str, ...]:
    aliases: list[str] = []
    for request in requests:
        try:
            raw_summary = request.get("summary") or "{}"
            summary = json.loads(raw_summary) if isinstance(raw_summary, str) else raw_summary
        except (TypeError, ValueError):
            continue
        if not isinstance(summary, dict) or summary.get("kind") != "workflow":
            continue
        ref = str(summary.get("ref") or "").strip()
        for prefix in ("refs/heads/", "refs/tags/"):
            if ref.startswith(prefix):
                ref = ref.removeprefix(prefix)
                break
        if _OCI_TAG_RE.fullmatch(ref) and ref not in aliases:
            aliases.append(ref)
    return tuple(aliases)


class RepositoryWebhookScheduler:
    def __init__(self, storage, release_scheduler, scheduler_host):
        self.storage = storage
        self.release_scheduler = release_scheduler
        self.scheduler_host = scheduler_host
        self._worker_task: asyncio.Task[None] | None = None
        self._stopping = False
        self.fetch_tasks = None

    async def initialize(self):
        recovered = await self.storage.webhooks.recover_interrupted_requests()
        if recovered:
            logger.warning(
                "Recovered %s interrupted repository webhook refresh requests", recovered
            )
        self.scheduler_host.add_interval_job("repository_webhooks", "worker", self.tick, seconds=2)
        self.scheduler_host.add_interval_job(
            "repository_webhooks", "cleanup", self.cleanup, seconds=86400
        )
        await self.cleanup()

    async def cleanup(self):
        try:
            await self.storage.webhooks.cleanup()
        except Exception:
            logger.exception("Repository webhook delivery cleanup failed")

    async def tick(self):
        """Claim work quickly and keep the APScheduler interval job non-blocking."""
        if self._stopping:
            return
        if self.fetch_tasks is not None:
            await self._dispatch_to_task_queue()
            return
        if self._worker_task is not None:
            if not self._worker_task.done():
                return
            self._worker_task = None
        try:
            requests = await self.storage.webhooks.claim()
        except Exception:
            logger.exception("Repository webhook worker claim failed")
            return
        if not requests:
            return
        task = asyncio.create_task(self._run_claimed(requests), name="repository-webhook-refresh")
        self._worker_task = task
        task.add_done_callback(self._worker_done)

    async def _dispatch_to_task_queue(self):
        # Synchronize receipt visibility from the single authoritative queue. This
        # also repairs a crash between task completion and delivery bookkeeping.
        db = await self.storage._get_connection()
        rows = await (
            await db.execute("""SELECT r.*,t.state AS task_state,t.attempts AS task_attempts,
               t.error_code,t.due_at AS task_due,t.result AS task_result
               FROM source_refresh_requests r JOIN tasks t ON t.id=r.task_id
               WHERE r.state IN ('pending','running','deferred') LIMIT 1000""")
        ).fetchall()
        for row in rows:
            task_state = row["task_state"]
            state = {
                "queued": "deferred",
                "retry_wait": "deferred",
                "running": "running",
                "succeeded": "completed",
                "no_change": "no_change",
                "skipped": "ignored",
                "superseded": "ignored",
                "cancelled": "ignored",
            }.get(task_state, "failed")
            result = json.loads(row["task_result"] or "{}")
            run_id = result.get("source_fetch_run_ids", {}).get(str(row["tracker_source_id"]))
            await db.execute(
                """UPDATE source_refresh_requests SET state=?,reason=?,due_at=?,attempts=?,
                   source_fetch_run_id=COALESCE(?,source_fetch_run_id),lease_until=NULL WHERE id=?""",
                (
                    state,
                    row["error_code"] or "",
                    row["task_due"],
                    row["task_attempts"],
                    run_id,
                    row["id"],
                ),
            )
        await db.commit()
        requests = await self.storage.webhooks.claim()
        if not requests:
            return
        try:
            receipt = await self.fetch_tasks.enqueue(
                requests[0]["tracker_name"],
                trigger_mode="webhook",
                source_ids={item["tracker_source_id"] for item in requests},
                request_ids=[item["id"] for item in requests],
                initial_attempts=max(item["attempts"] for item in requests),
                max_retries=3 if any(item["attempts"] for item in requests) else None,
                priority_aliases=_container_priority_aliases(requests),
                trigger_key="webhook-requests:" + ",".join(str(item["id"]) for item in requests),
            )
        except ValueError:
            await self.storage.webhooks.finish(requests, "ignored", "configuration_changed")
            return
        async with self.storage.webhooks.transaction() as db:
            await db.executemany(
                "UPDATE source_refresh_requests SET task_id=? WHERE id=?",
                [(receipt["task_id"], item["id"]) for item in requests],
            )

    async def _run_claimed(self, requests):
        try:
            await self._process(requests)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Repository webhook worker failed")

    def _worker_done(self, task: asyncio.Task[None]) -> None:
        if self._worker_task is task:
            self._worker_task = None

    async def shutdown(self) -> None:
        """Stop dispatching and let the claimed persistent work finish before DB close."""
        self._stopping = True
        task = self._worker_task
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            if self._worker_task is task:
                self._worker_task = None

    async def _process(self, requests):
        valid = []
        for request in requests:
            hook = await self.storage.webhooks.get(request["webhook_id"])
            context = await self.storage.webhooks.source(request["tracker_source_id"])
            if (
                not hook
                or not hook["enabled"]
                or hook["generation"] != request["webhook_generation"]
                or not context
                or not context[0].enabled
                or not context[2]
                or identity(context[0]) != request["source_identity"]
            ):
                await self.storage.webhooks.finish([request], "ignored", "configuration_changed")
                continue
            valid.append(request)
        if not valid:
            return

        tracker_name = valid[0]["tracker_name"]
        if tracker_name in self.release_scheduler._manual_checks_in_progress:
            await self.storage.webhooks.finish(
                valid, "deferred", "check_in_progress", time.time() + 5
            )
            return

        source_ids = {request["tracker_source_id"] for request in valid}
        last_started = await self.storage.webhooks.last_source_run_at(source_ids)
        if last_started:
            elapsed = (datetime.now() - datetime.fromisoformat(last_started)).total_seconds()
            if elapsed < 30:
                await self.storage.webhooks.finish(
                    valid, "deferred", "source_cooldown", time.time() + (30 - elapsed)
                )
                return

        tracker = await self.storage.get_aggregate_tracker(tracker_name)
        config = await self.storage.get_tracker_config(tracker_name)
        if tracker is None or not tracker.enabled or (config is not None and not config.enabled):
            await self.storage.webhooks.finish(valid, "ignored", "tracker_disabled")
            return

        before = await self.storage.webhooks.source_revision_tokens(source_ids)
        priority_aliases = _container_priority_aliases(valid)
        result = {}
        self.release_scheduler._manual_checks_in_progress.add(tracker_name)
        try:
            result = await self.release_scheduler._process_aggregate_tracker_check(
                tracker_name,
                tracker,
                config,
                log_prefix="Webhook ",
                trigger_mode="webhook",
                source_ids=source_ids,
                container_priority_aliases=priority_aliases,
            )
            after = await self.storage.webhooks.source_revision_tokens(source_ids)
            changed = before != after
            error = result.get("error") or ""
            latest_version = result.get("latest_version")
            releases = result.get("releases") or []
            await self.storage.update_tracker_status(
                TrackerStatus(
                    name=tracker_name,
                    type=_status_type(config, tracker),
                    enabled=True,
                    last_check=datetime.now(),
                    last_version=latest_version if releases or latest_version else None,
                    error=error or None,
                )
            )
            if error:
                raise RuntimeError(error)
            await self.storage.webhooks.finish(
                valid,
                "completed" if changed else "no_change",
                error,
                run_ids=result.get("source_fetch_run_ids"),
                attempt=True,
            )
        except Exception as exc:
            attempts = max(request["attempts"] for request in valid) + 1
            error = str(exc) or exc.__class__.__name__
            if attempts < 4:
                delay = (30, 120, 600)[attempts - 1]
                await self.storage.webhooks.finish(
                    valid,
                    "deferred",
                    error,
                    time.time() + delay,
                    run_ids=result.get("source_fetch_run_ids"),
                    attempt=True,
                )
            else:
                await self.storage.webhooks.finish(
                    valid,
                    "failed",
                    error,
                    run_ids=result.get("source_fetch_run_ids"),
                    attempt=True,
                )
        finally:
            self.release_scheduler._manual_checks_in_progress.discard(tracker_name)
