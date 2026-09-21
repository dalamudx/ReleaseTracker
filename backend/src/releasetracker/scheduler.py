"""Scheduler module"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from .config import TrackerConfig
from .models import Release
from .notifiers import SUPPORTED_NOTIFIER_TYPES, build_notifier
from .notifiers.base import BaseNotifier
from .scheduler_host import SchedulerHost
from .scheduler_manual_checks import ReleaseSchedulerManualChecks
from .scheduler_release_selection import ReleaseSchedulerReleaseSelection
from .scheduler_projection_notifications import ReleaseSchedulerProjectionNotifications
from .scheduler_aggregate_support import ReleaseSchedulerAggregateSupport
from .scheduler_tracker_factory import ReleaseSchedulerTrackerFactory
from .scheduler_scheduled_checks import ReleaseSchedulerScheduledChecks
from .scheduler_rebuilds import ReleaseSchedulerRebuilds
from .scheduler_aggregate_checks import ReleaseSchedulerAggregateChecks
from .storage.sqlite import SQLiteStorage
from .trackers import DockerTracker
from .trackers.base import BaseTracker

logger = logging.getLogger(__name__)
MAX_CONCURRENT_TRACKER_CHECKS = 3
MAX_CONCURRENT_FETCHES_PER_PROVIDER = {
    "github": 2,
    "gitlab": 2,
    "gitea": 2,
    "container": 2,
    "helm": 2,
}


class ReleaseScheduler(
    ReleaseSchedulerReleaseSelection,
    ReleaseSchedulerProjectionNotifications,
    ReleaseSchedulerAggregateSupport,
    ReleaseSchedulerTrackerFactory,
    ReleaseSchedulerAggregateChecks,
    ReleaseSchedulerManualChecks,
    ReleaseSchedulerScheduledChecks,
    ReleaseSchedulerRebuilds,
):
    """Release check scheduler"""

    def __init__(self, storage: SQLiteStorage, scheduler_host: SchedulerHost | None = None):
        self.storage = storage
        self.fetch_tasks = None
        self.scheduler_host = scheduler_host or SchedulerHost()
        self._job_namespace = "tracker"
        self.trackers: dict[str, BaseTracker] = {}
        self.notifiers: list[BaseNotifier] = []
        self._check_all_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TRACKER_CHECKS)
        # Shared by manual, scheduled and repository-webhook checks.
        self._manual_checks_in_progress: set[str] = set()
        self._provider_fetch_semaphores = {
            provider: asyncio.Semaphore(limit)
            for provider, limit in MAX_CONCURRENT_FETCHES_PER_PROVIDER.items()
        }

    async def _configure_container_tracker_fetch_state(
        self,
        tracker_source_id: int,
        tracker: BaseTracker,
        history_releases: list[Release] | None = None,
        *,
        priority_aliases: tuple[str, ...] = (),
    ) -> None:
        if not isinstance(tracker, DockerTracker):
            return
        if history_releases is None:
            history_releases = await self.storage.get_source_release_history_releases_by_source(
                tracker_source_id
            )
        alias_last_observed = await self.storage.get_source_alias_last_observed(tracker_source_id)
        alias_digest_by_name = await self.storage.get_source_alias_latest_digests(tracker_source_id)
        artifact_created_by_digest: dict[str, datetime] = {}
        artifact_metadata_by_digest: dict[str, dict[str, str]] = {}
        for release in history_releases:
            digest = release.artifact_digest or release.commit_sha
            if (
                isinstance(digest, str)
                and digest
                and release.published_at_source == "artifact_created"
            ):
                artifact_created_by_digest[digest] = release.published_at
            if isinstance(digest, str) and digest:
                metadata = {
                    key: value
                    for key, value in {
                        "version": release.oci_version,
                        "revision": release.oci_revision,
                        "source": release.oci_source,
                    }.items()
                    if value
                }
                if metadata:
                    artifact_metadata_by_digest[digest] = metadata
        tracker.configure_incremental_fetch(
            alias_last_observed=alias_last_observed,
            alias_digest_by_name=alias_digest_by_name,
            artifact_created_by_digest=artifact_created_by_digest,
            artifact_metadata_by_digest=artifact_metadata_by_digest,
            priority_aliases=priority_aliases,
        )

    async def initialize(self):
        """Initialize schedulers"""
        # Load tracker configuration from the database
        tracker_configs = await self.storage.get_all_tracker_configs()

        for tracker_config in tracker_configs:
            await self._add_or_update_tracker_job(tracker_config)

        # Load notifiers from the database
        await self._refresh_notifiers()

    async def _refresh_notifiers(self):
        """Refresh the notifier list"""
        self.notifiers = []
        try:
            db_notifiers = await self.storage.get_notifiers()
            for n in db_notifiers:
                if n.enabled and n.type in SUPPORTED_NOTIFIER_TYPES:
                    self.notifiers.append(
                        build_notifier(
                            notifier_type=n.type,
                            name=n.name,
                            url=n.url,
                            events=n.events,
                            language=n.language,
                        )
                    )
        except Exception as e:
            logger.error(f"Failed to load notifiers: {e}")

    async def refresh_tracker(self, name: str):
        """Refresh a single tracker after configuration updates."""
        tracker_config = await self.storage.get_tracker_config(name)
        if tracker_config:
            await self._add_or_update_tracker_job(tracker_config)

    async def refresh_container_tracker_redirect_settings(self) -> int:
        """Recreate cached container trackers after global redirect setting changes.

        In-flight checks keep the tracker object they already captured; replacing
        dictionary entries only affects subsequent checks. Non-container cached
        trackers are intentionally left untouched.
        """
        refreshed_count = 0
        cached_items = list(self.trackers.items())
        for tracker_name, cached_tracker in cached_items:
            if not isinstance(cached_tracker, DockerTracker):
                continue
            tracker_config = await self.storage.get_tracker_config(tracker_name)
            if tracker_config is None or tracker_config.type != "container":
                continue
            self.trackers[tracker_name] = await self._create_tracker(tracker_config)
            refreshed_count += 1
        return refreshed_count

    async def remove_tracker(self, name: str):
        """Remove a tracker"""
        if name in self.trackers:
            del self.trackers[name]

        self.scheduler_host.remove_job(self._job_namespace, name)

    async def _add_or_update_tracker_job(self, tracker_config):
        """Add or update a tracker job"""
        tracker = await self._create_tracker(tracker_config)
        self.trackers[tracker_config.name] = tracker

        # Add or update the scheduled job
        # interval unit is minutes; convert to seconds
        interval_seconds = tracker_config.interval * 60

        self.scheduler_host.add_interval_job(
            self._job_namespace,
            tracker_config.name,
            self._check_tracker,
            seconds=interval_seconds,
            args=[tracker_config.name],
        )

    async def start(self):
        """Start the scheduler"""
        await self.scheduler_host.start()
        logger.info("Scheduler started")

    async def check_all(self):
        """Check all trackers"""

        async def _bounded_check(name: str):
            async with self._check_all_semaphore:
                return await self._check_tracker(name)

        tasks = [_bounded_check(name) for name in self.trackers.keys()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_tracker_check(
        self,
        tracker_name: str,
        tracker: BaseTracker,
        tracker_config: TrackerConfig | None,
        *,
        log_prefix: str = "",
        trigger_mode: str = "scheduled",
    ) -> dict[str, Any]:
        aggregate_for_fetch = await self.storage.get_aggregate_tracker(tracker_name)
        if aggregate_for_fetch is not None:
            runtime_source_for_fetch = self.storage._select_runtime_source(aggregate_for_fetch)
            if runtime_source_for_fetch is not None and runtime_source_for_fetch.id is not None:
                await self._configure_container_tracker_fetch_state(
                    runtime_source_for_fetch.id, tracker
                )

        releases = await self._fetch_tracker_releases(
            tracker_name,
            tracker,
            tracker_config,
            log_prefix=log_prefix,
        )

        if tracker_config is None:
            return {
                "releases": releases,
                "latest_version": None,
                "error": None,
            }

        aggregate_tracker = await self.storage.get_aggregate_tracker(tracker_name)
        if aggregate_tracker is None or aggregate_tracker.id is None:
            return {
                "releases": releases,
                "latest_version": None,
                "error": None,
            }

        runtime_source = self.storage._select_runtime_source(aggregate_tracker)
        if runtime_source is None or runtime_source.id is None:
            raise ValueError("No available data source found")

        sort_mode = tracker_config.version_sort_mode if tracker_config else "published_at"
        fetched_releases = [
            release.model_copy(
                update={
                    "tracker_name": tracker_name,
                    "tracker_type": runtime_source.source_type,
                }
            )
            for release in releases
        ]
        if tracker_config.channels:
            fetched_releases = self._assign_first_matching_channel(
                self.storage,
                fetched_releases,
                tracker_config.channels,
                source_type=runtime_source.source_type,
            )
        filtered_releases = [
            release for release in fetched_releases if tracker._should_include(release)
        ]
        if not tracker_config.channels:
            filtered_releases = [
                release.model_copy(
                    update={"channel_name": "prerelease" if release.prerelease else "stable"}
                )
                for release in filtered_releases
            ]

        source_fetch_run_id = await self.storage.create_source_fetch_run(
            runtime_source.id,
            trigger_mode=trigger_mode,
        )
        source_history_ids_by_identity: dict[str, int] = {}
        fallback_hint = getattr(tracker, "last_fallback_hint", None)
        try:
            source_history_ids_by_identity = await self.storage.append_source_history_for_run(
                source_fetch_run_id,
                runtime_source,
                fetched_releases,
                aggregate_tracker_id=aggregate_tracker.id,
            )
            await self.storage.finalize_source_fetch_run(
                source_fetch_run_id,
                status="partial" if fallback_hint else "success",
                fetched_count=len(fetched_releases),
                filtered_in_count=len(filtered_releases),
                error_message=fallback_hint,
            )
        except Exception as exc:
            await self.storage.finalize_source_fetch_run(
                source_fetch_run_id,
                status="failed",
                fetched_count=len(fetched_releases),
                filtered_in_count=0,
                error_message=str(exc),
            )
            raise

        history_releases = await self.storage.get_tracker_release_history_releases(
            aggregate_tracker.id
        )
        for release in history_releases:
            release.tracker_name = tracker_name

        releases_to_persist = self.storage.dedupe_releases_by_immutable_identity(filtered_releases)

        releases_for_source_projection = history_releases + filtered_releases
        await self.storage.save_source_observations(
            aggregate_tracker.id,
            runtime_source,
            releases_for_source_projection,
            append_truth=False,
        )

        for release in self.storage.dedupe_releases_by_immutable_identity(releases_to_persist):
            identity_key = self.storage.release_identity_key_for_source(
                release,
                source_type=runtime_source.source_type,
            )
            source_history_id = source_history_ids_by_identity.get(identity_key)
            if source_history_id is None:
                source_history_id = await self.storage.get_source_release_history_id(
                    runtime_source.id,
                    identity_key,
                )
            if source_history_id is None:
                continue
            await self.storage.upsert_tracker_release_history(
                aggregate_tracker.id,
                release,
                primary_source_release_history_id=source_history_id,
                source_type=runtime_source.source_type,
            )

        projection_releases, latest_version = await self._refresh_tracker_projection_and_notify(
            aggregate_tracker_id=aggregate_tracker.id,
            tracker_name=tracker_name,
            channels=tracker_config.channels,
            sort_mode=sort_mode,
        )

        error = None
        if fallback_hint:
            error = f"Partial source check failed: {runtime_source.source_key}: {fallback_hint}"

        return {
            "releases": projection_releases,
            "latest_version": latest_version,
            "error": error,
        }
