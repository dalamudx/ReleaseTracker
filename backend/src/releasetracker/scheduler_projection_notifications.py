from __future__ import annotations

import asyncio
import logging
from typing import Any

from .executor_trigger import enqueue_executor_binding_targets
from .models import Release
from .notifiers import WebhookNotifier
from .notifiers.base import NotificationEvent

logger = logging.getLogger(__name__)


class ReleaseSchedulerProjectionNotifications:
    """Refresh release projections, enqueue executor work, and send notifications."""

    async def _emit_executor_trigger_work_for_projection_change(
        self,
        *,
        tracker_name: str,
        previous_version: str | None,
        current_version: str,
        previous_identity_key: str | None,
        current_identity_key: str,
    ) -> int:
        # The tracker-wide winner is only appropriate for release notifications.
        # Execution work must use each executor's bound source/channel target.
        del previous_version, current_version, previous_identity_key, current_identity_key
        queued_count = 0
        for executor_config in await self.storage.get_all_executor_configs():
            if await enqueue_executor_binding_targets(
                self.storage, executor_config, tracker_name=tracker_name
            ):
                queued_count += 1
        return queued_count

    async def _refresh_tracker_projection_and_notify(
        self,
        *,
        aggregate_tracker_id: int,
        tracker_name: str,
        channels: list[Any],
        sort_mode: str,
    ) -> tuple[list[Release], str | None]:
        previous_projection = await self.storage.get_tracker_current_releases(aggregate_tracker_id)
        for release in previous_projection:
            release.tracker_name = tracker_name

        previous_best = self._best_release_from_candidates(
            self.storage,
            previous_projection,
            channels,
            sort_mode,
        )

        history_releases = await self.storage.get_tracker_release_history_releases(
            aggregate_tracker_id
        )
        for release in history_releases:
            release.tracker_name = tracker_name

        if channels:
            projection_winners = list(
                self.storage.select_best_releases_by_channel(
                    history_releases,
                    channels,
                    sort_mode=sort_mode,
                    use_immutable_identity=True,
                ).values()
            )
        else:
            projection_winners = self.storage.dedupe_releases_by_immutable_identity(
                history_releases
            )

        await self.storage.refresh_tracker_current_releases(
            aggregate_tracker_id,
            projection_winners,
        )

        current_best = self._best_release_from_candidates(
            self.storage,
            projection_winners,
            channels,
            sort_mode,
        )
        current_best_identity = (
            self.storage.release_identity_key_for_source(current_best)
            if current_best is not None
            else None
        )
        previous_best_identity = (
            self.storage.release_identity_key_for_source(previous_best)
            if previous_best is not None
            else None
        )

        winner_changed = current_best_identity != previous_best_identity

        # Always reconcile bound executor targets. A stable or canary change can
        # be masked by a newer prerelease in the tracker-wide winner.
        queued_count = 0
        if current_best is not None:
            queued_count = await self._emit_executor_trigger_work_for_projection_change(
                tracker_name=tracker_name,
                previous_version=previous_best.version if previous_best is not None else None,
                current_version=current_best.version,
                previous_identity_key=previous_best_identity,
                current_identity_key=current_best_identity or current_best.version,
            )
        if queued_count > 0:
            logger.info(
                "Queued %s executor trigger work item(s) for tracker %s",
                queued_count,
                tracker_name,
            )
        if winner_changed and current_best is not None:
            if previous_best is not None and previous_best.version == current_best.version:
                await self._send_notifications(NotificationEvent.REPUBLISH, current_best)
            else:
                await self._send_notifications(NotificationEvent.NEW_RELEASE, current_best)

        return projection_winners, current_best.version if current_best is not None else None

    async def _send_notifications(self, event: str, release):
        """Send a notification with fresh notifiers from the database each time."""
        logger.info(
            f"Preparing to send notifications for event: {event}, release: {release.version}"
        )

        active_notifiers = []

        # Load directly from the database instead of self.notifiers cache to avoid duplicate sends
        try:
            db_notifiers = await self.storage.get_notifiers()
            logger.debug(f"Found {len(db_notifiers)} notifiers in DB")

            for n in db_notifiers:
                logger.debug(
                    f"Checking notifier: {n.name}, enabled: {n.enabled}, type: {n.type}, events: {n.events}"
                )
                if n.enabled and n.type == "webhook":
                    active_notifiers.append(
                        WebhookNotifier(
                            name=n.name,
                            url=n.url,
                            events=n.events,
                            language=n.language,
                        )
                    )
        except Exception as e:
            logger.error(f"Failed to load notifiers from DB: {e}")

        logger.info(f"Active notifiers count: {len(active_notifiers)}")

        if not active_notifiers:
            logger.warning("No active notifiers found to send notification")
            return

        tasks = [notifier.notify(event, release) for notifier in active_notifiers]
        await asyncio.gather(*tasks, return_exceptions=True)
