"""Current-release projection persistence helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TYPE_CHECKING
import aiosqlite
from ..models import Release, AggregateTracker

if TYPE_CHECKING:
    from .sqlite import SQLiteStorage


async def refresh_tracker_current_releases(
    storage: "SQLiteStorage",
    aggregate_tracker_id: int,
    releases: list[Release],
    *,
    source_type: str | None = None,
) -> None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    projection_releases = releases
    projected_at = datetime.now().isoformat()

    await db.execute(
        "DELETE FROM tracker_current_releases WHERE aggregate_tracker_id = ?",
        (aggregate_tracker_id,),
    )

    for release in storage.dedupe_releases_by_immutable_identity(projection_releases):
        identity_key = storage.release_identity_key_for_source(release, source_type=source_type)
        digest = release.artifact_digest or storage._release_digest_value(
            release, source_type=source_type
        )
        history_row = await (
            await db.execute(
                """
                SELECT id, digest
                FROM tracker_release_history
                WHERE aggregate_tracker_id = ? AND immutable_key = ?
                  AND merged_into_tracker_release_history_id IS NULL
                """,
                (aggregate_tracker_id, identity_key),
            )
        ).fetchone()
        if history_row is None:
            continue
        digest = digest or history_row["digest"]

        await db.execute(
            """
            INSERT INTO tracker_current_releases
            (aggregate_tracker_id, identity_key, immutable_key, version, digest, tracker_release_history_id, name, tag_name, published_at, url, changelog_url, prerelease, body, projected_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aggregate_tracker_id,
                identity_key,
                identity_key,
                release.version,
                digest,
                history_row["id"],
                release.name,
                release.tag_name,
                release.published_at.isoformat(),
                release.url,
                release.changelog_url,
                1 if release.prerelease else 0,
                release.body,
                projected_at,
                projected_at,
            ),
        )

    await db.commit()


async def _get_tracker_current_projection_rows_by_aggregate_tracker_id(
    storage: "SQLiteStorage", aggregate_tracker_ids: list[int]
) -> dict[int, list[dict[str, Any]]]:
    """Load current-release projections for a page of aggregate trackers in one query."""
    if not aggregate_tracker_ids:
        return {}

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    placeholders = ", ".join("?" for _ in aggregate_tracker_ids)
    rows = await (
        await db.execute(
            f"""
            SELECT tcr.*,
                   trh.id AS tracker_release_history_id,
                   trh.created_at AS tracker_created_at,
                   srh.id AS primary_source_release_history_id,
                   ats.source_key AS primary_source_key,
                   ats.source_type AS primary_source_type,
                   srh.commit_sha,
                   srh.raw_payload
            FROM tracker_current_releases tcr
            JOIN tracker_release_history trh ON trh.id = tcr.tracker_release_history_id
            JOIN source_release_history srh ON srh.id = trh.primary_source_release_history_id
            LEFT JOIN aggregate_tracker_sources ats ON ats.id = srh.tracker_source_id
            WHERE tcr.aggregate_tracker_id IN ({placeholders})
            ORDER BY tcr.aggregate_tracker_id ASC, tcr.published_at DESC, tcr.id DESC
            """,
            tuple(aggregate_tracker_ids),
        )
    ).fetchall()

    projections: dict[int, list[dict[str, Any]]] = {
        tracker_id: [] for tracker_id in aggregate_tracker_ids
    }
    for row in rows:
        raw_payload = storage._load_json(row["raw_payload"])
        projections[row["aggregate_tracker_id"]].append(
            {
                "tracker_release_history_id": row["tracker_release_history_id"],
                "identity_key": row["identity_key"],
                "version": row["version"],
                "digest": row["digest"],
                "published_at": datetime.fromisoformat(row["published_at"]),
                "name": row["name"],
                "tag_name": row["tag_name"],
                "prerelease": bool(row["prerelease"]),
                "url": row["url"],
                "changelog_url": row["changelog_url"],
                "body": row["body"],
                "projected_at": datetime.fromisoformat(row["projected_at"]),
                "primary_source": (
                    {
                        "source_key": row["primary_source_key"],
                        "source_type": row["primary_source_type"],
                        "source_release_history_id": row["primary_source_release_history_id"],
                    }
                    if row["primary_source_release_history_id"] is not None
                    else None
                ),
                "release": Release(
                    id=row["tracker_release_history_id"],
                    tracker_name="",
                    tracker_type=row["primary_source_type"] or "github",
                    name=row["name"],
                    tag_name=row["tag_name"],
                    version=row["version"],
                    app_version=raw_payload.get("appVersion"),
                    chart_version=raw_payload.get("chartVersion"),
                    published_at=datetime.fromisoformat(row["published_at"]),
                    url=row["url"],
                    changelog_url=row["changelog_url"],
                    prerelease=bool(row["prerelease"]),
                    body=row["body"],
                    channel_name=raw_payload.get("channel_name"),
                    commit_sha=row["commit_sha"],
                    artifact_digest=row["digest"],
                    created_at=datetime.fromisoformat(row["tracker_created_at"]),
                ),
            }
        )
    return projections


