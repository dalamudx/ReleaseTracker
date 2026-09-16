"""Persistent repository-webhook refresh worker."""

from __future__ import annotations

import logging
import time
from datetime import datetime

from .models import TrackerStatus
from .scheduler_status import _status_type
from .storage.sqlite_webhooks import identity

logger = logging.getLogger(__name__)


class RepositoryWebhookScheduler:
    def __init__(self, storage, release_scheduler, scheduler_host):
        self.storage = storage
        self.release_scheduler = release_scheduler
        self.scheduler_host = scheduler_host
        self._running = False

    async def initialize(self):
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
        if self._running:
            return
        self._running = True
        try:
            requests = await self.storage.webhooks.claim()
            if requests:
                await self._process(requests)
        except Exception:
            logger.exception("Repository webhook worker failed")
        finally:
            self._running = False

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
        self.release_scheduler._manual_checks_in_progress.add(tracker_name)
        try:
            result = await self.release_scheduler._process_aggregate_tracker_check(
                tracker_name,
                tracker,
                config,
                log_prefix="Webhook ",
                trigger_mode="webhook",
                source_ids=source_ids,
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
                    attempt=True,
                )
            else:
                await self.storage.webhooks.finish(valid, "failed", error, attempt=True)
        finally:
            self.release_scheduler._manual_checks_in_progress.discard(tracker_name)
