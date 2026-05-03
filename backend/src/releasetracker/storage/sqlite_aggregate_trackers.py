from __future__ import annotations

from datetime import datetime
import json
from typing import TYPE_CHECKING, Any

import aiosqlite

from ..models import AggregateTracker, TrackerSource

if TYPE_CHECKING:
    from .sqlite import SQLiteStorage


def normalize_tracker_source(source: TrackerSource) -> TrackerSource:
    normalized_source_config = {
        key: value.strip() if isinstance(value, str) else value
        for key, value in source.source_config.items()
    }
    if source.release_channels:
        normalized_source_config["release_channels"] = [
            release_channel.model_dump(mode="json") for release_channel in source.release_channels
        ]

    return source.model_copy(update={"source_config": normalized_source_config})


def _load_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def row_to_tracker_source(row: Any) -> TrackerSource:
    return TrackerSource(
        id=row["id"],
        aggregate_tracker_id=row["aggregate_tracker_id"],
        source_key=row["source_key"],
        source_type=row["source_type"],
        enabled=bool(row["enabled"]),
        credential_name=row["credential_name"],
        source_config=_load_json(row["source_config"]),
        source_rank=row["source_rank"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def select_runtime_source(tracker: AggregateTracker) -> TrackerSource | None:
    if tracker.primary_changelog_source_key:
        primary_source = next(
            (
                source
                for source in tracker.sources
                if source.source_key == tracker.primary_changelog_source_key
            ),
            None,
        )
        if primary_source is not None:
            return primary_source

    enabled_sources = [source for source in tracker.sources if source.enabled]
    if enabled_sources:
        return enabled_sources[0]

    if tracker.sources:
        return tracker.sources[0]

    return None


def flatten_runtime_release_channels(
    tracker: AggregateTracker,
    runtime_config,
    selected_source: TrackerSource | None,
) -> list[dict[str, Any]]:
    _ = tracker, runtime_config
    if selected_source is None or not selected_source.release_channels:
        return []

    channels: list[dict[str, Any]] = []
    for release_channel in selected_source.release_channels:
        payload = (
            release_channel.model_dump()
            if hasattr(release_channel, "model_dump")
            else dict(release_channel)
        )
        release_channel_key = payload.get("release_channel_key") or payload.get("channel_key")
        if release_channel_key:
            payload["release_channel_key"] = str(release_channel_key)
            payload["channel_key"] = str(release_channel_key)
        payload["source_type"] = selected_source.source_type
        channels.append(payload)
    return channels


async def load_tracker_sources(
    storage: "SQLiteStorage", db: aiosqlite.Connection, aggregate_tracker_id: int
) -> list[TrackerSource]:
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        """
        SELECT *
        FROM aggregate_tracker_sources
        WHERE aggregate_tracker_id = ?
        ORDER BY source_rank ASC, id ASC
        """,
        (aggregate_tracker_id,),
    )
    rows = await cursor.fetchall()
    return [row_to_tracker_source(row) for row in rows]


async def load_aggregate_tracker_from_row(
    storage: "SQLiteStorage", db: aiosqlite.Connection, row: aiosqlite.Row
) -> AggregateTracker:
    sources = await load_tracker_sources(storage, db, row["id"])
    primary_source_key = None
    if row["primary_changelog_source_id"] is not None:
        primary_source = next(
            (source for source in sources if source.id == row["primary_changelog_source_id"]),
            None,
        )
        primary_source_key = primary_source.source_key if primary_source else None

    return AggregateTracker(
        id=row["id"],
        name=row["name"],
        enabled=bool(row["enabled"]),
        changelog_policy=row["changelog_policy"],
        primary_changelog_source_key=primary_source_key,
        description=row["description"],
        sources=sources,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


async def persist_tracker_sources(
    storage: "SQLiteStorage",
    db: aiosqlite.Connection,
    aggregate_tracker_id: int,
    sources: list[TrackerSource],
    primary_changelog_source_key: str | None,
) -> int | None:
    db.row_factory = aiosqlite.Row
    existing_cursor = await db.execute(
        "SELECT * FROM aggregate_tracker_sources WHERE aggregate_tracker_id = ?",
        (aggregate_tracker_id,),
    )
    existing_rows = await existing_cursor.fetchall()
    existing_by_key = {row["source_key"]: row for row in existing_rows}
    seen_source_keys: set[str] = set()
    primary_source_id = None

    for source in sources:
        normalized_source = normalize_tracker_source(source)
        now = datetime.now().isoformat()
        existing_row = existing_by_key.get(normalized_source.source_key)

        if existing_row is None:
            cursor = await db.execute(
                """
                INSERT INTO aggregate_tracker_sources
                (aggregate_tracker_id, source_key, source_type, enabled, credential_name, source_config, source_rank, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aggregate_tracker_id,
                    normalized_source.source_key,
                    normalized_source.source_type,
                    1 if normalized_source.enabled else 0,
                    normalized_source.credential_name,
                    storage._dump_json(normalized_source.source_config),
                    normalized_source.source_rank,
                    normalized_source.created_at.isoformat(),
                    now,
                ),
            )
            source_id = storage._require_lastrowid(cursor.lastrowid, "aggregate tracker source")
        else:
            source_id = existing_row["id"]
            await db.execute(
                """
                UPDATE aggregate_tracker_sources
                SET source_type = ?,
                    enabled = ?,
                    credential_name = ?,
                    source_config = ?,
                    source_rank = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    normalized_source.source_type,
                    1 if normalized_source.enabled else 0,
                    normalized_source.credential_name,
                    storage._dump_json(normalized_source.source_config),
                    normalized_source.source_rank,
                    now,
                    source_id,
                ),
            )

        if normalized_source.source_key == primary_changelog_source_key:
            primary_source_id = source_id

        seen_source_keys.add(normalized_source.source_key)

    removed_source_ids: list[int] = []
    for source_key, existing_row in existing_by_key.items():
        if source_key not in seen_source_keys:
            removed_source_ids.append(existing_row["id"])

    if removed_source_ids:
        await storage._cleanup_removed_tracker_sources(db, aggregate_tracker_id, removed_source_ids)
        removed_placeholders = ", ".join("?" for _ in removed_source_ids)
        await db.execute(
            f"DELETE FROM aggregate_tracker_sources WHERE id IN ({removed_placeholders})",
            tuple(removed_source_ids),
        )
        await storage._rebuild_canonical_releases_for_tracker(db, aggregate_tracker_id)

    if primary_changelog_source_key is not None and primary_source_id is None:
        raise ValueError(
            "primary_changelog_source_key must reference one of the persisted tracker sources"
        )

    return primary_source_id


async def create_aggregate_tracker(
    storage: "SQLiteStorage", tracker: AggregateTracker
) -> AggregateTracker:
    existing_tracker = await get_aggregate_tracker(storage, tracker.name)
    if existing_tracker is not None:
        return await update_aggregate_tracker(
            storage,
            tracker.model_copy(
                update={
                    "id": existing_tracker.id,
                    "created_at": existing_tracker.created_at,
                }
            ),
        )

    db = await storage._get_connection()
    now = datetime.now()
    cursor = await db.execute(
        """
        INSERT INTO aggregate_trackers
        (name, enabled, changelog_policy, primary_changelog_source_id, description, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tracker.name,
            1 if tracker.enabled else 0,
            tracker.changelog_policy,
            None,
            tracker.description,
            tracker.created_at.isoformat(),
            now.isoformat(),
        ),
    )
    aggregate_tracker_id = storage._require_lastrowid(cursor.lastrowid, "aggregate tracker")
    primary_source_id = await persist_tracker_sources(
        storage,
        db,
        aggregate_tracker_id,
        tracker.sources,
        tracker.primary_changelog_source_key,
    )
    await db.execute(
        """
        UPDATE aggregate_trackers
        SET primary_changelog_source_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (primary_source_id, now.isoformat(), aggregate_tracker_id),
    )
    await db.commit()

    created_tracker = await get_aggregate_tracker(storage, tracker.name)
    if created_tracker is None:
        raise ValueError(f"Failed to load aggregate tracker after create: {tracker.name}")
    return created_tracker


async def get_aggregate_tracker(storage: "SQLiteStorage", name: str) -> AggregateTracker | None:
    if not await storage._aggregate_schema_available():
        return None
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM aggregate_trackers WHERE name = ?", (name,))
    row = await cursor.fetchone()
    if row is None:
        return None
    return await load_aggregate_tracker_from_row(storage, db, row)


async def get_all_aggregate_trackers(storage: "SQLiteStorage") -> list[AggregateTracker]:
    if not await storage._aggregate_schema_available():
        return []
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM aggregate_trackers ORDER BY name ASC")
    rows = await cursor.fetchall()
    return [await load_aggregate_tracker_from_row(storage, db, row) for row in rows]


async def get_executor_binding(
    storage: "SQLiteStorage", tracker_source_id: int
) -> tuple[AggregateTracker, TrackerSource] | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        """
        SELECT at.*
        FROM aggregate_trackers at
        JOIN aggregate_tracker_sources ats ON ats.aggregate_tracker_id = at.id
        WHERE ats.id = ?
        """,
        (tracker_source_id,),
    )
    tracker_row = await cursor.fetchone()
    if tracker_row is None:
        return None

    aggregate_tracker = await load_aggregate_tracker_from_row(storage, db, tracker_row)
    tracker_source = next(
        (source for source in aggregate_tracker.sources if source.id == tracker_source_id),
        None,
    )
    if tracker_source is None:
        return None
    return aggregate_tracker, tracker_source


