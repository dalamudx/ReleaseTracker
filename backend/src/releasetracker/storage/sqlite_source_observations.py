"""Source-release observation persistence helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TYPE_CHECKING
import aiosqlite
from ..models import Release, TrackerSource

if TYPE_CHECKING:
    from .sqlite import SQLiteStorage


async def _upsert_source_release_observation(
    storage: "SQLiteStorage",
    db: aiosqlite.Connection,
    tracker_source_id: int,
    release: Release,
    *,
    observed_at: datetime,
    raw_payload: dict[str, Any] | None = None,
    changelog_url: str | None = None,
    source_type: str | None = None,
) -> int:
    comparison_version, app_version, chart_version = storage._release_version_metadata(
        release, source_type=source_type
    )
    tag_name = (
        chart_version or storage._normalize_release_value(release.tag_name) or comparison_version
    )
    source_release_key = tag_name
    if not source_release_key:
        raise ValueError("release tag_name or version must be a non-empty string")

    observed_at_iso = observed_at.isoformat()
    persisted_raw_payload = dict(raw_payload or {})
    if app_version is not None:
        persisted_raw_payload["appVersion"] = app_version
    if chart_version is not None:
        persisted_raw_payload["chartVersion"] = chart_version
    raw_payload_json = storage._dump_json(persisted_raw_payload)
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        """
        SELECT id, version, published_at, commit_sha
        FROM source_release_observations
        WHERE tracker_source_id = ? AND source_release_key = ?
        """,
        (tracker_source_id, source_release_key),
    )
    existing_row = await cursor.fetchone()

    if existing_row is None:
        cursor = await db.execute(
            """
            INSERT INTO source_release_observations
            (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tracker_source_id,
                source_release_key,
                release.name,
                tag_name,
                comparison_version,
                release.published_at.isoformat(),
                release.url,
                changelog_url,
                1 if release.prerelease else 0,
                release.body,
                release.commit_sha,
                raw_payload_json,
                observed_at_iso,
                observed_at_iso,
                observed_at_iso,
            ),
        )
        return storage._require_lastrowid(cursor.lastrowid, "source release observation")

    persisted_published_at = release.published_at.isoformat()
    normalized_existing_commit = storage._normalize_release_value(existing_row["commit_sha"])
    normalized_new_commit = storage._normalize_release_value(release.commit_sha)

    # Mirror the digest-diff logic from append_source_history_for_run:
    # container releases preserve their prior published_at unless the
    # digest itself changes (and failed-lookup digests don't count as
    # a change), while other sources keep preserving on version+commit
    # identity like before.
    if existing_row["version"] == comparison_version:
        if source_type == "container":
            digest_matches_or_absent = (
                normalized_new_commit is None
                or normalized_existing_commit is None
                or normalized_existing_commit == normalized_new_commit
            )
            if digest_matches_or_absent:
                persisted_published_at = existing_row["published_at"]
        elif (
            normalized_existing_commit is not None
            and normalized_existing_commit == normalized_new_commit
        ):
            persisted_published_at = existing_row["published_at"]

    await db.execute(
        """
        UPDATE source_release_observations
        SET name = ?,
            tag_name = ?,
            version = ?,
            published_at = ?,
            url = ?,
            changelog_url = ?,
            prerelease = ?,
            body = ?,
            commit_sha = ?,
            raw_payload = ?,
            observed_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            release.name,
            tag_name,
            comparison_version,
            persisted_published_at,
            release.url,
            changelog_url,
            1 if release.prerelease else 0,
            release.body,
            release.commit_sha,
            raw_payload_json,
            observed_at_iso,
            observed_at_iso,
            existing_row["id"],
        ),
    )
    return existing_row["id"]


async def save_source_observations(
    storage: "SQLiteStorage",
    aggregate_tracker_id: int,
    tracker_source: TrackerSource,
    releases: list[Release],
    *,
    observed_at: datetime | None = None,
    append_truth: bool = True,
) -> list[int]:
    if tracker_source.id is None:
        raise ValueError("tracker_source.id is required to save source observations")

    persisted_observation_ids: list[int] = []
    timestamp = observed_at or datetime.now()

    if append_truth:
        source_fetch_run_id = await storage.create_source_fetch_run(
            tracker_source.id,
            trigger_mode="bootstrap",
            started_at=timestamp,
        )
        await storage.append_source_history_for_run(
            source_fetch_run_id,
            tracker_source,
            releases,
            aggregate_tracker_id=aggregate_tracker_id,
            observed_at=timestamp,
        )
        await storage.finalize_source_fetch_run(
            source_fetch_run_id,
            status="success",
            fetched_count=len(releases),
            filtered_in_count=len(releases),
            finished_at=timestamp,
        )

    source_release_keys = {
        storage._source_release_key_for_release(release, source_type=tracker_source.source_type)
        for release in releases
    }

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    for release in releases:
        observation_id = await storage._upsert_source_release_observation(
            db,
            tracker_source.id,
            release,
            observed_at=timestamp,
            raw_payload={
                "aggregate_tracker_id": aggregate_tracker_id,
                "source_key": tracker_source.source_key,
                "source_type": tracker_source.source_type,
                "channel_name": release.channel_name,
            },
            changelog_url=getattr(release, "changelog_url", None),
            source_type=tracker_source.source_type,
        )
        persisted_observation_ids.append(observation_id)

    if source_release_keys:
        placeholders = ", ".join("?" for _ in source_release_keys)
        await db.execute(
            f"DELETE FROM source_release_observations WHERE tracker_source_id = ? AND source_release_key NOT IN ({placeholders})",
            (tracker_source.id, *source_release_keys),
        )
    else:
        await db.execute(
            "DELETE FROM source_release_observations WHERE tracker_source_id = ?",
            (tracker_source.id,),
        )

    await storage._rebuild_canonical_releases_for_tracker(db, aggregate_tracker_id)

    await db.commit()

    return persisted_observation_ids