async def get_tracker_current_releases(
    storage: "SQLiteStorage", aggregate_tracker_id: int
) -> list[Release]:
    projection_rows = await storage._get_tracker_current_projection_rows(aggregate_tracker_id)
    return [row["release"] for row in projection_rows]


async def _get_tracker_current_projection_rows(
    storage: "SQLiteStorage", aggregate_tracker_id: int
) -> list[dict[str, Any]]:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    rows = await (
        await db.execute(
            """
            SELECT tcr.*,
                   trh.id AS tracker_release_history_id,
                   trh.created_at AS tracker_created_at,
                   srh.id AS primary_source_release_history_id,
                   ats.source_key AS primary_source_key,
                   ats.source_type AS primary_source_type,
                   srh.commit_sha,
                   srh.raw_payload
            FROM tracker_current_releases tcr
            JOIN tracker_release_history trh ON trh.id = tcr.tracker_release_history_id
            JOIN source_release_history srh ON srh.id = trh.primary_source_release_history_id
            LEFT JOIN aggregate_tracker_sources ats ON ats.id = srh.tracker_source_id
            WHERE tcr.aggregate_tracker_id = ?
            ORDER BY tcr.published_at DESC, tcr.id DESC
            """,
            (aggregate_tracker_id,),
        )
    ).fetchall()

    projection_rows: list[dict[str, Any]] = []
    for row in rows:
        raw_payload = storage._load_json(row["raw_payload"])
        projection_rows.append(
            {
                "tracker_release_history_id": row["tracker_release_history_id"],
                "identity_key": row["identity_key"],
                "version": row["version"],
                "digest": row["digest"],
                "published_at": datetime.fromisoformat(row["published_at"]),
                "name": row["name"],
                "tag_name": row["tag_name"],
                "prerelease": bool(row["prerelease"]),
                "url": row["url"],
                "changelog_url": row["changelog_url"],
                "body": row["body"],
                "projected_at": datetime.fromisoformat(row["projected_at"]),
                "primary_source": (
                    {
                        "source_key": row["primary_source_key"],
                        "source_type": row["primary_source_type"],
                        "source_release_history_id": row["primary_source_release_history_id"],
                    }
                    if row["primary_source_release_history_id"] is not None
                    else None
                ),
                "release": Release(
                    id=row["tracker_release_history_id"],
                    tracker_name="",
                    tracker_type=row["primary_source_type"] or "github",
                    name=row["name"],
                    tag_name=row["tag_name"],
                    version=row["version"],
                    app_version=raw_payload.get("appVersion"),
                    chart_version=raw_payload.get("chartVersion"),
                    published_at=datetime.fromisoformat(row["published_at"]),
                    url=row["url"],
                    changelog_url=row["changelog_url"],
                    prerelease=bool(row["prerelease"]),
                    body=row["body"],
                    channel_name=raw_payload.get("channel_name"),
                    commit_sha=row["commit_sha"],
                    artifact_digest=row["digest"],
                    created_at=datetime.fromisoformat(row["tracker_created_at"]),
                ),
            }
        )
    return projection_rows


