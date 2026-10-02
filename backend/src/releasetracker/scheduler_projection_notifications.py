from __future__ import annotations

import logging
from typing import Any

from .executor_trigger import enqueue_executor_binding_targets
from .models import Release
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
        enqueue_executors: bool = True,
    ) -> tuple[list[Release], str | None]:
        async with self.storage.tasks.transaction():
            previous_projection = await self.storage.get_tracker_current_releases(
                aggregate_tracker_id
            )
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
                        use_source_aliases=True,
                    ).values()
                )
            else:
                projection_winners = self.storage.dedupe_releases_by_immutable_identity(
                    history_releases
                )

            await self.storage.refresh_tracker_current_releases(
                aggregate_tracker_id,
                projection_winners,
                commit=False,
            )

            current_best = self._best_release_from_candidates(
                self.storage,
                projection_winners,
                channels,
                sort_mode,
            )
            current_best_identity = (
                self._projection_release_identity(current_best)
                if current_best is not None
                else None
            )
            previous_best_identity = (
                self._projection_release_identity(previous_best)
                if previous_best is not None
                else None
            )

            winner_changed = self._projection_release_changed(previous_best, current_best)

            notified: set[str] = set()
            if winner_changed and current_best is not None:
                await self._notify_change(previous_best, current_best)
                notified.add(self._projection_release_identity(current_best))
            if channels:
                # A newer prerelease can own the tracker-wide winner while a stable
                # channel also moves. Notify each changed channel winner once.
                previous_by_channel = {
                    r.channel_name: r for r in previous_projection if r.channel_name
                }
                for release in projection_winners:
                    identity = self._projection_release_identity(release)
                    if not release.channel_name or identity in notified:
                        continue
                    previous = previous_by_channel.get(release.channel_name)
                    if self._projection_release_changed(previous, release):
                        release.tracker_name = tracker_name
                        await self._notify_change(previous, release)
                        notified.add(identity)

        # Always reconcile bound executor targets. A stable or canary change can
        # be masked by a newer prerelease in the tracker-wide winner.
        queued_count = 0
        if enqueue_executors and current_best is not None:
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
        return projection_winners, current_best.version if current_best is not None else None

    async def _notify_change(self, previous: Release | None, current: Release) -> None:
        if previous is not None and previous.version == current.version:
            await self._send_notifications(NotificationEvent.REPUBLISH, current)
        else:
            await self._send_notifications(NotificationEvent.NEW_RELEASE, current)

    def _projection_release_identity(self, release: Release) -> str:
        # Notifications describe logical releases, not deployment artifacts. A new
        # repository release may intentionally reuse an existing image digest.
        if release.id is not None:
            return f"history:{release.id}"
        return self.storage.release_identity_key_for_source(release)

    def _projection_release_changed(
        self, previous: Release | None, current: Release | None
    ) -> bool:
        if previous is None or current is None:
            return previous is not current
        if self._projection_release_identity(previous) != self._projection_release_identity(
            current
        ):
            return True
        previous_digest = str(previous.artifact_digest or "").strip().lower() or None
        current_digest = str(current.artifact_digest or "").strip().lower() or None
        # None -> digest is metadata enrichment after alias discovery, not a republish.
        return (
            previous_digest is not None
            and current_digest is not None
            and previous_digest != current_digest
        )

    async def _send_notifications(self, event: str, release):
        """Queue durable, de-duplicated delivery; never send inline from a fetch."""
        from .services.release_notification_outbox import enqueue_release_notification

        db = await self.storage._get_connection()
        queued = await enqueue_release_notification(
            self.storage, event, release, commit=not db.in_transaction
        )
        if queued:
            logger.info(
                "Queued %s %s notification(s) for %s %s",
                queued,
                event,
                release.tracker_name,
                release.version,
            )
