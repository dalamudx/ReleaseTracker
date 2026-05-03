import logging
from datetime import datetime
from typing import Annotated, Any, Literal

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config import Channel, TrackerConfig
from ..dependencies import get_current_user, get_scheduler, get_storage
from ..models import AggregateTracker, Release, TrackerSource
from ..scheduler import ReleaseScheduler
from ..storage.sqlite import SQLiteStorage

router = APIRouter(prefix="/api/trackers", tags=["trackers"])
logger = logging.getLogger(__name__)


class AggregateTrackerPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str
    enabled: bool = True
    description: str | None = None
    changelog_policy: Literal["primary_source"] = "primary_source"
    primary_changelog_source_key: str
    sources: Annotated[list[TrackerSource], Field(min_length=1)]
    interval: int = 360
    version_sort_mode: Literal["published_at", "semver"] = "published_at"
    fetch_limit: int = 10
    fetch_timeout: int = 15
    fallback_tags: bool = False
    github_fetch_mode: Literal["graphql_first", "rest_first"] = "rest_first"
    channels: list[Channel] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_payload(self):
        AggregateTracker(
            name=self.name,
            enabled=self.enabled,
            description=self.description,
            changelog_policy=self.changelog_policy,
            primary_changelog_source_key=self.primary_changelog_source_key,
            sources=self.sources,
        )
        return self

    def to_aggregate_tracker(self) -> AggregateTracker:
        return AggregateTracker(
            name=self.name,
            enabled=self.enabled,
            description=self.description,
            changelog_policy=self.changelog_policy,
            primary_changelog_source_key=self.primary_changelog_source_key,
            sources=self.sources,
        )

    def to_runtime_config(self) -> TrackerConfig:
        aggregate_tracker = self.to_aggregate_tracker()
        selected_source = next(
            (
                source
                for source in aggregate_tracker.sources
                if source.source_key == aggregate_tracker.primary_changelog_source_key
            ),
            aggregate_tracker.sources[0],
        )
        source_config = selected_source.source_config
        return TrackerConfig(
            name=aggregate_tracker.name,
            type=selected_source.source_type,
            enabled=aggregate_tracker.enabled,
            repo=source_config.get("repo"),
            project=source_config.get("project"),
            instance=source_config.get("instance"),
            chart=source_config.get("chart"),
            image=source_config.get("image"),
            registry=source_config.get("registry"),
            credential_name=selected_source.credential_name,
            interval=self.interval,
            version_sort_mode=self.version_sort_mode,
            fetch_limit=self.fetch_limit,
            fetch_timeout=self.fetch_timeout,
            fallback_tags=self.fallback_tags,
            github_fetch_mode=self.github_fetch_mode,
            channels=self.channels,
        )


def _search_matches(tracker: AggregateTracker, search: str | None) -> bool:
    if not search:
        return True

    search_value = search.strip().lower()
    if not search_value:
        return True

    haystacks = [
        tracker.name,
        tracker.description or "",
        tracker.primary_changelog_source_key or "",
    ]
    for source in tracker.sources:
        haystacks.append(source.source_key)
        haystacks.append(source.source_type)
        haystacks.extend(str(value) for value in source.source_config.values() if value is not None)

    return any(search_value in haystack.lower() for haystack in haystacks)


