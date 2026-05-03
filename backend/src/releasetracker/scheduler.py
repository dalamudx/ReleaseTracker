"""Scheduler module"""

import asyncio
import inspect
import logging
from datetime import datetime
from typing import Any, cast

from .config import TrackerConfig
from .models import (
    AggregateTracker,
    Credential,
    Release,
    SourceReleaseObservation,
    TrackerSource,
    TrackerSourceType,
    TrackerStatus,
)
from .notifiers import WebhookNotifier
from .notifiers.base import BaseNotifier, NotificationEvent
from .scheduler_host import SchedulerHost
from .storage.sqlite import SQLiteStorage
from .trackers import GitHubTracker, GitLabTracker, GiteaTracker, HelmTracker, DockerTracker
from .trackers.base import BaseTracker

logger = logging.getLogger(__name__)
MAX_CONCURRENT_TRACKER_CHECKS = 3
MANUAL_CHECK_COOLDOWN_SECONDS = 30
MAX_CONCURRENT_FETCHES_PER_PROVIDER = {
    "github": 2,
    "gitlab": 2,
    "gitea": 2,
    "container": 2,
    "helm": 2,
}


def _tracker_method_supports_argument(method: Any, argument_name: str) -> bool:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return False

    return argument_name in signature.parameters


def _tracker_channel_count(config: TrackerConfig | None) -> int:
    return len(config.channels) if config and config.channels else 0


def _credential_secret_string(credential: Credential, key: str) -> str:
    value = credential.secrets.get(key)
    return value.strip() if isinstance(value, str) else ""


def _container_registry_auth_token(credential: Credential) -> str:
    username = _credential_secret_string(credential, "username")
    password = _credential_secret_string(credential, "password") or credential.token.strip()
    if username and password:
        return f"{username}:{password}"
    return credential.token


def _status_type(
    tracker_config: TrackerConfig | None, aggregate_tracker: AggregateTracker | None
) -> TrackerSourceType:
    if tracker_config is not None:
        return tracker_config.type

    if aggregate_tracker is not None:
        enabled_sources = [source for source in aggregate_tracker.sources if source.enabled]
        if enabled_sources:
            return cast(TrackerSourceType, enabled_sources[0].source_type)
        if aggregate_tracker.sources:
            return cast(TrackerSourceType, aggregate_tracker.sources[0].source_type)

    return cast(TrackerSourceType, "github")


