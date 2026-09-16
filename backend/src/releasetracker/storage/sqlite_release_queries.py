"""Canonical release querying, filtering, and channel-selection helpers."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING
from ..models import (
    Release,
    AggregateTracker,
    TrackerSource,
    SourceReleaseObservation,
    CanonicalRelease,
)

if TYPE_CHECKING:
    from .sqlite import SQLiteStorage


async def get_releases(
    storage: "SQLiteStorage",
    tracker_name: str | None = None,
    skip: int = 0,
    limit: int | None = 50,
    search: str | None = None,
    prerelease: bool | None = None,
    include_history: bool = True,
) -> list[Release]:
    aggregate_trackers: list[AggregateTracker] = []
    if tracker_name:
        aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
        if aggregate_tracker is not None:
            aggregate_trackers = [aggregate_tracker]
    else:
        aggregate_trackers = await storage.get_all_aggregate_trackers()

    releases: list[Release] = []
    for aggregate_tracker in aggregate_trackers:
        tracker_releases: list[Release] = []
        if aggregate_tracker.id is not None:
            if include_history:
                tracker_releases = await storage.get_tracker_release_history_releases(
                    aggregate_tracker.id
                )
            else:
                tracker_releases = await storage.get_tracker_current_releases(aggregate_tracker.id)

        if tracker_releases:
            if not include_history:
                tracker_config = await storage.get_tracker_config(aggregate_tracker.name)
                channels = tracker_config.channels if tracker_config is not None else []
                if channels:
                    tracker_releases = list(
                        storage.select_best_releases_by_channel(
                            tracker_releases,
                            channels,
                            sort_mode=(
                                tracker_config.version_sort_mode
                                if tracker_config is not None
                                else "published_at"
                            ),
                            use_immutable_identity=True,
                        ).values()
                    )
            for tracker_release in tracker_releases:
                tracker_release.tracker_name = aggregate_tracker.name
            releases.extend(tracker_releases)
            continue

        if not include_history:
            continue

        canonical_releases = await storage.get_canonical_releases(aggregate_tracker.name)
        source_observations = await storage.get_source_release_observations(aggregate_tracker.name)
        observations_by_id = {
            observation.id: observation
            for observation in source_observations
            if observation.id is not None
        }
        sources_by_id = {
            source.id: source for source in aggregate_tracker.sources if source.id is not None
        }
        visible_canonical_releases = [
            canonical_release
            for canonical_release in canonical_releases
            if storage._canonical_release_should_be_listed_in_history(
                aggregate_tracker,
                canonical_release,
                observations_by_id,
                sources_by_id,
            )
        ]
        releases.extend(
            storage._canonical_release_to_release(
                aggregate_tracker,
                canonical_release,
                observations_by_id,
            )
            for canonical_release in visible_canonical_releases
        )

    releases = [
        release
        for release in releases
        if storage._release_matches_filters(
            release,
            tracker_name=tracker_name,
            search=search,
            prerelease=prerelease,
        )
    ]
    releases = sorted(releases, key=storage._release_listing_sort_key, reverse=True)

    if limit is None:
        return releases[skip:]
    return releases[skip : skip + limit]


def _tracker_type_for_canonical_release(
    storage_cls: type["SQLiteStorage"],
    tracker: AggregateTracker,
    canonical_release: CanonicalRelease,
) -> str:
    if canonical_release.primary_observation_id is not None:
        observation = next(
            (
                source
                for source in tracker.sources
                if source.id is not None
                and any(
                    obs.source_release_observation_id == canonical_release.primary_observation_id
                    for obs in canonical_release.observations
                )
            ),
            None,
        )
        if observation is not None:
            return observation.source_type

    runtime_source = storage_cls._select_runtime_source(tracker)
    if runtime_source is not None:
        return runtime_source.source_type
    return "github"


def _aggregate_tracker_prefers_repo_history(tracker: AggregateTracker) -> bool:
    return any(source.source_type in {"github", "gitlab", "gitea"} for source in tracker.sources)


def _canonical_release_should_be_listed_in_history(
    cls,
    tracker: AggregateTracker,
    canonical_release: CanonicalRelease,
    observations_by_id: dict[int, SourceReleaseObservation],
    sources_by_id: dict[int, TrackerSource],
) -> bool:
    if not cls._aggregate_tracker_prefers_repo_history(tracker):
        return True

    for observation in canonical_release.observations:
        source_observation = observations_by_id.get(observation.source_release_observation_id)
        if source_observation is None:
            continue
        source = sources_by_id.get(source_observation.tracker_source_id)
        if source and source.source_type in {"github", "gitlab", "gitea"}:
            return True

    return False


def _canonical_release_to_release(
    cls,
    tracker: AggregateTracker,
    canonical_release: CanonicalRelease,
    observations_by_id: dict[int, SourceReleaseObservation],
) -> Release:
    tracker_type = cls._tracker_type_for_canonical_release(tracker, canonical_release)
    primary_observation_id = canonical_release.primary_observation_id
    primary_observation = (
        observations_by_id.get(primary_observation_id)
        if primary_observation_id is not None
        else None
    )
    if primary_observation is not None:
        app_version = primary_observation.app_version
        chart_version = primary_observation.chart_version
        if tracker_type == "helm" and app_version is None:
            app_version = primary_observation.version
        if tracker_type == "helm" and chart_version is None:
            chart_version = primary_observation.tag_name
        release_version = primary_observation.version
        release_name = primary_observation.name
        release_tag_name = primary_observation.tag_name
        release_published_at = primary_observation.published_at
        release_url = primary_observation.url
        release_prerelease = primary_observation.prerelease
        release_body = primary_observation.body
        release_commit_sha = primary_observation.commit_sha
        release_channel_name = primary_observation.raw_payload.get("channel_name")
    else:
        app_version = canonical_release.version if tracker_type == "helm" else None
        chart_version = canonical_release.tag_name if tracker_type == "helm" else None
        release_version = canonical_release.version
        release_name = canonical_release.name
        release_tag_name = canonical_release.tag_name
        release_published_at = canonical_release.published_at
        release_url = canonical_release.url
        release_prerelease = canonical_release.prerelease
        release_body = canonical_release.body
        release_commit_sha = None
        release_channel_name = None

    return Release(
        id=canonical_release.id,
        tracker_name=tracker.name,
        tracker_type=tracker_type,
        name=release_name,
        tag_name=release_tag_name,
        version=release_version,
        app_version=app_version,
        chart_version=chart_version,
        published_at=release_published_at,
        url=release_url,
        prerelease=release_prerelease,
        body=release_body,
        channel_name=release_channel_name,
        commit_sha=release_commit_sha,
        created_at=canonical_release.created_at,
    )


def _release_matches_filters(
    release: Release,
    *,
    tracker_name: str | None = None,
    search: str | None = None,
    prerelease: bool | None = None,
) -> bool:
    if tracker_name and release.tracker_name != tracker_name:
        return False
    if prerelease is not None and release.prerelease != prerelease:
        return False
    if search:
        search_value = search.lower()
        haystacks = [
            release.tracker_name,
            release.name,
            release.tag_name,
            release.version,
        ]
        if not any(search_value in haystack.lower() for haystack in haystacks):
            return False
    return True


def _release_listing_sort_key(release: Release) -> tuple[float, float, int]:
    created_at = release.created_at.timestamp() if release.created_at else 0.0
    return (release.published_at.timestamp(), created_at, release.id or 0)


async def get_latest_tracker_releases(storage: "SQLiteStorage", limit: int = 5) -> list[Release]:
    """Provide global recent releases for the dashboard, capped at one per tracker to avoid flooding"""
    releases = await storage.get_releases(limit=None, include_history=True)
    latest_by_tracker: dict[str, Release] = {}
    for release in releases:
        if release.tracker_name not in latest_by_tracker:
            latest_by_tracker[release.tracker_name] = release
    return list(latest_by_tracker.values())[:limit]


async def get_total_count(
    storage: "SQLiteStorage",
    tracker_name: str | None = None,
    search: str | None = None,
    prerelease: bool | None = None,
    include_history: bool = True,
) -> int:
    """Get count of matching records"""
    releases = await storage.get_releases(
        tracker_name=tracker_name,
        search=search,
        prerelease=prerelease,
        limit=None,
        include_history=include_history,
    )
    return len(releases)


async def get_releases_for_trackers_bulk(
    storage: "SQLiteStorage", tracker_names: list[str], limit_per_tracker: int = 200
) -> dict[str, list[Release]]:
    """
    Fetch recent release records for multiple trackers in one query

    Use a window function to avoid N+1 queries
    Returns:{tracker_name: [Release, ...]}
    """
    if not tracker_names:
        return {}

    result = {name: [] for name in tracker_names}
    for tracker_name in tracker_names:
        result[tracker_name] = await storage.get_releases(
            tracker_name=tracker_name,
            limit=limit_per_tracker,
            include_history=False,
        )

    return result


async def get_latest_release(storage: "SQLiteStorage", tracker_name: str) -> Release | None:
    """Get the latest release for a tracker"""
    latest_current_release = await storage.get_tracker_latest_current_release_summary(tracker_name)
    if latest_current_release is not None:
        return latest_current_release["release"]
    releases = await storage.get_releases(tracker_name, limit=1)
    return releases[0] if releases else None


async def get_latest_release_for_channels(
    storage: "SQLiteStorage", tracker_name: str, channels: list
) -> Release | None:
    """Get the latest releases across all enabled channels for a tracker"""
    if not channels:
        return await storage.get_latest_release(tracker_name)

    all_releases = await storage.get_releases(
        tracker_name,
        limit=None,
        include_history=False,
    )
    sort_mode = await storage.get_setting("version_sort_mode") or "published_at"
    channel_winners = storage.select_best_releases_by_channel(
        all_releases,
        channels,
        sort_mode=sort_mode,
        use_immutable_identity=True,
    )
    return storage.select_best_release(
        list(channel_winners.values()),
        channels,
        sort_mode=sort_mode,
        use_immutable_identity=True,
    )


def release_identity_key(release: Release) -> tuple[str, str]:
    return (release.tracker_name, release.tag_name)


def immutable_release_identity_key(cls, release: Release) -> tuple[str, str]:
    source_type = cls._normalize_release_value(release.tracker_type) or "github"
    return (
        release.tracker_name,
        cls.release_identity_key_for_source(release, source_type=source_type),
    )


def dedupe_releases_by_identity(
    storage_cls: type["SQLiteStorage"], releases: list[Release]
) -> list[Release]:
    unique_by_identity: dict[tuple[str, str], Release] = {}

    for release in releases:
        unique_by_identity[storage_cls.release_identity_key(release)] = release

    return list(unique_by_identity.values())


def dedupe_releases_by_immutable_identity(cls, releases: list[Release]) -> list[Release]:
    unique_by_identity: dict[tuple[str, str], Release] = {}

    for release in releases:
        identity_key = cls.immutable_release_identity_key(release)
        existing_release = unique_by_identity.get(identity_key)
        if existing_release is None or cls._should_replace_source_history_display(
            source_type=release.tracker_type,
            version=release.version,
            tag_name=release.tag_name,
            existing_version=existing_release.version,
            existing_tag_name=existing_release.tag_name,
        ):
            unique_by_identity[identity_key] = release

    return list(unique_by_identity.values())


def _release_matches_channel(
    storage_cls: type["SQLiteStorage"],
    release: Release,
    channel,
    *,
    channel_source_type: str | None = None,
) -> bool:
    from ..config import Channel
    import re

    if isinstance(channel, dict):
        channel = Channel(**channel)

    if storage_cls._supports_release_type_filter(channel_source_type):
        if channel.type == "release" and release.prerelease:
            return False
        if channel.type == "prerelease" and not release.prerelease:
            return False

    if channel.include_pattern:
        try:
            if not re.search(channel.include_pattern, release.tag_name):
                return False
        except re.error:
            pass

    if channel.exclude_pattern:
        try:
            if any(
                re.search(channel.exclude_pattern, candidate)
                for candidate in storage_cls._channel_exclude_match_candidates(release)
            ):
                return False
        except re.error:
            pass

    return True


def _supports_release_type_filter(source_type: str | None) -> bool:
    return source_type is None or source_type in {"github", "gitlab", "gitea"}


def _channel_exclude_match_candidates(release: Release) -> list[str]:
    return [release.tag_name]


def _release_order_key(
    storage_cls: type["SQLiteStorage"], release: Release, sort_mode: str = "published_at"
) -> tuple:
    from packaging.version import InvalidVersion, parse as parse_version

    normalized_version = storage_cls._normalize_version_for_ordering(release.version)
    semver_key: tuple[int, Any] | None = None
    try:
        semver_key = (1, parse_version(normalized_version))
    except InvalidVersion:
        semver_key = None

    published_at_key = release.published_at.timestamp()
    if sort_mode == "semver":
        if semver_key is not None:
            return (*semver_key, published_at_key)
        return (0, published_at_key, normalized_version)

    # Published-time mode must not let parseable versions outrank newer
    # non-PEP-440 tags such as build/commit-suffixed development releases.
    if semver_key is not None:
        return (published_at_key, *semver_key)
    return (published_at_key, 0, normalized_version)


def _channel_selection_key(channel, index: int) -> str:
    release_channel_key = getattr(channel, "release_channel_key", None)
    if isinstance(channel, dict):
        release_channel_key = channel.get("release_channel_key") or channel.get("channel_key")
    if release_channel_key:
        return str(release_channel_key)

    channel_name = getattr(channel, "name", None)
    if isinstance(channel, dict):
        channel_name = channel.get("name")
    return str(channel_name or f"legacy-channel-{index}")


def _release_matches_source_aliases(
    storage_cls: type["SQLiteStorage"],
    release: Release,
    channel,
    *,
    channel_source_type: str | None = None,
) -> bool:
    tracker_source_id = (
        channel.get("tracker_source_id")
        if isinstance(channel, dict)
        else getattr(channel, "tracker_source_id", None)
    )
    references = [
        reference
        for reference in release.alias_references
        if tracker_source_id is None or reference.tracker_source_id == int(tracker_source_id)
    ]
    if tracker_source_id is not None and not references:
        return False
    if not references:
        return storage_cls._release_matches_channel(
            release, channel, channel_source_type=channel_source_type
        )
    return any(
        storage_cls._release_matches_channel(
            release.model_copy(
                update={
                    "version": reference.alias,
                    "name": reference.alias,
                    "tag_name": reference.alias,
                    "prerelease": reference.prerelease,
                    "published_at": reference.published_at,
                    "artifact_digest": reference.digest,
                }
            ),
            channel,
            channel_source_type=reference.source_type,
        )
        for reference in references
    )


def _copy_release_with_channel_name(release: Release, channel_name: str) -> Release:
    return release.model_copy(update={"channel_name": channel_name})


def select_best_releases_by_channel(
    storage_cls: type["SQLiteStorage"],
    releases: list[Release],
    channels: list,
    sort_mode: str = "published_at",
    *,
    channel_source_type: str | None = None,
    use_immutable_identity: bool = False,
    use_source_aliases: bool = False,
) -> dict[str, Release]:
    if not releases or not channels:
        return {}

    if not any(ch.get("enabled", True) if isinstance(ch, dict) else ch.enabled for ch in channels):
        return {}

    unique_releases = (
        storage_cls.dedupe_releases_by_immutable_identity(releases)
        if use_immutable_identity
        else storage_cls.dedupe_releases_by_identity(releases)
    )
    winners: dict[str, Release] = {}

    for index, channel in enumerate(channels):
        if isinstance(channel, dict):
            if not channel.get("enabled", True):
                continue
            channel_name = channel.get("name")
        else:
            if not channel.enabled:
                continue
            channel_name = channel.name

        if not channel_name:
            continue
        channel_candidates = [
            release
            for release in unique_releases
            if (
                storage_cls._release_matches_source_aliases(
                    release, channel, channel_source_type=channel_source_type
                )
                if use_source_aliases
                else storage_cls._release_matches_channel(
                    release, channel, channel_source_type=channel_source_type
                )
            )
        ]

        if not channel_candidates:
            continue

        winner = max(
            channel_candidates,
            key=lambda release: storage_cls._release_order_key(release, sort_mode),
        )
        winner = storage_cls._copy_release_with_channel_name(winner, channel_name)
        winners[storage_cls._channel_selection_key(channel, index)] = winner

    return winners


def select_best_releases_for_tracker_channel(
    storage_cls: type["SQLiteStorage"],
    releases: list[Release],
    tracker_channel,
    sort_mode: str = "published_at",
    *,
    use_immutable_identity: bool = False,
) -> dict[str, Release]:
    if tracker_channel is None:
        return {}

    release_channels = getattr(tracker_channel, "release_channels", None)
    channel_source_type = getattr(tracker_channel, "source_type", None)
    if isinstance(tracker_channel, dict):
        release_channels = tracker_channel.get("release_channels")
        channel_source_type = tracker_channel.get("source_type")

    return storage_cls.select_best_releases_by_channel(
        releases,
        list(release_channels or []),
        sort_mode=sort_mode,
        channel_source_type=channel_source_type,
        use_immutable_identity=use_immutable_identity,
    )


def select_best_release(
    storage_cls: type["SQLiteStorage"],
    releases: list[Release],
    channels: list,
    sort_mode: str = "published_at",
    *,
    use_immutable_identity: bool = False,
) -> Release | None:
    """
    Select the latest release from the release list according to channel rules
    """
    if not releases:
        return None

    if not channels:
        return max(
            (
                storage_cls.dedupe_releases_by_immutable_identity(releases)
                if use_immutable_identity
                else storage_cls.dedupe_releases_by_identity(releases)
            ),
            key=lambda release: storage_cls._release_order_key(release, sort_mode),
        )

    enabled_channels = [ch for ch in channels if ch.enabled]
    if not enabled_channels:
        return max(
            (
                storage_cls.dedupe_releases_by_immutable_identity(releases)
                if use_immutable_identity
                else storage_cls.dedupe_releases_by_identity(releases)
            ),
            key=lambda release: storage_cls._release_order_key(release, sort_mode),
        )

    channel_winners = storage_cls.select_best_releases_by_channel(
        releases,
        enabled_channels,
        sort_mode=sort_mode,
        use_immutable_identity=use_immutable_identity,
    )
    if not channel_winners:
        return None

    return max(
        channel_winners.values(),
        key=lambda release: storage_cls._release_order_key(release, sort_mode),
    )
