"""Release history query and response mapping helpers."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from ..models import Release

if TYPE_CHECKING:
    from ..storage.sqlite import SQLiteStorage


def infer_release_channel(
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


def find_channel_by_stored_name(
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


async def get_release_history_page(
    storage: SQLiteStorage,
    *,
    tracker_name: str | None = None,
    skip: int,
    limit: int,
    search: str | None = None,
    prerelease: bool | None = None,
    channel_filter_active: bool = False,
    channel_filter: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Return release history after SQL filters and optional tracker-local channel matching."""
    if channel_filter_active and channel_filter is None:
        raise ValueError("A resolved channel filter is required when channel filtering is active")

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
    query_params: tuple[Any, ...] = tuple(params)
    pagination_sql = ""
    total: int | None = None
    if not channel_filter_active:
        total_row = await (
            await db.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM tracker_release_history trh
                JOIN aggregate_trackers at ON at.id = trh.aggregate_tracker_id
                JOIN source_release_history srh ON srh.id = trh.primary_source_release_history_id
                {where_sql}
                """,
                query_params,
            )
        ).fetchone()
        total = int(total_row["total"])
        pagination_sql = "LIMIT ? OFFSET ?"
        query_params = (*query_params, limit, skip)

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
            {pagination_sql}
            """,
            query_params,
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
        if channel_filter is not None:
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
                channel_filter,
                channel_source_type=channel_filter.get("source_type"),
            ):
                continue
        items.append(item)

    channel_filters_by_name: dict[str, tuple[Any, list[dict[str, Any]]]] = {}

    for item in items:
        history_tracker_name = item["tracker_name"]
        aggregate_tracker, enabled_channels = channel_filters_by_name.get(
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
            channel_filters_by_name[history_tracker_name] = (aggregate_tracker, enabled_channels)

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
            matched_channel = find_channel_by_stored_name(
                item.get("channel_name"),
                enabled_channels,
            ) or infer_release_channel(
                storage,
                release_for_channel,
                enabled_channels,
            )
            if matched_channel is not None:
                item["channel_name"] = item.get("channel_name") or matched_channel.get("name")
                item["channel_type"] = matched_channel.get("type")
        item.pop("_primary_source_type_raw", None)
        item.pop("aggregate_tracker_id", None)

    if channel_filter_active:
        return items[skip : skip + limit], len(items)
    return items, total if total is not None else len(items)
