"""Version-check jobs: every attempt has an explicit source set and config identity."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime

from ..models import TrackerStatus
from ..scheduler_status import _status_type
from .task_queue import Deferred, TaskResult
from ..storage.sqlite_webhooks import identity


def config_identity(tracker, config, source_ids):
    sources = [
        source.model_dump(mode="json", exclude={"created_at", "updated_at"})
        for source in tracker.sources
        if source.id in source_ids
    ]
    value = {
        "tracker": tracker.model_dump(mode="json", exclude={"created_at", "updated_at", "sources"}),
        "sources": sources,
        "config": config.model_dump(mode="json") if config else None,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class FetchTasks:
    def __init__(self, storage, scheduler):
        self.storage = storage
        self.scheduler = scheduler

    async def enqueue(
        self,
        name,
        *,
        trigger_mode="manual",
        source_ids=None,
        request_ids=None,
        priority_aliases=(),
        trigger_key=None,
        initial_attempts=0,
        max_retries=None,
    ):
        tracker = await self.storage.get_aggregate_tracker(name)
        config = await self.storage.get_tracker_config(name)
        if tracker is None:
            raise ValueError("Tracker not found")
        if not tracker.enabled or (config is not None and not config.enabled):
            raise ValueError("Tracker is disabled")
        selected = sorted(
            source.id
            for source in tracker.sources
            if source.enabled
            and source.id is not None
            and (source_ids is None or source.id in source_ids)
        )
        if not selected or (source_ids is not None and set(selected) != set(source_ids)):
            raise ValueError("Tracker source is missing or disabled")
        payload = {
            "tracker_id": tracker.id,
            "tracker_name": name,
            "source_ids": selected,
            "config_identity": config_identity(tracker, config, selected),
            "trigger_mode": trigger_mode,
            "request_ids": sorted(request_ids or []),
            "priority_aliases": list(priority_aliases),
        }
        task = await self.storage.tasks.enqueue(
            kind="fetch",
            resource_key=f"tracker:{tracker.id}",
            dedupe_key=f"fetch:{tracker.id}:{','.join(map(str, selected))}",
            target_label=name,
            payload=payload,
            trigger_mode=trigger_mode,
            trigger_key=trigger_key,
            join_running=trigger_mode == "manual",
            initial_attempts=initial_attempts,
            max_retries=max_retries,
        )
        return {"task_id": task["id"], "status": task["state"]}

    async def context(self, task):
        payload = task["payload"]
        tracker = await self.storage.get_aggregate_tracker(payload["tracker_name"])
        config = await self.storage.get_tracker_config(payload["tracker_name"])
        if tracker is None or tracker.id != payload["tracker_id"]:
            return None
        if not tracker.enabled or (config is not None and not config.enabled):
            return None
        if config_identity(tracker, config, payload["source_ids"]) != payload["config_identity"]:
            return None
        return tracker, config

    async def prepare(self, task):
        if await self.context(task) is None:
            return TaskResult("superseded", "configuration_changed")
        if task["payload"]["tracker_name"] in self.scheduler._manual_checks_in_progress:
            return Deferred(time.time() + 5, "check_in_progress")
        for request in await self.requests(task):
            hook = await self.storage.webhooks.get(request["webhook_id"])
            source = await self.storage.webhooks.source(request["tracker_source_id"])
            if (
                not hook
                or not hook["enabled"]
                or hook["generation"] != request["webhook_generation"]
                or not source
                or not source[0].enabled
                or not source[2]
                or identity(source[0]) != request["source_identity"]
            ):
                return TaskResult("superseded", "configuration_changed")
        last = await self.storage.webhooks.last_source_run_at(set(task["payload"]["source_ids"]))
        if last:
            started = datetime.fromisoformat(last)
            elapsed = (datetime.now(started.tzinfo) - started).total_seconds()
            if elapsed < 30:
                return Deferred(time.time() + 30 - elapsed, "source_cooldown")
        return None

    async def requests(self, task):
        ids = task["payload"].get("request_ids", [])
        if not ids:
            return []
        db = await self.storage._get_connection()
        rows = await (
            await db.execute(
                f"""SELECT r.*,d.webhook_id FROM source_refresh_requests r
                JOIN webhook_deliveries d ON d.id=r.delivery_id WHERE r.id IN ({','.join('?' for _ in ids)})""",
                ids,
            )
        ).fetchall()
        return [dict(row) for row in rows]

    async def execute(self, task):
        context = await self.context(task)
        if context is None:
            return TaskResult("superseded", "configuration_changed")
        tracker, config = context
        name = tracker.name
        source_ids = set(task["payload"]["source_ids"])
        before = await self.storage.webhooks.source_revision_tokens(source_ids)
        previous_projection = await self.storage.get_tracker_current_releases(tracker.id)

        async def commit_guard():
            current = await self.storage.tasks.get(task["id"])
            if (
                not current
                or current["owner"] != task["owner"]
                or current["state"] != "running"
                or current["lease_until"] <= time.time()
            ):
                raise asyncio.CancelledError("Task lease lost")
            if await self.context(task) is None:
                raise asyncio.CancelledError("Source configuration changed during fetch")

        self.scheduler._manual_checks_in_progress.add(name)
        try:
            result = await self.scheduler._process_aggregate_tracker_check(
                name,
                tracker,
                config,
                trigger_mode=task["payload"]["trigger_mode"],
                source_ids=source_ids,
                container_priority_aliases=tuple(task["payload"]["priority_aliases"]),
                queued_check=True,
                commit_guard=commit_guard,
            )
            failures = result.get("source_failures", [])
            after = await self.storage.webhooks.source_revision_tokens(source_ids)
            latest_version = result.get("latest_version")

            def projection_identity(releases):
                return {
                    (r.id, r.version, r.channel_name, r.commit_sha, r.artifact_digest)
                    for r in releases
                }

            changed = before != after or projection_identity(
                previous_projection
            ) != projection_identity(result.get("releases", []))
            outcome = TaskResult(
                "succeeded" if changed else "no_change",
                result={
                    "source_fetch_run_ids": result.get("source_fetch_run_ids", {}),
                    "latest_version": latest_version,
                    "warnings": bool(result.get("error") and not failures),
                },
            )
            if failures:
                fatal = next(
                    (failure for failure in failures if not failure["retryable"]), failures[0]
                )
                outcome.state = "failed"
                outcome.code = fatal["code"]
                outcome.retryable = all(failure["retryable"] for failure in failures)
                outcome.retry_after = max(failure.get("retry_after", 0) for failure in failures)
                outcome.result["source_failures"] = failures
            await self.storage.update_tracker_status(
                TrackerStatus(
                    name=name,
                    type=_status_type(config, tracker),
                    enabled=True,
                    last_check=datetime.now(),
                    last_version=latest_version,
                    error=outcome.code,
                )
            )
            if not failures:
                # Repair a crash between projection commit and desired-state enqueue.
                from ..executor_trigger import enqueue_executor_binding_targets

                for executor in await self.storage.get_all_executor_configs():
                    await enqueue_executor_binding_targets(
                        self.storage, executor, tracker_name=name
                    )
            return outcome
        finally:
            self.scheduler._manual_checks_in_progress.discard(name)