def _select_top_current_projection_release(
    cls,
    releases: list[Release],
    channels: list[Any],
    sort_mode: str,
) -> Release | None:
    if not releases:
        return None

    # Channels define eligibility, not priority. Select each channel's winner
    # and then apply the configured tracker ordering across those winners.
    return cls.select_best_release(
        releases,
        channels,
        sort_mode=sort_mode,
        use_immutable_identity=True,
    )


def _filter_projection_rows_by_channels(
    cls,
    rows: list[dict[str, Any]],
    channels: list[Any],
) -> list[dict[str, Any]]:
    enabled_channels = [channel for channel in channels if channel.enabled] if channels else []
    if not enabled_channels:
        return rows

    visible_rows: list[dict[str, Any]] = []
    for row in rows:
        release = row["release"]
        if any(
            cls._release_matches_channel(
                release,
                channel,
                channel_source_type=(
                    channel.get("source_type")
                    if isinstance(channel, dict)
                    else getattr(channel, "source_type", None)
                ),
            )
            for channel in enabled_channels
        ):
            visible_rows.append(row)

    return visible_rows


async def get_tracker_current_release_rows(
    storage: "SQLiteStorage", tracker_name: str
) -> list[dict[str, Any]]:
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    if aggregate_tracker is None or aggregate_tracker.id is None:
        return []

    current_rows = await storage._get_tracker_current_projection_rows(aggregate_tracker.id)
    tracker_config = await storage.get_tracker_config(tracker_name)
    channels = tracker_config.channels if tracker_config is not None else []
    return storage._filter_projection_rows_by_channels(current_rows, channels)


async def get_tracker_latest_current_release_summary(
    storage: "SQLiteStorage", tracker_name: str
) -> dict[str, Any] | None:
    current_rows = await storage.get_tracker_current_release_rows(tracker_name)
    if not current_rows:
        return None

    tracker_config = await storage.get_tracker_config(tracker_name)
    sort_mode = tracker_config.version_sort_mode if tracker_config is not None else "published_at"
    channels = tracker_config.channels if tracker_config is not None else []
    current_releases = [
        row["release"].model_copy(update={"tracker_name": tracker_name}) for row in current_rows
    ]
    latest_release = storage._select_top_current_projection_release(
        current_releases,
        channels,
        sort_mode,
    )
    if latest_release is None:
        return None

    latest_identity_key = storage.release_identity_key_for_source(
        latest_release,
        source_type=latest_release.tracker_type,
    )
    latest_row = next(
        (row for row in current_rows if row["identity_key"] == latest_identity_key),
        None,
    )
    if latest_row is None:
        return None

    return {
        "tracker_name": tracker_name,
        "tracker_release_history_id": latest_row["tracker_release_history_id"],
        "identity_key": latest_row["identity_key"],
        "version": latest_release.version,
        "digest": latest_row["digest"],
        "published_at": latest_row["published_at"],
        "prerelease": latest_release.prerelease,
        "name": latest_release.name,
        "tag_name": latest_release.tag_name,
        "url": latest_release.url,
        "changelog_url": latest_release.changelog_url,
        "body": latest_release.body,
        "channel_name": latest_row["release"].channel_name,
        "primary_source": latest_row["primary_source"],
        "primary_source_type": (
            latest_row["primary_source"]["source_type"]
            if latest_row["primary_source"] is not None
            else None
        ),
        "projected_at": latest_row["projected_at"],
        "release": latest_release,
    }


async def get_tracker_current_status_derivation(
    storage: "SQLiteStorage", tracker_name: str
) -> dict[str, Any]:
    tracker_status = await storage.get_tracker_status(tracker_name)
    latest_summary = await storage.get_tracker_latest_current_release_summary(tracker_name)
    return {
        "tracker_name": tracker_name,
        "last_check": tracker_status.last_check if tracker_status is not None else None,
        "error": tracker_status.error if tracker_status is not None else None,
        "latest_identity_key": (
            latest_summary["identity_key"] if latest_summary is not None else None
        ),
        "latest_version": latest_summary["version"] if latest_summary is not None else None,
        "latest_tracker_release_history_id": (
            latest_summary["tracker_release_history_id"] if latest_summary is not None else None
        ),
        "projected_at": latest_summary["projected_at"] if latest_summary is not None else None,
    }


