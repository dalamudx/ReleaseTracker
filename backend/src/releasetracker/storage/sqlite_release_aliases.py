"""Persist source aliases separately from immutable release artifacts."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from ..models import Release, TrackerSource

if TYPE_CHECKING:
    from .sqlite import SQLiteStorage


async def upsert_source_release_alias(
    storage: "SQLiteStorage",
    db: aiosqlite.Connection,
    *,
    source_fetch_run_id: int,
    tracker_source: TrackerSource,
    source_release_history_id: int,
    release: Release,
    observed_at: datetime,
) -> int:
    if tracker_source.id is None:
        raise ValueError("tracker_source.id is required to persist a release alias")
    alias = storage._normalize_release_value(release.tag_name) or storage._normalize_release_value(
        release.version
    )
    if alias is None:
        raise ValueError("release alias must be a non-empty string")
    normalized_alias = storage._canonical_key_for_version(alias).lower()
    timestamp = observed_at.isoformat()
    await db.execute(
        """
        INSERT INTO source_release_aliases
        (source_release_history_id, tracker_source_id, alias, normalized_alias, channel_name,
         first_source_fetch_run_id, last_source_fetch_run_id, first_observed_at,
         last_observed_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tracker_source_id, source_release_history_id, normalized_alias)
        DO UPDATE SET alias = excluded.alias,
                      channel_name = excluded.channel_name,
                      last_source_fetch_run_id = excluded.last_source_fetch_run_id,
                      last_observed_at = excluded.last_observed_at,
                      updated_at = excluded.updated_at
        """,
        (
            source_release_history_id,
            tracker_source.id,
            alias,
            normalized_alias,
            release.channel_name,
            source_fetch_run_id,
            source_fetch_run_id,
            timestamp,
            timestamp,
            timestamp,
            timestamp,
        ),
    )
    row = await (
        await db.execute(
            """
            SELECT id
            FROM source_release_aliases
            WHERE tracker_source_id = ?
              AND source_release_history_id = ?
              AND normalized_alias = ?
            """,
            (tracker_source.id, source_release_history_id, normalized_alias),
        )
    ).fetchone()
    if row is None:
        raise ValueError("Failed to read persisted source release alias")
    alias_id = int(row["id"])
    await db.execute(
        """
        INSERT OR IGNORE INTO source_release_alias_run_observations
        (source_fetch_run_id, source_release_alias_id, observed_at, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (source_fetch_run_id, alias_id, timestamp, timestamp),
    )
    return alias_id


async def get_source_release_aliases_by_history_ids(
    storage: "SQLiteStorage", source_release_history_ids: list[int]
) -> dict[int, list[dict[str, Any]]]:
    if not source_release_history_ids:
        return {}
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    placeholders = ", ".join("?" for _ in source_release_history_ids)
    rows = await (
        await db.execute(
            f"""
            SELECT *
            FROM source_release_aliases
            WHERE source_release_history_id IN ({placeholders})
            ORDER BY source_release_history_id ASC, last_observed_at DESC,
                     normalized_alias ASC
            """,
            tuple(source_release_history_ids),
        )
    ).fetchall()
    result: dict[int, list[dict[str, Any]]] = {
        history_id: [] for history_id in source_release_history_ids
    }
    for row in rows:
        result[int(row["source_release_history_id"])].append(
            {
                "id": int(row["id"]),
                "alias": row["alias"],
                "normalized_alias": row["normalized_alias"],
                "channel_name": row["channel_name"],
                "first_observed_at": row["first_observed_at"],
                "last_observed_at": row["last_observed_at"],
                "first_source_fetch_run_id": int(row["first_source_fetch_run_id"]),
                "last_source_fetch_run_id": int(row["last_source_fetch_run_id"]),
            }
        )
    return result


