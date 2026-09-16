from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_current_admin_user, get_storage
from ..models import ReleaseStats
from ..services.release_history import (
    find_channel_by_stored_name,
    get_release_history_page,
    infer_release_channel,
)
from ..storage.sqlite import SQLiteStorage

router = APIRouter(prefix="/api", tags=["releases"])


def _resolve_canonical_tracker_channel_selector(
    storage: SQLiteStorage,
    aggregate_tracker,
    selector: str,
) -> dict[str, Any] | None:
    return storage.resolve_tracker_release_channel(aggregate_tracker, selector)


async def _resolve_release_history_channel_filter(
    storage: SQLiteStorage,
    *,
    tracker_name: str | None,
    channel: str | None,
) -> dict[str, Any] | None:
    if channel is None:
        return None
    if tracker_name is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "`channel` filter requires `tracker` on `/api/releases` because channel definitions "
                "are tracker-local."
            ),
        )

    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    if aggregate_tracker is None:
        raise HTTPException(
            status_code=400,
            detail=f"`channel={channel}` requires an existing tracker context.",
        )

    tracker_channel = _resolve_canonical_tracker_channel_selector(
        storage,
        aggregate_tracker,
        channel,
    )
    if tracker_channel is None:
        raise HTTPException(
            status_code=400,
            detail=f"Tracker '{tracker_name}' does not define channel '{channel}'.",
        )
    return tracker_channel


def _summary_matches_filters(
    summary: dict[str, Any],
    *,
    search: str | None,
    prerelease: bool | None,
) -> bool:
    if prerelease is not None and bool(summary["prerelease"]) != prerelease:
        return False

    normalized_search = search.strip().lower() if search and search.strip() else None
    if normalized_search is None:
        return True

    haystacks = [
        summary["tracker_name"],
        summary["identity_key"] or "",
        summary["version"] or "",
        summary["digest"] or "",
        summary["name"] or "",
        summary["tag_name"] or "",
    ]
    return any(normalized_search in str(haystack).lower() for haystack in haystacks)


async def _get_tracker_channel_summary(
    storage: SQLiteStorage,
    tracker_name: str,
    channel: str,
) -> dict[str, Any] | None:
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    if aggregate_tracker is None:
        return None

    tracker_config = await storage.get_tracker_config(tracker_name)
    sort_mode = tracker_config.version_sort_mode if tracker_config is not None else "published_at"

    matched_channel = _resolve_canonical_tracker_channel_selector(
        storage,
        aggregate_tracker,
        channel,
    )
    if matched_channel is None:
        raise HTTPException(
            status_code=400,
            detail=f"Tracker '{tracker_name}' does not define channel '{channel}'.",
        )

    current_rows = await storage.get_tracker_current_release_rows(tracker_name)
    if not current_rows:
        return None

    history_releases = await storage.get_tracker_release_history_releases(aggregate_tracker.id)
    history_by_id = {release.id: release for release in history_releases}
    projection_releases = []
    for row in current_rows:
        release = row["release"]
        history_release = history_by_id.get(row["tracker_release_history_id"])
        if history_release is not None:
            release = release.model_copy(
                update={
                    "aliases": history_release.aliases,
                    "alias_references": history_release.alias_references,
                }
            )
        projection_releases.append(release.model_copy(update={"tracker_name": tracker_name}))

    winners = storage.select_best_releases_by_channel(
        projection_releases,
        [matched_channel],
        sort_mode=sort_mode,
        use_immutable_identity=True,
        use_source_aliases=True,
    )
    winner = next(iter(winners.values()), None)
    if winner is None:
        return None

    winner_identity_key = storage.release_identity_key_for_source(
        winner,
        source_type=winner.tracker_type,
    )
    winner_row = next(
        (row for row in current_rows if row["identity_key"] == winner_identity_key),
        None,
    )
    if winner_row is None:
        return None

    return {
        "tracker_name": tracker_name,
        "tracker_release_history_id": winner_row["tracker_release_history_id"],
        "identity_key": winner_row["identity_key"],
        "version": winner.version,
        "digest": winner_row["digest"],
        "published_at": winner_row["published_at"],
        "prerelease": winner.prerelease,
        "name": winner.name,
        "tag_name": winner.tag_name,
        "channel_name": winner.channel_name,
        "url": winner.url,
        "changelog_url": winner.changelog_url,
        "body": winner.body,
        "primary_source": winner_row["primary_source"],
        "primary_source_type": (
            winner_row["primary_source"]["source_type"]
            if winner_row["primary_source"] is not None
            else None
        ),
        "projected_at": winner_row["projected_at"],
    }