async def get_tracker_runtime_configs_for_aggregate_trackers(
    storage: "SQLiteStorage", trackers: list[AggregateTracker]
) -> dict[str, Any]:
    """Build runtime configs for an already-loaded tracker page without reloading sources."""
    if not trackers:
        return {}

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    runtime_rows_by_name: dict[str, aiosqlite.Row] = {}
    if await storage._table_exists("trackers"):
        placeholders = ", ".join("?" for _ in trackers)
        runtime_rows = await (
            await db.execute(
                f"SELECT * FROM trackers WHERE name IN ({placeholders})",
                tuple(tracker.name for tracker in trackers),
            )
        ).fetchall()
        runtime_rows_by_name = {row["name"]: row for row in runtime_rows}

    return {
        tracker.name: storage._aggregate_tracker_to_runtime_config(
            tracker, runtime_rows_by_name.get(tracker.name)
        )
        for tracker in trackers
    }


async def get_tracker_current_release_rows_for_aggregate_trackers(
    storage: "SQLiteStorage",
    trackers: list[AggregateTracker],
    runtime_configs: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Return channel-visible current rows for every tracker in a loaded page."""
    tracker_ids = [tracker.id for tracker in trackers if tracker.id is not None]
    projections_by_id = await storage._get_tracker_current_projection_rows_by_aggregate_tracker_id(
        tracker_ids
    )
    return {
        tracker.name: storage._filter_projection_rows_by_channels(
            projections_by_id.get(tracker.id, []),
            (
                runtime_configs.get(tracker.name).channels
                if runtime_configs.get(tracker.name) is not None
                else []
            ),
        )
        for tracker in trackers
    }


async def get_tracker_current_status_derivations_for_aggregate_trackers(
    storage: "SQLiteStorage",
    trackers: list[AggregateTracker],
    current_rows_by_tracker_name: dict[str, list[dict[str, Any]]],
    runtime_configs: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Derive list-page statuses from shared current-release projections."""
    if not trackers:
        return {}

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    placeholders = ", ".join("?" for _ in trackers)
    status_rows = await (
        await db.execute(
            f"SELECT * FROM tracker_status WHERE name IN ({placeholders})",
            tuple(tracker.name for tracker in trackers),
        )
    ).fetchall()
    statuses_by_name = {row["name"]: storage._row_to_tracker_status(row) for row in status_rows}

    derivations: dict[str, dict[str, Any]] = {}
    for tracker in trackers:
        current_rows = current_rows_by_tracker_name.get(tracker.name, [])
        runtime_config = runtime_configs.get(tracker.name)
        latest_row = None
        latest_release = None
        if current_rows:
            current_releases = [
                row["release"].model_copy(update={"tracker_name": tracker.name})
                for row in current_rows
            ]
            latest_release = storage._select_top_current_projection_release(
                current_releases,
                runtime_config.channels if runtime_config is not None else [],
                (
                    runtime_config.version_sort_mode
                    if runtime_config is not None
                    else "published_at"
                ),
            )
            if latest_release is not None:
                latest_identity_key = storage.release_identity_key_for_source(
                    latest_release, source_type=latest_release.tracker_type
                )
                latest_row = next(
                    (row for row in current_rows if row["identity_key"] == latest_identity_key),
                    None,
                )

        tracker_status = statuses_by_name.get(tracker.name)
        derivations[tracker.name] = {
            "tracker_name": tracker.name,
            "last_check": tracker_status.last_check if tracker_status is not None else None,
            "error": tracker_status.error if tracker_status is not None else None,
            "latest_identity_key": (latest_row["identity_key"] if latest_row is not None else None),
            "latest_version": latest_release.version if latest_release is not None else None,
            "latest_tracker_release_history_id": (
                latest_row["tracker_release_history_id"] if latest_row is not None else None
            ),
            "projected_at": latest_row["projected_at"] if latest_row is not None else None,
        }
    return derivations
