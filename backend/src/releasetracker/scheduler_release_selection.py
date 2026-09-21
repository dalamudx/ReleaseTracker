from __future__ import annotations

import inspect
import logging
from typing import Any, cast

from .config import TrackerConfig
from .models import Release
from .storage.sqlite import SQLiteStorage
from .trackers.base import BaseTracker

logger = logging.getLogger(__name__)


def _tracker_method_supports_argument(method: Any, argument_name: str) -> bool:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return False

    return argument_name in signature.parameters


class ReleaseSchedulerReleaseSelection:
    """Fetch tracker releases and select channel candidates."""

    async def _fetch_tracker_releases(
        self,
        tracker_name: str,
        tracker: BaseTracker,
        tracker_config: TrackerConfig | None,
        *,
        log_prefix: str,
        require_complete: bool = False,
        fail_fast: bool = False,
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
            fetch_all_error: Exception | None = None

            try:
                limit = tracker_config.fetch_limit if tracker_config else 30
                local_releases = await tracker.fetch_all(limit=limit, fallback_tags=fallback_tags)
            except Exception as inner_e:
                if fail_fast:
                    raise
                fetch_all_error = inner_e
                logger.warning(
                    f"{log_prefix}fetch_all failed for {tracker_name} ({inner_e.__class__.__name__}: {inner_e}), trying fallback"
                )

            if not local_releases and not fail_fast:
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

            if fetch_all_error is not None and require_complete:
                message = str(fetch_all_error) or fetch_all_error.__class__.__name__
                fallback_result = "one release" if local_releases else "no releases"
                diagnostic = (
                    f"complete fetch failed ({fetch_all_error.__class__.__name__}: {message}); "
                    f"fetch_latest fallback returned {fallback_result} (diagnostic only)"
                )
                existing_hint = getattr(tracker, "last_fallback_hint", None)
                tracker.last_fallback_hint = (
                    f"{existing_hint}; {diagnostic}" if existing_hint else diagnostic
                )
                # A source-local latest value cannot prove complete alias ownership.
                # Keep it diagnostic-only so a retry is the sole commit boundary.
                return []

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
