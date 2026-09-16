from __future__ import annotations

import logging
from typing import Any

from .config import TrackerConfig
from .models import AggregateTracker, Release, TrackerSource

logger = logging.getLogger(__name__)


class ReleaseSchedulerAggregateChecks:
    """Fetch aggregate tracker sources and persist their truth and projections."""

    async def _process_aggregate_tracker_check(
        self,
        tracker_name: str,
        aggregate_tracker: AggregateTracker,
        tracker_config: TrackerConfig | None,
        *,
        log_prefix: str = "",
        trigger_mode: str = "scheduled",
        source_ids: set[int] | None = None,
    ) -> dict[str, Any]:
        enabled_sources = [
            source
            for source in aggregate_tracker.sources
            if source.enabled and (source_ids is None or source.id in source_ids)
        ]
        if not enabled_sources:
            raise ValueError("Aggregate tracker has no enabled data sources")
        if aggregate_tracker.id is None:
            raise ValueError("Aggregate tracker is missing a persisted ID")

        source_errors: list[str] = []
        source_fetch_run_ids: dict[int, int] = {}
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
            source_fetch_run_ids[source.id] = source_fetch_run_id
            source_history_ids_by_identity: dict[str, int] = {}

            try:
                source_tracker = await self._create_tracker(source_config)
                await self._configure_container_tracker_fetch_state(
                    source.id, source_tracker, source_history_releases
                )
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
                mapped_releases, changelog_errors = await self._apply_custom_changelog_notes(
                    aggregate_tracker=aggregate_tracker,
                    source=source,
                    source_config=source_config,
                    releases=mapped_releases,
                )
                source_errors.extend(
                    f"Release notes extraction failed: {error}" for error in changelog_errors
                )
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
                # Canonical correlation needs every source alias. Immutable artifact
                # dedupe remains in tracker history selection, not observation truth.
                releases_for_source_projection = (
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

        correlated_candidates = await self.storage.get_correlated_release_candidates(
            aggregate_tracker.id
        )
        for candidate in correlated_candidates:
            source_history_ids = candidate["source_history_ids"]
            if len(candidate["tracker_source_ids"]) < 2:
                continue
            release = candidate["release"]
            release.tracker_name = tracker_name
            primary_source_history_id = candidate["primary_source_history_id"]
            tracker_release_history_id, _ = await self.storage.upsert_tracker_release_history(
                aggregate_tracker.id,
                release,
                primary_source_release_history_id=primary_source_history_id,
                supporting_source_release_history_ids=[
                    source_history_id
                    for source_history_id in source_history_ids
                    if source_history_id != primary_source_history_id
                ],
                source_type=release.tracker_type,
            )
            await self.storage.merge_tracker_release_history_sources(
                aggregate_tracker_id=aggregate_tracker.id,
                canonical_tracker_release_history_id=tracker_release_history_id,
                source_history_ids=source_history_ids,
                artifact_digest=release.artifact_digest,
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
            error = f"Partial source check failed: {'; '.join(source_errors)}"

        return {
            "releases": projection_releases,
            "latest_version": latest_version,
            "error": error,
            "source_fetch_run_ids": source_fetch_run_ids,
        }