def _removed_mode_error() -> HTTPException:
    return HTTPException(
        status_code=400,
        detail=(
            "`include_history` has been removed. `/api/releases` is history-only. "
            "Use `/api/trackers/{tracker_name}/releases/history` for tracker history, "
            "`/api/trackers/{tracker_name}/current` for current projection, or "
            "`/api/releases/latest` for latest current summaries."
        ),
    )


async def _load_aggregate_tracker_or_404(storage: SQLiteStorage, tracker_name: str):
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    if aggregate_tracker is None:
        raise HTTPException(status_code=404, detail="Tracker not found")
    return aggregate_tracker


@router.get("/stats", response_model=ReleaseStats, dependencies=[Depends(get_current_admin_user)])
async def get_stats(storage: Annotated[SQLiteStorage, Depends(get_storage)]):
    return await storage.get_stats()


@router.get("/releases", dependencies=[Depends(get_current_admin_user)])
async def get_releases(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    tracker: str | None = None,
    skip: int = 0,
    limit: int = 20,
    search: str | None = None,
    prerelease: bool | None = None,
    channel: str | None = None,
    include_history: str | None = None,
):
    if include_history is not None:
        raise _removed_mode_error()
    if limit > 100:
        limit = 100
    if limit < 1:
        limit = 1

    tracker_channel = await _resolve_release_history_channel_filter(
        storage,
        tracker_name=tracker,
        channel=channel,
    )
    items, total = await get_release_history_page(
        storage,
        tracker_name=tracker,
        skip=skip,
        limit=limit,
        search=search,
        prerelease=prerelease,
        channel_filter_active=channel is not None,
        channel_filter=tracker_channel,
    )
    return {"total": total, "items": items, "skip": skip, "limit": limit}


@router.get("/releases/latest", dependencies=[Depends(get_current_admin_user)])
async def get_latest_releases(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    tracker: str | None = None,
    limit: int = 5,
    search: str | None = None,
    prerelease: bool | None = None,
    channel: str | None = None,
    include_history: str | None = None,
):
    if include_history is not None:
        raise _removed_mode_error()
    if channel is not None and tracker is None:
        raise HTTPException(
            status_code=400,
            detail="`channel` filter requires `tracker` on `/api/releases/latest` because channel definitions are tracker-local.",
        )
    if limit > 100:
        limit = 100
    if limit < 1:
        limit = 1

    trackers = []
    if tracker is not None:
        resolved = await storage.get_aggregate_tracker(tracker)
        if resolved is None:
            return []
        trackers = [resolved]
    else:
        trackers = await storage.get_all_aggregate_trackers()

    items: list[dict[str, Any]] = []
    for aggregate_tracker in trackers:
        summary = (
            await _get_tracker_channel_summary(storage, aggregate_tracker.name, channel)
            if channel is not None
            else await storage.get_tracker_latest_current_release_summary(aggregate_tracker.name)
        )
        if summary is None:
            continue
        if not _summary_matches_filters(summary, search=search, prerelease=prerelease):
            continue
        release_summary = summary.get("release")
        channel_name = summary.get("channel_name") or (
            release_summary.channel_name if release_summary is not None else None
        )
        channel_type = None
        enabled_channels = [
            channel
            for channel in storage.authoritative_release_channels_for_tracker(aggregate_tracker)
            if channel.get("enabled", True)
        ]
        matched_channel = find_channel_by_stored_name(channel_name, enabled_channels)
        if matched_channel is None and release_summary is not None and channel_name is None:
            matched_channel = infer_release_channel(storage, release_summary, enabled_channels)
        if matched_channel is not None:
            channel_name = channel_name or matched_channel.get("name")
            channel_type = matched_channel.get("type")
        items.append(
            {
                "tracker_name": aggregate_tracker.name,
                "tracker_release_history_id": summary["tracker_release_history_id"],
                "identity_key": summary["identity_key"],
                "version": summary["version"],
                "digest": summary["digest"],
                "published_at": summary["published_at"],
                "prerelease": summary["prerelease"],
                "name": summary["name"],
                "tag_name": summary["tag_name"],
                "channel_name": channel_name,
                "channel_type": channel_type,
                "url": summary["url"],
                "changelog_url": summary["changelog_url"],
                "body": summary["body"],
                "primary_source": summary["primary_source"],
                "primary_source_type": (
                    (summary["primary_source_type"])
                    if summary["primary_source_type"] is not None
                    else None
                ),
                "projected_at": summary["projected_at"],
            }
        )
    items.sort(
        key=lambda item: (
            -(
                item["published_at"].timestamp()
                if item["published_at"] is not None
                else float("-inf")
            ),
            -(
                item["projected_at"].timestamp()
                if item["projected_at"] is not None
                else float("-inf")
            ),
            item["tracker_name"],
        ),
    )
    return items[:limit]
