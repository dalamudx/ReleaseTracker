from __future__ import annotations

from typing import Any, cast

from .config import TrackerConfig
from .models import (
    AggregateTracker,
    Release,
    SourceReleaseObservation,
    TrackerSource,
    TrackerSourceType,
)
from .services.changelog import fetch_and_extract_changelog


class ReleaseSchedulerAggregateSupport:
    """Normalize aggregate tracker sources and rebuild their stored release views."""

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
            raise ValueError(f"Aggregate tracker has no enabled data sources: {tracker_name}")
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

    async def _apply_custom_changelog_notes(
        self,
        *,
        aggregate_tracker: AggregateTracker,
        source: TrackerSource,
        source_config: TrackerConfig,
        releases: list[Release],
    ) -> tuple[list[Release], list[str]]:
        release_notes = aggregate_tracker.release_notes
        if release_notes.source != "custom_changelog":
            return releases, []

        if source.source_key != release_notes.changelog_source_key:
            return [
                release.model_copy(update={"body": None, "changelog_url": None})
                for release in releases
            ], []

        token = None
        if source.credential_name:
            credential = await self.storage.get_credential_by_name(source.credential_name)
            if credential:
                token = credential.token

        rewritten_releases: list[Release] = []
        diagnostics: list[str] = []
        for release in releases:
            try:
                result = await fetch_and_extract_changelog(
                    source=source,
                    release=release,
                    config=release_notes,
                    token=token,
                    timeout=source_config.fetch_timeout,
                )
                rewritten_releases.append(
                    release.model_copy(update={"body": result.body, "changelog_url": None})
                )
            except Exception as exc:
                diagnostics.append(
                    f"{source.source_key}/{release.tag_name or release.version}: {str(exc) or exc.__class__.__name__}"
                )
                rewritten_releases.append(
                    release.model_copy(update={"body": None, "changelog_url": None})
                )

        return rewritten_releases, diagnostics

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
            metadata_credential_name=source.source_config.get("metadata_credential_name"),
            version_format=source.source_config.get("version_format", "dotted"),
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
            raise ValueError("Aggregate tracker has no enabled data sources")
        if aggregate_tracker.id is None:
            raise ValueError("Aggregate tracker is missing a persisted ID")

        history_releases = await self.storage.get_tracker_release_history_releases(
            aggregate_tracker.id
        )
        for release in history_releases:
            release.tracker_name = tracker_name

        from .storage import sqlite_selection

        selection_candidates = await sqlite_selection.refresh_selections(
            self.storage, aggregate_tracker
        )
        # Rebuild the materialized view from source truth, never stale projections.
        # This path deliberately emits neither executor work nor release notifications.
        await self.storage.refresh_tracker_current_releases(
            aggregate_tracker.id, selection_candidates
        )
        from .versioning import select_version_candidate

        best_release = (
            selection_candidates[0]
            if len(selection_candidates) == 1
            else select_version_candidate(selection_candidates)
        )
        return {
            "releases": selection_candidates,
            "latest_version": best_release.version if best_release else None,
            "error": None,
        }