async def update_aggregate_tracker(
    storage: "SQLiteStorage", tracker: AggregateTracker
) -> AggregateTracker:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    if tracker.id is not None:
        cursor = await db.execute("SELECT * FROM aggregate_trackers WHERE id = ?", (tracker.id,))
    else:
        cursor = await db.execute(
            "SELECT * FROM aggregate_trackers WHERE name = ?", (tracker.name,)
        )

    existing_row = await cursor.fetchone()
    if existing_row is None:
        raise ValueError(f"Aggregate tracker not found: {tracker.name}")

    now = datetime.now().isoformat()
    await db.execute(
        """
        UPDATE aggregate_trackers
        SET name = ?,
            enabled = ?,
            changelog_policy = ?,
            primary_changelog_source_id = NULL,
            description = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            tracker.name,
            1 if tracker.enabled else 0,
            tracker.changelog_policy,
            tracker.description,
            now,
            existing_row["id"],
        ),
    )
    primary_source_id = await persist_tracker_sources(
        storage,
        db,
        existing_row["id"],
        tracker.sources,
        tracker.primary_changelog_source_key,
    )
    await db.execute(
        """
        UPDATE aggregate_trackers
        SET primary_changelog_source_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (primary_source_id, now, existing_row["id"]),
    )
    await db.commit()

    updated_tracker = await get_aggregate_tracker(storage, tracker.name)
    if updated_tracker is None:
        raise ValueError(f"Failed to load aggregate tracker after update: {tracker.name}")
    return updated_tracker