class ReleaseScheduler:
    """Release check scheduler"""

    def __init__(self, storage: SQLiteStorage, scheduler_host: SchedulerHost | None = None):
        self.storage = storage
        self.scheduler_host = scheduler_host or SchedulerHost()
        self._job_namespace = "tracker"
        self.trackers: dict[str, BaseTracker] = {}
        self.notifiers: list[BaseNotifier] = []
        self._check_all_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TRACKER_CHECKS)
        self._manual_checks_in_progress: set[str] = set()
        self._provider_fetch_semaphores = {
            provider: asyncio.Semaphore(limit)
            for provider, limit in MAX_CONCURRENT_FETCHES_PER_PROVIDER.items()
        }

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
                if n.enabled and n.type == "webhook":
                    notifier = WebhookNotifier(
                        name=n.name,
                        url=n.url,
                        events=n.events,
                        language=n.language,
                    )
                    self.notifiers.append(notifier)
        except Exception as e:
            logger.error(f"Failed to load notifiers: {e}")

    async def refresh_tracker(self, name: str):
        """Refresh a single tracker after configuration updates."""
        tracker_config = await self.storage.get_tracker_config(name)
        if tracker_config:
            await self._add_or_update_tracker_job(tracker_config)

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

    async def _fetch_tracker_releases(
        self,
        tracker_name: str,
        tracker: BaseTracker,
        tracker_config: TrackerConfig | None,
        *,
        log_prefix: str,
    ) -> list[Any]:
        releases: list[Any] = []
        provider = (
            tracker_config.type
            if tracker_config
            else cast(str | None, getattr(tracker, "tracker_type", None))
        )
        semaphore = self._provider_fetch_semaphores.get(provider) if provider else None

        async def _do_fetch() -> list[Any]:
            local_releases: list[Any] = []
            fallback_tags = tracker_config.fallback_tags if tracker_config else False

            try:
                limit = tracker_config.fetch_limit if tracker_config else 30
                local_releases = await tracker.fetch_all(limit=limit, fallback_tags=fallback_tags)
            except Exception as inner_e:
                logger.warning(
                    f"{log_prefix}fetch_all failed for {tracker_name} ({inner_e.__class__.__name__}: {inner_e}), trying fallback"
                )

            if not local_releases:
                try:
                    if _tracker_method_supports_argument(tracker.fetch_latest, "fallback_tags"):
                        single_latest = await tracker.fetch_latest(fallback_tags=fallback_tags)
                    else:
                        single_latest = await tracker.fetch_latest()
                    if single_latest:
                        local_releases = [single_latest]
                except Exception as fb_e:
                    raise Exception(
                        f"Fallback fetch_latest failed: {str(fb_e) or getattr(fb_e, '__class__', Exception).__name__}"
                    )

            return local_releases

        if semaphore:
            async with semaphore:
                releases = await _do_fetch()
        else:
            releases = await _do_fetch()

        return releases

    @staticmethod
    def _best_release_from_candidates(
        storage: SQLiteStorage,
        releases: list[Release],
        channels: list[Any],
        sort_mode: str,
    ) -> Release | None:
        if not releases:
            return None
        if channels:
            return storage.select_best_release(
                releases,
                channels,
                sort_mode=sort_mode,
                use_immutable_identity=True,
            )
        return max(
            storage.dedupe_releases_by_immutable_identity(releases),
            key=lambda release: storage._release_order_key(release, sort_mode),
        )

    @staticmethod
    def _assign_first_matching_channel(
        storage: SQLiteStorage,
        releases: list[Release],
        channels: list[Any],
        *,
        source_type: str | None = None,
    ) -> list[Release]:
        if not channels:
            return releases

        assigned_releases: list[Release] = []
        for release in releases:
            channel_name = None
            for channel in channels:
                if isinstance(channel, dict):
                    if not channel.get("enabled", True):
                        continue
                    candidate_channel_name = channel.get("name")
                    channel_source_type = channel.get("source_type") or source_type
                else:
                    if not channel.enabled:
                        continue
                    candidate_channel_name = channel.name
                    channel_source_type = getattr(channel, "source_type", None) or source_type

                if not candidate_channel_name:
                    continue
                if storage._release_matches_channel(
                    release,
                    channel,
                    channel_source_type=channel_source_type,
                ):
                    channel_name = str(candidate_channel_name)
                    break

            assigned_releases.append(
                release.model_copy(update={"channel_name": channel_name})
                if channel_name
                else release
            )

        return assigned_releases

    async def _emit_executor_trigger_work_for_projection_change(
        self,
        *,
        tracker_name: str,
        previous_version: str | None,
        current_version: str,
        previous_identity_key: str | None,
        current_identity_key: str,
    ) -> int:
        queued_count = 0
        for executor_config in await self.storage.get_all_executor_configs():
            if executor_config.id is None:
                continue
            if not executor_config.enabled:
                continue
            matches_tracker = executor_config.tracker_name == tracker_name
            if not matches_tracker and executor_config.service_bindings:
                for binding in executor_config.service_bindings:
                    binding_target = await self.storage.get_executor_binding(
                        binding.tracker_source_id
                    )
                    if binding_target is None:
                        continue
                    binding_tracker, _binding_source = binding_target
                    if binding_tracker.name == tracker_name:
                        matches_tracker = True
                        break

            if not matches_tracker:
                continue
            if executor_config.update_mode == "manual":
                continue

            enqueued = await self.storage.enqueue_executor_projection_trigger_work(
                executor_id=executor_config.id,
                tracker_name=tracker_name,
                previous_version=previous_version,
                current_version=current_version,
                previous_identity_key=previous_identity_key,
                current_identity_key=current_identity_key,
            )
            if enqueued:
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

        if winner_changed and current_best is not None and current_best_identity is not None:
            queued_count = await self._emit_executor_trigger_work_for_projection_change(
                tracker_name=tracker_name,
                previous_version=previous_best.version if previous_best is not None else None,
                current_version=current_best.version,
                previous_identity_key=previous_best_identity,
                current_identity_key=current_best_identity,
            )
            if queued_count > 0:
                logger.info(
                    "Queued %s executor trigger work item(s) for tracker %s (%s -> %s)",
                    queued_count,
                    tracker_name,
                    previous_best.version if previous_best is not None else "<none>",
                    current_best.version,
                )

        if winner_changed and current_best is not None:
            if previous_best is not None and previous_best.version == current_best.version:
                await self._send_notifications(NotificationEvent.REPUBLISH, current_best)
            else:
                await self._send_notifications(NotificationEvent.NEW_RELEASE, current_best)

        return projection_winners, current_best.version if current_best is not None else None

    async def _process_tracker_check(
        self,
        tracker_name: str,
        tracker: BaseTracker,
        tracker_config: TrackerConfig | None,
        *,
        log_prefix: str = "",
        trigger_mode: str = "scheduled",
    ) -> dict[str, Any]:
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
            raise ValueError("未找到可用的数据源")

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

        releases_for_source_projection = self.storage.dedupe_releases_by_immutable_identity(
            history_releases + releases_to_persist
        )
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
            error = f"部分来源检查失败: {runtime_source.source_key}: {fallback_hint}"

        return {
            "releases": projection_releases,
            "latest_version": latest_version,
            "error": error,
        }

    @staticmethod
    def _should_use_aggregate_path(aggregate_tracker: AggregateTracker | None) -> bool:
        if aggregate_tracker is None:
            return False

        enabled_sources = [source for source in aggregate_tracker.sources if source.enabled]
        return bool(enabled_sources)

    def _require_aggregate_tracker_for_live_check(
        self, tracker_name: str, aggregate_tracker: AggregateTracker | None
    ) -> AggregateTracker:
        if aggregate_tracker is None:
            raise ValueError(
                f"Legacy-only tracker state is not supported for live checks: {tracker_name}"
            )
        if not self._should_use_aggregate_path(aggregate_tracker):
            raise ValueError(f"聚合追踪器没有启用的数据源: {tracker_name}")
        return aggregate_tracker

    @staticmethod
    def _canonical_release_to_release(
        tracker_name: str, tracker_type: TrackerSourceType, canonical_release
    ) -> Release:
        return Release(
            tracker_name=tracker_name,
            tracker_type=tracker_type,
            name=canonical_release.name,
            tag_name=canonical_release.tag_name,
            version=canonical_release.version,
            published_at=canonical_release.published_at,
            url=canonical_release.url,
            prerelease=canonical_release.prerelease,
            body=canonical_release.body,
        )

    def _make_source_tracker_config(
        self,
        tracker_name: str,
        source: TrackerSource,
        tracker_config: TrackerConfig | None,
        primary_changelog_source_key: str | None = None,
    ) -> TrackerConfig:
        _ = primary_changelog_source_key
        effective_channels = source.release_channels

        channels = [
            channel.model_dump() if hasattr(channel, "model_dump") else channel
            for channel in effective_channels
        ]
        return TrackerConfig(
            name=tracker_name,
            type=cast(TrackerSourceType, source.source_type),
            enabled=source.enabled,
            repo=source.source_config.get("repo"),
            project=source.source_config.get("project"),
            instance=source.source_config.get("instance"),
            chart=source.source_config.get("chart"),
            image=source.source_config.get("image"),
            registry=source.source_config.get("registry"),
            credential_name=source.credential_name,
            interval=tracker_config.interval if tracker_config else 360,
            version_sort_mode=(
                tracker_config.version_sort_mode if tracker_config else "published_at"
            ),
            fetch_limit=tracker_config.fetch_limit if tracker_config else 10,
            fetch_timeout=tracker_config.fetch_timeout if tracker_config else 15,
            fallback_tags=tracker_config.fallback_tags if tracker_config else False,
            github_fetch_mode=source.source_config.get("fetch_mode")
            or (tracker_config.github_fetch_mode if tracker_config else "rest_first"),
            channels=cast(list[Any], channels),
        )

    @staticmethod
    def _source_observation_to_release(
        tracker_name: str, source: TrackerSource, observation: SourceReleaseObservation
    ) -> Release:
        return Release(
            tracker_name=tracker_name,
            tracker_type=source.source_type,
            name=observation.name,
            tag_name=observation.tag_name,
            version=observation.version,
            app_version=observation.app_version,
            chart_version=observation.chart_version,
            published_at=observation.published_at,
            url=observation.url,
            changelog_url=observation.changelog_url,
            prerelease=observation.prerelease,
            body=observation.body,
            commit_sha=observation.commit_sha,
            created_at=observation.created_at,
        )

    async def _process_aggregate_tracker_local_rebuild(
        self,
        tracker_name: str,
        aggregate_tracker: AggregateTracker,
        tracker_config: TrackerConfig | None,
    ) -> dict[str, Any]:
        enabled_sources = [source for source in aggregate_tracker.sources if source.enabled]
        if not enabled_sources:
            raise ValueError("聚合追踪器没有启用的数据源")
        if aggregate_tracker.id is None:
            raise ValueError("聚合追踪器缺少持久化 ID")

        projection_releases = await self.storage.get_tracker_current_releases(aggregate_tracker.id)
        for release in projection_releases:
            release.tracker_name = tracker_name

        history_releases = await self.storage.get_tracker_release_history_releases(
            aggregate_tracker.id
        )
        for release in history_releases:
            release.tracker_name = tracker_name

        candidate_releases = projection_releases if projection_releases else history_releases

        sort_mode = tracker_config.version_sort_mode if tracker_config else "published_at"
        selection_candidates: list[Release]
        if tracker_config is not None and tracker_config.channels:
            selection_candidates = list(
                self.storage.select_best_releases_by_channel(
                    history_releases,
                    tracker_config.channels,
                    sort_mode=sort_mode,
                    use_immutable_identity=True,
                ).values()
            )
        else:
            selection_candidates = self.storage.dedupe_releases_by_immutable_identity(
                history_releases
            )

        best_release = None
        if selection_candidates:
            best_release = max(
                self.storage.dedupe_releases_by_immutable_identity(selection_candidates),
                key=lambda release: self.storage._release_order_key(
                    release,
                    sort_mode,
                ),
            )
        elif candidate_releases:
            best_release = max(
                self.storage.dedupe_releases_by_immutable_identity(candidate_releases),
                key=lambda release: self.storage._release_order_key(
                    release,
                    sort_mode,
                ),
            )

        return {
            "releases": candidate_releases,
            "latest_version": best_release.version if best_release else None,
            "error": None,
        }

    async def rebuild_tracker_views_from_storage(self, name: str) -> TrackerStatus:
        config = await self.storage.get_tracker_config(name)
        aggregate_tracker = await self.storage.get_aggregate_tracker(name)
        if not config and aggregate_tracker is None:
            raise ValueError(f"Tracker {name} not found")

        current_status = await self.storage.get_tracker_status(name)
        preserved_last_check = current_status.last_check if current_status else None
        latest_version = current_status.last_version if current_status else None

        tracker_type = _status_type(config, aggregate_tracker)
        aggregate_enabled = aggregate_tracker.enabled if aggregate_tracker is not None else True
        tracker_enabled = config.enabled if config is not None else True
        if not tracker_enabled or not aggregate_enabled:
            status = TrackerStatus(
                name=name,
                type=tracker_type,
                enabled=False,
                last_check=preserved_last_check,
                last_version=latest_version,
                error="追踪器已禁用",
            )
            await self.storage.update_tracker_status(status)
            return status

        if aggregate_tracker is None:
            raise ValueError(f"Aggregate tracker missing during local rebuild: {name}")

        result = await self._process_aggregate_tracker_local_rebuild(
            name, aggregate_tracker, config
        )
        releases = result["releases"]
        if result["latest_version"]:
            latest_version = result["latest_version"]

        status = TrackerStatus(
            name=name,
            type=tracker_type,
            enabled=True,
            last_check=preserved_last_check,
            last_version=latest_version,
            error=(
                result.get("error")
                if releases or latest_version
                else (result.get("error") or "未找到版本信息")
            ),
            channel_count=_tracker_channel_count(config),
        )
        await self.storage.update_tracker_status(status)
        return status

    async def _process_aggregate_tracker_check(
        self,
        tracker_name: str,
        aggregate_tracker: AggregateTracker,
        tracker_config: TrackerConfig | None,
        *,
        log_prefix: str = "",
        trigger_mode: str = "scheduled",
    ) -> dict[str, Any]:
        enabled_sources = [source for source in aggregate_tracker.sources if source.enabled]
        if not enabled_sources:
            raise ValueError("聚合追踪器没有启用的数据源")
        if aggregate_tracker.id is None:
            raise ValueError("聚合追踪器缺少持久化 ID")

        source_errors: list[str] = []
        selection_candidates: list[Release] = []
        candidate_sources: dict[str, list[tuple[TrackerSource, Release]]] = {}
        sort_mode = tracker_config.version_sort_mode if tracker_config else "published_at"

        for source in enabled_sources:
            source_config = self._make_source_tracker_config(
                tracker_name,
                source,
                tracker_config,
                aggregate_tracker.primary_changelog_source_key,
            )
            if source.id is None:
                continue

            source_history_releases = (
                await self.storage.get_source_release_history_releases_by_source(source.id)
            )
            for history_release in source_history_releases:
                history_release.tracker_name = tracker_name
                history_release.tracker_type = source.source_type

            source_fetch_run_id = await self.storage.create_source_fetch_run(
                source.id,
                trigger_mode=trigger_mode,
            )
            source_history_ids_by_identity: dict[str, int] = {}

            try:
                source_tracker = await self._create_tracker(source_config)
                if source_config.channels:
                    source_history_releases = self._assign_first_matching_channel(
                        self.storage,
                        source_history_releases,
                        source_config.channels,
                        source_type=source.source_type,
                    )
                eligible_source_history_releases = [
                    release
                    for release in source_history_releases
                    if source_tracker._should_include(release)
                ]
                fetched_releases = await self._fetch_tracker_releases(
                    tracker_name,
                    source_tracker,
                    source_config,
                    log_prefix=f"{log_prefix}[{source.source_key}] ",
                )

                mapped_releases = [
                    release.model_copy(
                        update={
                            "tracker_name": tracker_name,
                            "tracker_type": source.source_type,
                        }
                    )
                    for release in fetched_releases
                ]
                if source_config.channels:
                    mapped_releases = self._assign_first_matching_channel(
                        self.storage,
                        mapped_releases,
                        source_config.channels,
                        source_type=source.source_type,
                    )
                filtered_releases = [
                    release
                    for release in mapped_releases
                    if source_tracker._should_include(release)
                ]

                fallback_hint = getattr(source_tracker, "last_fallback_hint", None)
                if fallback_hint:
                    source_errors.append(f"{source.source_key}: {fallback_hint}")

                source_history_ids_by_identity = await self.storage.append_source_history_for_run(
                    source_fetch_run_id,
                    source,
                    mapped_releases,
                    aggregate_tracker_id=aggregate_tracker.id,
                )
                await self.storage.finalize_source_fetch_run(
                    source_fetch_run_id,
                    status="partial" if fallback_hint else "success",
                    fetched_count=len(mapped_releases),
                    filtered_in_count=len(filtered_releases),
                    error_message=fallback_hint,
                )

                eligible_releases_for_history = self.storage.dedupe_releases_by_immutable_identity(
                    eligible_source_history_releases + filtered_releases
                )
                releases_for_source_projection = self.storage.dedupe_releases_by_immutable_identity(
                    eligible_source_history_releases + filtered_releases
                )

                await self.storage.save_source_observations(
                    aggregate_tracker.id,
                    source,
                    releases_for_source_projection,
                    append_truth=False,
                )

                selection_candidates.extend(eligible_releases_for_history)
                for release in eligible_releases_for_history:
                    identity_key = self.storage.release_identity_key_for_source(
                        release,
                        source_type=source.source_type,
                    )
                    source_history_id = source_history_ids_by_identity.get(identity_key)
                    if source_history_id is None:
                        source_history_id = await self.storage.get_source_release_history_id(
                            source.id,
                            identity_key,
                        )
                    if source_history_id is None:
                        continue
                    candidate_sources.setdefault(identity_key, []).append((source, release))
            except Exception as e:
                error_msg = str(e) or getattr(e, "__class__", Exception).__name__
                logger.error(
                    f"{log_prefix}Aggregate source check failed for {tracker_name}/{source.source_key}: {error_msg}"
                )
                source_errors.append(f"{source.source_key}: {error_msg}")
                await self.storage.finalize_source_fetch_run(
                    source_fetch_run_id,
                    status="failed",
                    fetched_count=0,
                    filtered_in_count=0,
                    error_message=error_msg,
                )

        history_releases = await self.storage.get_tracker_release_history_releases(
            aggregate_tracker.id
        )
        for release in history_releases:
            release.tracker_name = tracker_name

        if not selection_candidates:
            selection_candidates = list(history_releases)

        unique_selection_candidates = self.storage.dedupe_releases_by_immutable_identity(
            selection_candidates
        )

        for release in unique_selection_candidates:
            identity_key = self.storage.release_identity_key_for_source(release)
            source_candidates = candidate_sources.get(identity_key, [])
            if not source_candidates:
                continue

            source_candidates = sorted(
                source_candidates,
                key=lambda item: item[0].source_rank,
            )
            primary_source = source_candidates[0][0]
            if primary_source.id is None:
                continue

            primary_source_history_id = await self.storage.get_source_release_history_id(
                primary_source.id,
                identity_key,
            )
            if primary_source_history_id is None:
                continue

            supporting_source_history_ids: list[int] = []
            for candidate_source, _ in source_candidates[1:]:
                if candidate_source.id is None:
                    continue
                source_history_id = await self.storage.get_source_release_history_id(
                    candidate_source.id,
                    identity_key,
                )
                if source_history_id is not None:
                    supporting_source_history_ids.append(source_history_id)

            await self.storage.upsert_tracker_release_history(
                aggregate_tracker.id,
                release,
                primary_source_release_history_id=primary_source_history_id,
                supporting_source_release_history_ids=supporting_source_history_ids,
                source_type=primary_source.source_type,
            )

        projection_releases, latest_version = await self._refresh_tracker_projection_and_notify(
            aggregate_tracker_id=aggregate_tracker.id,
            tracker_name=tracker_name,
            channels=tracker_config.channels if tracker_config is not None else [],
            sort_mode=sort_mode,
        )

        if source_errors and not projection_releases:
            raise RuntimeError("; ".join(source_errors))

        error = None
        if source_errors:
            error = f"部分来源检查失败: {'; '.join(source_errors)}"

        return {
            "releases": projection_releases,
            "latest_version": latest_version,
            "error": error,
        }

    async def _check_tracker(self, tracker_name: str):
        """Check one tracker"""
        aggregate_tracker = await self.storage.get_aggregate_tracker(tracker_name)

        # Fetch the latest configuration from the database to keep status synchronized
        tracker_config = await self.storage.get_tracker_config(tracker_name)
        if not tracker_config and aggregate_tracker is None:
            logger.warning(f"Tracker config missing during check: {tracker_name}")
            return None

        tracker_type = _status_type(tracker_config, aggregate_tracker)

        # Check only when the configuration is enabled
        aggregate_enabled = aggregate_tracker.enabled if aggregate_tracker is not None else True
        tracker_enabled = tracker_config.enabled if tracker_config is not None else True
        if not tracker_enabled or not aggregate_enabled:
            # If disabled, update status without running a check
            status = TrackerStatus(
                name=tracker_name,
                type=tracker_type,
                enabled=False,
                last_check=datetime.now(),
                last_version=None,  # or keep the previous value
                error="追踪器已禁用",
            )
            await self.storage.update_tracker_status(status)
            return status

        try:
            aggregate_tracker = self._require_aggregate_tracker_for_live_check(
                tracker_name, aggregate_tracker
            )
            result = await self._process_aggregate_tracker_check(
                tracker_name,
                aggregate_tracker,
                tracker_config,
                trigger_mode="scheduled",
            )

            releases = result["releases"]
            latest_version = result["latest_version"]
            error = result.get("error")

            if releases or latest_version:
                status = TrackerStatus(
                    name=tracker_name,
                    type=tracker_type,
                    enabled=True,
                    last_check=datetime.now(),
                    last_version=latest_version,
                    error=error,
                )
            else:
                status = TrackerStatus(
                    name=tracker_name,
                    type=tracker_type,
                    enabled=True,
                    last_check=datetime.now(),
                    last_version=None,
                    error=error or "未找到版本信息",
                )

            await self.storage.update_tracker_status(status)
            return status

        except Exception as e:
            error_msg = str(e) or getattr(e, "__class__", Exception).__name__
            logger.error(f"Check failed for {tracker_name}: {error_msg}")

            # Update error status
            status = TrackerStatus(
                name=tracker_name,
                type=tracker_type,
                enabled=True,
                last_check=datetime.now(),
                last_version=None,
                error=error_msg,
            )
            await self.storage.update_tracker_status(status)
            return status

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

    async def _create_tracker(self, config: TrackerConfig) -> BaseTracker:
        """Create a tracker instance"""
        credential = None
        token = None
        if config.credential_name:
            credential = await self.storage.get_credential_by_name(config.credential_name)
            if credential:
                token = credential.token
            else:
                logger.warning(
                    f"Credential '{config.credential_name}' referenced by tracker {config.name} not found, using anonymous access"
                )

        legacy_filter = {}

        if config.type == "github":
            return GitHubTracker(
                name=config.name,
                repo=config.repo or "",
                token=token,
                fetch_mode=config.github_fetch_mode,
                filter=legacy_filter,
                channels=config.channels,
                timeout=config.fetch_timeout,
            )
        elif config.type == "gitlab":
            return GitLabTracker(
                name=config.name,
                project=config.project or "",
                instance=config.instance or "https://gitlab.com",
                token=token,
                filter=legacy_filter,
                channels=config.channels,
                timeout=config.fetch_timeout,
            )
        elif config.type == "gitea":
            return GiteaTracker(
                name=config.name,
                repo=config.repo or "",
                instance=config.instance or "https://gitea.com",
                token=token,
                filter=legacy_filter,
                channels=config.channels,
                timeout=config.fetch_timeout,
            )
        elif config.type == "helm":
            return HelmTracker(
                name=config.name,
                repo=config.repo or "",
                chart=config.chart or "",
                token=token,
                filter=legacy_filter,
                channels=config.channels,
                timeout=config.fetch_timeout,
            )
        elif config.type == "container":
            return DockerTracker(
                name=config.name,
                image=config.image or "",
                registry=config.registry,
                token=_container_registry_auth_token(credential) if credential else token,
                filter=legacy_filter,
                channels=config.channels,
                timeout=config.fetch_timeout,
            )
        else:
            raise ValueError(f"不支持的追踪器类型: {config.type}")

    async def check_tracker_now_v2(self, name: str) -> TrackerStatus:
        """Check the specified tracker immediately (V2)"""
        config = await self.storage.get_tracker_config(name)
        aggregate_tracker = await self.storage.get_aggregate_tracker(name)
        if not config and aggregate_tracker is None:
            raise ValueError(f"Tracker {name} not found")

        tracker_type = _status_type(config, aggregate_tracker)
        current_status = await self.storage.get_tracker_status(name)
        enabled = (
            config.enabled
            if config is not None
            else bool(aggregate_tracker and aggregate_tracker.enabled)
        )

        if name in self._manual_checks_in_progress:
            return TrackerStatus(
                name=name,
                type=tracker_type,
                enabled=enabled,
                last_check=current_status.last_check if current_status else None,
                last_version=current_status.last_version if current_status else None,
                error="检查进行中，跳过重复请求",
                channel_count=_tracker_channel_count(config),
            )

        if (
            current_status
            and current_status.last_check
            and (datetime.now() - current_status.last_check).total_seconds()
            < MANUAL_CHECK_COOLDOWN_SECONDS
        ):
            return TrackerStatus(
                name=name,
                type=tracker_type,
                enabled=enabled,
                last_check=current_status.last_check,
                last_version=current_status.last_version,
                error="最近已检查，跳过重复请求",
                channel_count=_tracker_channel_count(config),
            )

        try:
            latest_version = current_status.last_version if current_status else None
            self._manual_checks_in_progress.add(name)
            aggregate_tracker = self._require_aggregate_tracker_for_live_check(
                name, aggregate_tracker
            )
            result = await self._process_aggregate_tracker_check(
                name,
                aggregate_tracker,
                config,
                log_prefix="Manual ",
                trigger_mode="manual",
            )

            releases = result["releases"]
            if result["latest_version"]:
                latest_version = result["latest_version"]
            error = result.get("error")

            status = TrackerStatus(
                name=name,
                type=_status_type(config, aggregate_tracker),
                enabled=(
                    config.enabled
                    if config is not None
                    else bool(aggregate_tracker and aggregate_tracker.enabled)
                ),
                last_check=datetime.now(),
                last_version=latest_version,
                error=error if releases or latest_version else (error or "未找到版本信息"),
                channel_count=_tracker_channel_count(config),
            )
            await self.storage.update_tracker_status(status)

            return status

        except Exception as e:
            error_msg = str(e) or getattr(e, "__class__", Exception).__name__
            logger.error(f"Manual check failed for {name}: {error_msg}")

            # Update error status in the database
            status = TrackerStatus(
                name=name,
                type=_status_type(config, aggregate_tracker),
                enabled=(
                    config.enabled
                    if config is not None
                    else bool(aggregate_tracker and aggregate_tracker.enabled)
                ),
                last_check=datetime.now(),
                last_version=None,
                error=error_msg,
                channel_count=_tracker_channel_count(config),
            )
            await self.storage.update_tracker_status(status)

            # Return status with an error message instead of raising
            return status
        finally:
            self._manual_checks_in_progress.discard(name)