async def get_current_source_release_aliases_by_history_ids(
    storage: "SQLiteStorage", source_release_history_ids: list[int]
) -> dict[int, list[dict[str, Any]]]:
    """Return aliases whose latest source-local observation points at each history row."""
    if not source_release_history_ids:
        return {}
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    placeholders = ", ".join("?" for _ in source_release_history_ids)
    rows = await (
        await db.execute(
            f"""
            WITH requested_history AS (
                SELECT id, tracker_source_id
                FROM source_release_history
                WHERE id IN ({placeholders})
            ),
            relevant_sources AS (
                SELECT DISTINCT tracker_source_id
                FROM requested_history
            ),
            ranked_aliases AS (
                SELECT sra.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY sra.tracker_source_id, sra.normalized_alias
                           ORDER BY sra.last_observed_at DESC, sra.id DESC
                       ) AS alias_rank
                FROM source_release_aliases sra
                JOIN relevant_sources rs
                  ON rs.tracker_source_id = sra.tracker_source_id
            )
            SELECT ranked_aliases.*
            FROM ranked_aliases
            JOIN requested_history rh
              ON rh.id = ranked_aliases.source_release_history_id
            WHERE ranked_aliases.alias_rank = 1
            ORDER BY ranked_aliases.source_release_history_id ASC,
                     ranked_aliases.normalized_alias ASC
            """,
            tuple(source_release_history_ids),
        )
    ).fetchall()
    result: dict[int, list[dict[str, Any]]] = {
        history_id: [] for history_id in source_release_history_ids
    }
    for row in rows:
        result[int(row["source_release_history_id"])].append(
            {
                "id": int(row["id"]),
                "alias": row["alias"],
                "normalized_alias": row["normalized_alias"],
                "channel_name": row["channel_name"],
                "first_observed_at": row["first_observed_at"],
                "last_observed_at": row["last_observed_at"],
                "first_source_fetch_run_id": int(row["first_source_fetch_run_id"]),
                "last_source_fetch_run_id": int(row["last_source_fetch_run_id"]),
            }
        )
    return result


async def get_source_release_aliases_for_source(
    storage: "SQLiteStorage", tracker_source_id: int
) -> dict[int, list[dict[str, Any]]]:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    rows = await (
        await db.execute(
            """
            SELECT source_release_history_id
            FROM source_release_aliases
            WHERE tracker_source_id = ?
            GROUP BY source_release_history_id
            """,
            (tracker_source_id,),
        )
    ).fetchall()
    return await get_source_release_aliases_by_history_ids(
        storage, [int(row["source_release_history_id"]) for row in rows]
    )


async def get_source_alias_last_observed(
    storage: "SQLiteStorage", tracker_source_id: int
) -> dict[str, datetime]:
    """Return the latest persisted observation time for each concrete source alias."""
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    rows = await (
        await db.execute(
            """
            SELECT alias, MAX(last_observed_at) AS last_observed_at
            FROM source_release_aliases
            WHERE tracker_source_id = ?
            GROUP BY alias
            """,
            (tracker_source_id,),
        )
    ).fetchall()
    return {
        str(row["alias"]): datetime.fromisoformat(row["last_observed_at"])
        for row in rows
        if row["alias"] and row["last_observed_at"]
    }


async def get_source_alias_latest_digests(
    storage: "SQLiteStorage", tracker_source_id: int
) -> dict[str, str]:
    """Return each alias's most recently observed immutable artifact digest."""
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    rows = await (
        await db.execute(
            """
            WITH ranked_aliases AS (
                SELECT sra.alias, srh.digest,
                       ROW_NUMBER() OVER (
                           PARTITION BY sra.normalized_alias
                           ORDER BY sra.last_observed_at DESC, sra.id DESC
                       ) AS rank
                FROM source_release_aliases sra
                JOIN source_release_history srh
                  ON srh.id = sra.source_release_history_id
                WHERE sra.tracker_source_id = ?
                  AND srh.digest IS NOT NULL
                  AND srh.digest != ''
            )
            SELECT alias, digest
            FROM ranked_aliases
            WHERE rank = 1
            """,
            (tracker_source_id,),
        )
    ).fetchall()
    return {str(row["alias"]): str(row["digest"]) for row in rows}