async def delete_aggregate_tracker(storage: "SQLiteStorage", name: str) -> None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT id FROM aggregate_trackers WHERE name = ?", (name,))
    row = await cursor.fetchone()
    if row is None:
        return

    aggregate_tracker_id = row["id"]
    source_cursor = await db.execute(
        "SELECT id FROM aggregate_tracker_sources WHERE aggregate_tracker_id = ?",
        (aggregate_tracker_id,),
    )
    source_rows = await source_cursor.fetchall()
    source_ids = [source_row["id"] for source_row in source_rows]

    canonical_cursor = await db.execute(
        "SELECT id FROM canonical_releases WHERE aggregate_tracker_id = ?",
        (aggregate_tracker_id,),
    )
    canonical_rows = await canonical_cursor.fetchall()
    canonical_release_ids = [canonical_row["id"] for canonical_row in canonical_rows]

    if canonical_release_ids:
        canonical_placeholders = ", ".join("?" for _ in canonical_release_ids)
        await db.execute(
            f"DELETE FROM canonical_release_observations WHERE canonical_release_id IN ({canonical_placeholders})",
            tuple(canonical_release_ids),
        )

    await db.execute(
        "DELETE FROM canonical_releases WHERE aggregate_tracker_id = ?",
        (aggregate_tracker_id,),
    )

    if source_ids:
        source_placeholders = ", ".join("?" for _ in source_ids)
        await db.execute(
            f"DELETE FROM canonical_release_observations WHERE source_release_observation_id IN (SELECT id FROM source_release_observations WHERE tracker_source_id IN ({source_placeholders}))",
            tuple(source_ids),
        )
        await db.execute(
            f"DELETE FROM source_release_observations WHERE tracker_source_id IN ({source_placeholders})",
            tuple(source_ids),
        )

    await db.execute(
        "UPDATE aggregate_trackers SET primary_changelog_source_id = NULL WHERE id = ?",
        (aggregate_tracker_id,),
    )
    await db.execute(
        "DELETE FROM aggregate_tracker_sources WHERE aggregate_tracker_id = ?",
        (aggregate_tracker_id,),
    )
    await db.execute("DELETE FROM aggregate_trackers WHERE id = ?", (aggregate_tracker_id,))
    await db.commit()