async def _load_tracker_history_items(
    storage: SQLiteStorage,
    tracker_name: str,
    *,
    search: str | None = None,
    prerelease: bool | None = None,
) -> list[dict[str, Any]]:
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    if aggregate_tracker is None or aggregate_tracker.id is None:
        return []

    enabled_channels = [
        channel
        for channel in storage.authoritative_release_channels_for_tracker(aggregate_tracker)
        if channel.get("enabled", True)
    ]

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    rows = await (
        await db.execute(
            """
            SELECT trh.id AS tracker_release_history_id,
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
            JOIN source_release_history srh ON srh.id = trh.primary_source_release_history_id
            LEFT JOIN aggregate_tracker_sources ats ON ats.id = srh.tracker_source_id
            WHERE trh.aggregate_tracker_id = ?
            ORDER BY srh.published_at DESC, trh.id DESC
            """,
            (aggregate_tracker.id,),
        )
    ).fetchall()

    items: list[dict[str, Any]] = []
    normalized_search = search.strip().lower() if search else None
    for row in rows:
        is_prerelease = bool(row["prerelease"])
        if prerelease is not None and prerelease != is_prerelease:
            continue

        raw_payload = storage._load_json(row["raw_payload"])
        item = {
            "tracker_name": tracker_name,
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

        matched_channel_name: str | None = item["channel_name"]
        if not item["channel_name"] and enabled_channels:
            release_for_channel = Release(
                tracker_name=tracker_name,
                tracker_type=row["primary_source_type"] or "github",
                version=item["version"] or "",
                tag_name=item["tag_name"] or item["version"] or item["identity_key"],
                name=item["name"] or item["tag_name"] or item["version"] or item["identity_key"],
                url=item["url"] or "",
                published_at=datetime.fromisoformat(item["published_at"]),
                prerelease=bool(item["prerelease"]),
                body=item["body"],
                changelog_url=item["changelog_url"],
                channel_name=item["channel_name"],
                commit_sha=item["commit_sha"],
            )
            for channel in enabled_channels:
                if storage._release_matches_channel(
                    release_for_channel,
                    channel,
                    channel_source_type=channel.get("source_type"),
                ):
                    matched_channel_name = channel.get("name")
                    break

        if enabled_channels and not matched_channel_name:
            continue

        item["channel_name"] = matched_channel_name

        if normalized_search:
            haystacks = [
                item["name"] or "",
                item["tag_name"] or "",
                item["version"] or "",
                item["identity_key"] or "",
            ]
            if not any(normalized_search in str(haystack).lower() for haystack in haystacks):
                continue
        items.append(item)

    return items


async def _load_current_source_contributions(
    storage: SQLiteStorage,
    tracker_release_history_ids: list[int],
) -> dict[int, list[dict[str, Any]]]:
    if not tracker_release_history_ids:
        return {}

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    placeholders = ", ".join("?" for _ in tracker_release_history_ids)
    rows = await (
        await db.execute(
            f"""
            SELECT trhs.tracker_release_history_id,
                   trhs.contribution_kind,
                   srh.id AS source_release_history_id,
                   srh.source_type,
                   ats.source_key,
                   srh.version,
                   srh.tag_name,
                   srh.published_at,
                   srh.url,
                   srh.changelog_url,
                   srh.prerelease,
                   srh.body,
                   srh.digest,
                   srh.raw_payload,
                   srh.first_observed_at
            FROM tracker_release_history_sources trhs
            JOIN tracker_release_history trh ON trh.id = trhs.tracker_release_history_id
            JOIN source_release_history srh ON srh.id = trhs.source_release_history_id
            JOIN aggregate_tracker_sources ats
              ON ats.id = srh.tracker_source_id
             AND ats.aggregate_tracker_id = trh.aggregate_tracker_id
            WHERE trhs.tracker_release_history_id IN ({placeholders})
            ORDER BY trhs.tracker_release_history_id ASC,
                     CASE trhs.contribution_kind WHEN 'primary' THEN 0 ELSE 1 END ASC,
                     srh.published_at DESC,
                     srh.id DESC
            """,
            tracker_release_history_ids,
        )
    ).fetchall()

    contributions_by_history_id: dict[int, list[dict[str, Any]]] = {
        tracker_release_history_id: [] for tracker_release_history_id in tracker_release_history_ids
    }
    for row in rows:
        raw_payload = storage._load_json(row["raw_payload"])
        contributions_by_history_id[row["tracker_release_history_id"]].append(
            {
                "source_release_history_id": row["source_release_history_id"],
                "source_key": row["source_key"],
                "source_type": (row["source_type"]),
                "contribution_kind": row["contribution_kind"],
                "version": row["version"],
                "tag_name": row["tag_name"],
                "published_at": row["published_at"],
                "url": row["url"],
                "changelog_url": row["changelog_url"],
                "prerelease": bool(row["prerelease"]),
                "body": row["body"],
                "channel_name": raw_payload.get("channel_name"),
                "digest": row["digest"],
                "app_version": raw_payload.get("appVersion"),
                "chart_version": raw_payload.get("chartVersion"),
                "observed_at": row["first_observed_at"],
            }
        )

    return contributions_by_history_id


def _serialize_current_summary(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if summary is None:
        return None

    primary_source = summary["primary_source"]
    return {
        "tracker_release_history_id": summary["tracker_release_history_id"],
        "identity_key": summary["identity_key"],
        "version": summary["version"],
        "digest": summary["digest"],
        "published_at": summary["published_at"],
        "name": summary["name"],
        "tag_name": summary["tag_name"],
        "channel_name": summary["release"].channel_name,
        "prerelease": summary["prerelease"],
        "url": summary["url"],
        "changelog_url": summary["changelog_url"],
        "body": summary["body"],
        "primary_source": (
            {
                **primary_source,
                "source_type": (primary_source["source_type"]),
            }
            if primary_source is not None
            else None
        ),
    }


async def _build_tracker_current_view(
    storage: SQLiteStorage,
    tracker: AggregateTracker,
) -> dict[str, Any]:
    tracker_status = await storage.get_tracker_current_status_derivation(tracker.name)
    latest_summary = await storage.get_tracker_latest_current_release_summary(tracker.name)
    current_rows = await storage.get_tracker_current_release_rows(tracker.name)
    runtime_config = await storage.get_tracker_config(tracker.name)
    sort_mode = runtime_config.version_sort_mode if runtime_config is not None else "published_at"
    runtime_channels = runtime_config.channels if runtime_config is not None else []

    columns = [
        {
            "channel_key": storage._channel_selection_key(channel, channel_rank),
            "channel_type": channel.type,
            "enabled": channel.enabled,
            "channel_rank": channel_rank,
        }
        for channel_rank, channel in enumerate(runtime_channels)
    ]
    enabled_columns = [column for column in columns if column["enabled"]]

    channel_winners = storage.select_best_releases_by_channel(
        [row["release"].model_copy(update={"tracker_name": tracker.name}) for row in current_rows],
        runtime_channels,
        sort_mode=sort_mode,
        use_immutable_identity=True,
    )
    channel_keys_by_identity: dict[str, list[str]] = {}
    for column in enabled_columns:
        channel_key = column["channel_key"]
        winner = channel_winners.get(channel_key)
        if winner is None:
            continue
        identity_key = storage.release_identity_key_for_source(
            winner,
            source_type=winner.tracker_type,
        )
        channel_keys_by_identity.setdefault(identity_key, []).append(channel_key)

    contributions_by_history_id = await _load_current_source_contributions(
        storage,
        [row["tracker_release_history_id"] for row in current_rows],
    )

    rows: list[dict[str, Any]] = []
    for row in current_rows:
        selected_channel_keys = channel_keys_by_identity.get(row["identity_key"], [])
        rows.append(
            {
                "tracker_release_history_id": row["tracker_release_history_id"],
                "identity_key": row["identity_key"],
                "version": row["version"],
                "digest": row["digest"],
                "published_at": row["published_at"],
                "matched_channel_count": len(selected_channel_keys),
                "channel_keys": selected_channel_keys,
                "primary_source": (
                    {
                        **row["primary_source"],
                        "source_type": (
                            row["primary_source"]["source_type"]
                        ),
                    }
                    if row["primary_source"] is not None
                    else None
                ),
                "source_contributions": contributions_by_history_id.get(
                    row["tracker_release_history_id"], []
                ),
                "cells": {
                    column["channel_key"]: (
                        {
                            "channel_key": column["channel_key"],
                            "channel_type": column["channel_type"],
                            "selected": True,
                        }
                        if column["channel_key"] in selected_channel_keys
                        else None
                    )
                    for column in columns
                    if column["enabled"]
                },
            }
        )

    return {
        "tracker": {
            "name": tracker.name,
        },
        "status": {
            "last_check": tracker_status["last_check"],
            "last_version": tracker_status["latest_version"],
            "error": tracker_status["error"],
        },
        "latest_release": _serialize_current_summary(latest_summary),
        "matrix": {
            "columns": columns,
            "rows": rows,
        },
        "projected_at": tracker_status["projected_at"],
    }


async def _build_tracker_response(
    storage: SQLiteStorage,
    tracker: AggregateTracker,
    *,
    current_status_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    runtime_config = await storage.get_tracker_config(tracker.name)
    tracker_status = (
        current_status_map.get(tracker.name) if current_status_map is not None else None
    )
    if tracker_status is None:
        tracker_status = await storage.get_tracker_current_status_derivation(tracker.name)

    enabled_sources = [source for source in tracker.sources if source.enabled]

    return {
        "id": tracker.id,
        "name": tracker.name,
        "enabled": tracker.enabled,
        "description": tracker.description,
        "primary_changelog_source_key": tracker.primary_changelog_source_key,
        "interval": runtime_config.interval if runtime_config else 360,
        "version_sort_mode": runtime_config.version_sort_mode if runtime_config else "published_at",
        "fetch_limit": runtime_config.fetch_limit if runtime_config else 10,
        "fetch_timeout": runtime_config.fetch_timeout if runtime_config else 15,
        "fallback_tags": runtime_config.fallback_tags if runtime_config else False,
        "github_fetch_mode": runtime_config.github_fetch_mode if runtime_config else "rest_first",
        "channels": (
            [channel.model_dump() for channel in (runtime_config.channels or [])]
            if runtime_config
            else []
        ),
        "sources": [source.model_dump(mode="json") for source in tracker.sources],
        "status": {
            "last_check": tracker_status["last_check"] if tracker_status else None,
            "last_version": tracker_status["latest_version"] if tracker_status else None,
            "error": tracker_status["error"] if tracker_status else None,
            "source_count": len(tracker.sources),
            "enabled_source_count": len(enabled_sources),
            "source_types": sorted(
                {(source.source_type) for source in tracker.sources}
            ),
        },
        "created_at": tracker.created_at,
        "updated_at": tracker.updated_at,
    }


@router.get("", dependencies=[Depends(get_current_user)])
async def get_trackers(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    skip: int = 0,
    limit: int = 20,
    search: str | None = None,
):
    await storage.cleanup_blank_tracker_rows()

    trackers = await storage.get_all_aggregate_trackers()
    filtered_trackers = [tracker for tracker in trackers if _search_matches(tracker, search)]
    total = len(filtered_trackers)
    paginated_trackers = filtered_trackers[skip : skip + limit]

    current_status_map: dict[str, dict[str, Any]] = {}
    for tracker in paginated_trackers:
        current_status_map[tracker.name] = await storage.get_tracker_current_status_derivation(
            tracker.name
        )

    items = [
        await _build_tracker_response(
            storage,
            tracker,
            current_status_map=current_status_map,
        )
        for tracker in paginated_trackers
    ]
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/{tracker_name}", dependencies=[Depends(get_current_user)])
async def get_tracker(tracker_name: str, storage: Annotated[SQLiteStorage, Depends(get_storage)]):
    tracker = await storage.get_aggregate_tracker(tracker_name)
    if not tracker:
        raise HTTPException(status_code=404, detail="追踪器不存在")
    return await _build_tracker_response(storage, tracker)


@router.get("/{tracker_name}/config", dependencies=[Depends(get_current_user)])
async def get_tracker_config_detail(
    tracker_name: str, storage: Annotated[SQLiteStorage, Depends(get_storage)]
):
    tracker = await storage.get_aggregate_tracker(tracker_name)
    if not tracker:
        raise HTTPException(status_code=404, detail="追踪器不存在")
    return await _build_tracker_response(storage, tracker)


@router.get("/{tracker_name}/releases/history", dependencies=[Depends(get_current_user)])
async def get_tracker_release_history(
    tracker_name: str,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    skip: int = 0,
    limit: int = 20,
    search: str | None = None,
    prerelease: bool | None = None,
):
    tracker = await storage.get_aggregate_tracker(tracker_name)
    if tracker is None:
        raise HTTPException(status_code=404, detail="追踪器不存在")
    if limit > 100:
        limit = 100

    items = await _load_tracker_history_items(
        storage,
        tracker_name,
        search=search,
        prerelease=prerelease,
    )
    return {
        "tracker": tracker_name,
        "total": len(items),
        "items": items[skip : skip + limit],
        "skip": skip,
        "limit": limit,
    }


@router.get("/{tracker_name}/current", dependencies=[Depends(get_current_user)])
async def get_tracker_current_view(
    tracker_name: str,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    channel: str | None = None,
):
    tracker = await storage.get_aggregate_tracker(tracker_name)
    if tracker is None:
        raise HTTPException(status_code=404, detail="追踪器不存在")
    if channel is not None:
        if storage.resolve_tracker_release_channel(tracker, channel) is None:
            raise HTTPException(
                status_code=400,
                detail=f"Tracker '{tracker_name}' does not define channel '{channel}'.",
            )
    return await _build_tracker_current_view(storage, tracker)


@router.post("/{tracker_name}/check", dependencies=[Depends(get_current_user)])
async def check_tracker(
    tracker_name: str, scheduler: Annotated[ReleaseScheduler, Depends(get_scheduler)]
):
    try:
        return await scheduler.check_tracker_now_v2(tracker_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"检查失败: {str(e)}")


@router.post("", dependencies=[Depends(get_current_user)])
async def create_tracker(
    tracker_data: AggregateTrackerPayload,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    scheduler: Annotated[ReleaseScheduler, Depends(get_scheduler)],
):
    try:
        aggregate_tracker = tracker_data.to_aggregate_tracker()
        runtime_config = tracker_data.to_runtime_config()
        existing = await storage.get_aggregate_tracker(aggregate_tracker.name)
        if existing:
            raise HTTPException(status_code=400, detail="追踪器名称已存在")

        await storage.create_aggregate_tracker(aggregate_tracker)
        await storage.save_tracker_runtime_config(runtime_config)
        persisted_runtime = await storage.get_tracker_config(aggregate_tracker.name)
        if persisted_runtime is None:
            raise HTTPException(status_code=500, detail="追踪器运行时配置写入失败")
        await scheduler.refresh_tracker(aggregate_tracker.name)

        created_tracker = await storage.get_aggregate_tracker(aggregate_tracker.name)
        if created_tracker is None:
            raise HTTPException(status_code=500, detail="追踪器创建后读取失败")
        return await _build_tracker_response(storage, created_tracker)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"创建失败: {str(e)}")


@router.put("/{tracker_name}", dependencies=[Depends(get_current_user)])
async def update_tracker(
    tracker_name: str,
    tracker_data: AggregateTrackerPayload,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    scheduler: Annotated[ReleaseScheduler, Depends(get_scheduler)],
):
    try:
        aggregate_tracker = tracker_data.to_aggregate_tracker()
        runtime_config = tracker_data.to_runtime_config()
        existing = await storage.get_aggregate_tracker(tracker_name)
        if not existing:
            raise HTTPException(status_code=404, detail="追踪器不存在")

        normalized_name = aggregate_tracker.name
        if normalized_name != tracker_name:
            raise HTTPException(status_code=400, detail="不支持修改追踪器名称")

        await storage.update_aggregate_tracker(
            aggregate_tracker.model_copy(update={"id": existing.id})
        )
        await storage.save_tracker_runtime_config(runtime_config)
        persisted_runtime = await storage.get_tracker_config(tracker_name)
        if persisted_runtime is None:
            raise HTTPException(status_code=500, detail="追踪器运行时配置写入失败")
        await scheduler.refresh_tracker(tracker_name)
        await scheduler.rebuild_tracker_views_from_storage(tracker_name)

        updated_tracker = await storage.get_aggregate_tracker(tracker_name)
        if updated_tracker is None:
            raise HTTPException(status_code=500, detail="追踪器更新后读取失败")
        return await _build_tracker_response(storage, updated_tracker)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"更新失败: {str(e)}")


@router.delete("/{tracker_name}", dependencies=[Depends(get_current_user)])
async def delete_tracker(
    tracker_name: str,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    scheduler: Annotated[ReleaseScheduler, Depends(get_scheduler)],
):
    existing = await storage.get_aggregate_tracker(tracker_name)
    if not existing:
        raise HTTPException(status_code=404, detail="追踪器不存在")

    await storage.delete_aggregate_tracker(tracker_name)
    await storage.delete_tracker_config(tracker_name)
    await storage.delete_tracker_status(tracker_name)
    await scheduler.remove_tracker(tracker_name)

    return {"name": tracker_name, "deleted": True}
