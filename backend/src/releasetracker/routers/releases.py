from datetime import datetime
from typing import Annotated, Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_current_user, get_storage
from ..models import Release, ReleaseStats
from ..storage.sqlite import SQLiteStorage

router = APIRouter(prefix="/api", tags=["releases"])


def _resolve_canonical_tracker_channel_selector(
    storage: SQLiteStorage,
    aggregate_tracker,
    selector: str,
) -> dict[str, Any] | None:
    return storage.resolve_tracker_release_channel(aggregate_tracker, selector)


def _infer_release_channel(
    storage: SQLiteStorage,
    release: Release,
    enabled_channels: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for channel in enabled_channels:
        if storage._release_matches_channel(
            release,
            channel,
            channel_source_type=channel.get("source_type"),
        ):
            return channel

    return None


def _find_channel_by_stored_name(
    channel_name: str | None,
    enabled_channels: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not channel_name:
        return None

    for channel in enabled_channels:
        if channel_name in {
            channel.get("name"),
            channel.get("release_channel_key"),
            channel.get("channel_key"),
        }:
            return channel

    return None


async def _get_release_history_items(
    storage: SQLiteStorage,
    *,
    tracker_name: str | None = None,
    search: str | None = None,
    prerelease: bool | None = None,
    channel: str | None = None,
) -> list[dict[str, Any]]:
    if channel is not None and tracker_name is None:
        raise HTTPException(
            status_code=400,
            detail="`channel` filter requires `tracker` on `/api/releases` because channel definitions are tracker-local.",
        )

    tracker_channel = None
    if channel is not None:
        tracker_name_value = tracker_name
        if tracker_name_value is None:
            raise HTTPException(
                status_code=400,
                detail="`channel` filter requires `tracker` on `/api/releases`.",
            )
        aggregate_tracker = await storage.get_aggregate_tracker(tracker_name_value)
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
                detail=f"Tracker '{tracker_name_value}' does not define channel '{channel}'.",
            )

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    clauses: list[str] = []
    params: list[Any] = []
    if tracker_name is not None:
        clauses.append("at.name = ?")
        params.append(tracker_name)
    if prerelease is not None:
        clauses.append("srh.prerelease = ?")
        params.append(1 if prerelease else 0)

    normalized_search = search.strip().lower() if search and search.strip() else None
    if normalized_search is not None:
        like = f"%{normalized_search}%"
        clauses.append(
            "("  # noqa: ISC003
            "LOWER(at.name) LIKE ? "
            "OR LOWER(COALESCE(trh.identity_key, '')) LIKE ? "
            "OR LOWER(COALESCE(srh.version, trh.version, '')) LIKE ? "
            "OR LOWER(COALESCE(srh.name, '')) LIKE ? "
            "OR LOWER(COALESCE(srh.tag_name, '')) LIKE ? "
            "OR LOWER(COALESCE(trh.digest, '')) LIKE ?"
            ")"
        )
        params.extend([like, like, like, like, like, like])

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = await (
        await db.execute(
            f"""
            SELECT at.name AS tracker_name,
                   at.id AS aggregate_tracker_id,
                   trh.id AS tracker_release_history_id,
                   trh.identity_key,
                   trh.version AS tracker_version,
                   trh.digest,
                   trh.created_at AS tracker_created_at,
                   srh.id AS primary_source_release_history_id,
                   srh.source_type AS primary_source_type,
                   ats.source_key AS primary_source_key,
                   srh.name,
                   srh.tag_name,
                   srh.version AS display_version,
                   srh.published_at,
                   srh.url,
                   srh.changelog_url,
                   srh.prerelease,
                   srh.body,
                   srh.commit_sha,
                   srh.raw_payload
            FROM tracker_release_history trh
            JOIN aggregate_trackers at ON at.id = trh.aggregate_tracker_id
            JOIN source_release_history srh ON srh.id = trh.primary_source_release_history_id
            LEFT JOIN aggregate_tracker_sources ats ON ats.id = srh.tracker_source_id
            {where_sql}
            ORDER BY trh.created_at DESC, trh.id DESC
            """,
            tuple(params),
        )
    ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        is_prerelease = bool(row["prerelease"])

        raw_payload = storage._load_json(row["raw_payload"])
        item = {
            "tracker_name": row["tracker_name"],
            "aggregate_tracker_id": row["aggregate_tracker_id"],
            "_primary_source_type_raw": row["primary_source_type"],
            "tracker_release_history_id": row["tracker_release_history_id"],
            "identity_key": row["identity_key"],
            "version": row["display_version"] or row["tracker_version"],
            "digest": row["digest"],
            "name": row["name"],
            "tag_name": row["tag_name"],
            "app_version": raw_payload.get("appVersion"),
            "chart_version": raw_payload.get("chartVersion"),
            "published_at": row["published_at"],
            "url": row["url"],
            "changelog_url": row["changelog_url"],
            "prerelease": is_prerelease,
            "body": row["body"],
            "channel_name": raw_payload.get("channel_name"),
            "channel_type": None,
            "commit_sha": row["commit_sha"],
            "primary_source": (
                {
                    "source_key": row["primary_source_key"],
                    "source_type": row["primary_source_type"],
                    "source_release_history_id": row["primary_source_release_history_id"],
                }
                if row["primary_source_release_history_id"] is not None
                else None
            ),
            "created_at": row["tracker_created_at"],
        }
        if tracker_channel is not None:
            tag_name = item["tag_name"] or item["version"] or item["identity_key"]
            history_release = Release(
                tracker_name=item["tracker_name"],
                tracker_type=row["primary_source_type"] or "github",
                version=item["version"] or "",
                tag_name=tag_name,
                name=item["name"] or tag_name,
                url=item["url"] or "",
                published_at=datetime.fromisoformat(item["published_at"]),
                prerelease=is_prerelease,
            )
            if not storage._release_matches_channel(
                history_release,
                tracker_channel,
                channel_source_type=tracker_channel.get("source_type"),
            ):
                continue
        items.append(item)

    tracker_channels_by_name: dict[str, tuple[Any, list[dict[str, Any]]]] = {}

    for item in items:
        history_tracker_name = item["tracker_name"]
        aggregate_tracker, enabled_channels = tracker_channels_by_name.get(
            history_tracker_name,
            (None, []),
        )
        if aggregate_tracker is None:
            aggregate_tracker = await storage.get_aggregate_tracker(history_tracker_name)
            enabled_channels = (
                [
                    channel
                    for channel in storage.authoritative_release_channels_for_tracker(
                        aggregate_tracker
                    )
                    if channel.get("enabled", True)
                ]
                if aggregate_tracker is not None
                else []
            )
            tracker_channels_by_name[history_tracker_name] = (aggregate_tracker, enabled_channels)

        if aggregate_tracker is not None and enabled_channels:
            release_for_channel = Release(
                tracker_name=item["tracker_name"],
                tracker_type=item.get("_primary_source_type_raw") or "github",
                version=item["version"] or "",
                tag_name=item["tag_name"] or item["version"] or item["identity_key"],
                name=item["name"] or item["tag_name"] or item["version"] or item["identity_key"],
                url=item["url"] or "",
                published_at=datetime.fromisoformat(item["published_at"]),
                prerelease=bool(item["prerelease"]),
                body=item["body"],
                changelog_url=item["changelog_url"],
                channel_name=item.get("channel_name"),
                commit_sha=item.get("commit_sha"),
            )
            matched_channel = _find_channel_by_stored_name(
                item.get("channel_name"),
                enabled_channels,
            ) or _infer_release_channel(
                storage,
                release_for_channel,
                enabled_channels,
            )
            if matched_channel is not None:
                item["channel_name"] = item.get("channel_name") or matched_channel.get("name")
                item["channel_type"] = matched_channel.get("type")
        item.pop("_primary_source_type_raw", None)
        item.pop("aggregate_tracker_id", None)

    return items


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

    winners = storage.select_best_releases_by_channel(
        [row["release"].model_copy(update={"tracker_name": tracker_name}) for row in current_rows],
        [matched_channel],
        sort_mode=sort_mode,
        use_immutable_identity=True,
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
        raise HTTPException(status_code=404, detail="追踪器不存在")
    return aggregate_tracker


@router.get("/stats", response_model=ReleaseStats, dependencies=[Depends(get_current_user)])
async def get_stats(storage: Annotated[SQLiteStorage, Depends(get_storage)]):
    return await storage.get_stats()


@router.get("/releases", dependencies=[Depends(get_current_user)])
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

    items = await _get_release_history_items(
        storage,
        tracker_name=tracker,
        search=search,
        prerelease=prerelease,
        channel=channel,
    )
    return {
        "total": len(items),
        "items": items[skip : skip + limit],
        "skip": skip,
        "limit": limit,
    }


@router.get("/releases/latest", dependencies=[Depends(get_current_user)])
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
            for channel in storage.authoritative_release_channels_for_tracker(
                aggregate_tracker
            )
            if channel.get("enabled", True)
        ]
        matched_channel = _find_channel_by_stored_name(channel_name, enabled_channels)
        if matched_channel is None and release_summary is not None and channel_name is None:
            matched_channel = _infer_release_channel(storage, release_summary, enabled_channels)
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
