"""Source and tracker release-history persistence helpers."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from ..models import Release, TrackerSource

if TYPE_CHECKING:
    from .sqlite import SQLiteStorage


async def create_source_fetch_run(
    storage: "SQLiteStorage",
    tracker_source_id: int,
    *,
    trigger_mode: str,
    started_at: datetime | None = None,
) -> int:
    db = await storage._get_connection()
    timestamp = (started_at or datetime.now()).isoformat()
    cursor = await db.execute(
        """
        INSERT INTO source_fetch_runs
        (tracker_source_id, trigger_mode, started_at, status, created_at)
        VALUES (?, ?, ?, 'running', ?)
        """,
        (tracker_source_id, trigger_mode, timestamp, timestamp),
    )
    await db.commit()
    return storage._require_lastrowid(cursor.lastrowid, "source fetch run")


async def finalize_source_fetch_run(
    storage: "SQLiteStorage",
    source_fetch_run_id: int,
    *,
    status: str,
    fetched_count: int,
    filtered_in_count: int,
    error_message: str | None = None,
    finished_at: datetime | None = None,
) -> None:
    db = await storage._get_connection()
    finished_at_value = (finished_at or datetime.now()).isoformat()
    await db.execute(
        """
        UPDATE source_fetch_runs
        SET status = ?,
            fetched_count = ?,
            filtered_in_count = ?,
            error_message = ?,
            finished_at = ?
        WHERE id = ?
        """,
        (
            status,
            fetched_count,
            filtered_in_count,
            error_message,
            finished_at_value,
            source_fetch_run_id,
        ),
    )
    await db.commit()


async def append_source_history_for_run(
    storage: "SQLiteStorage",
    source_fetch_run_id: int,
    tracker_source: TrackerSource,
    releases: list[Release],
    *,
    aggregate_tracker_id: int | None = None,
    observed_at: datetime | None = None,
) -> dict[str, int]:
    if tracker_source.id is None:
        raise ValueError("tracker_source.id is required to append source history")

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    timestamp = (observed_at or datetime.now()).isoformat()
    source_history_ids_by_identity: dict[str, int] = {}

    for release in releases:
        version, app_version, chart_version = storage._release_version_metadata(
            release, source_type=tracker_source.source_type
        )
        source_release_key = storage._source_release_key_for_release(
            release, source_type=tracker_source.source_type
        )
        digest = storage._release_digest_value(release, source_type=tracker_source.source_type)
        identity_key = storage.release_identity_key_for_source(
            release, source_type=tracker_source.source_type
        )
        tag_name = chart_version or storage._normalize_release_value(release.tag_name) or version

        # When a container fetch couldn't resolve the manifest digest
        # (registry errors, intermittent unavailability, or manifest 404
        # for a retired tag) the release enters this function without
        # `commit_sha`. The identity_key for such a release falls back
        # to `version` instead of the digest, which would silently
        # create a *second* history row that shadows the real one.
        # Skip the write entirely if a row with a real digest already
        # exists for this (source, tag) so the stable observation and
        # its original published_at stay authoritative.
        if tracker_source.source_type == "container" and digest is None and tag_name is not None:
            prior_digest_row = await (
                await db.execute(
                    """
                    SELECT id
                    FROM source_release_history
                    WHERE tracker_source_id = ?
                      AND tag_name = ?
                      AND commit_sha IS NOT NULL
                      AND commit_sha != ''
                    LIMIT 1
                    """,
                    (tracker_source.id, tag_name),
                )
            ).fetchone()
            if prior_digest_row is not None:
                source_history_ids_by_identity[identity_key] = prior_digest_row["id"]
                await db.execute(
                    """
                    INSERT OR IGNORE INTO source_release_run_observations
                    (source_fetch_run_id, source_release_history_id, observed_at, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        source_fetch_run_id,
                        prior_digest_row["id"],
                        timestamp,
                        timestamp,
                    ),
                )
                continue

        raw_payload: dict[str, Any] = {
            "aggregate_tracker_id": aggregate_tracker_id,
            "source_key": tracker_source.source_key,
            "source_type": tracker_source.source_type,
            "channel_name": release.channel_name,
        }
        if app_version is not None:
            raw_payload["appVersion"] = app_version
        if chart_version is not None:
            raw_payload["chartVersion"] = chart_version

        await db.execute(
            """
            INSERT OR IGNORE INTO source_release_history
            (tracker_source_id, first_source_fetch_run_id, source_type, source_release_key, version, digest, digest_algorithm, digest_media_type, digest_platform, identity_key, immutable_key, name, tag_name, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, first_observed_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tracker_source.id,
                source_fetch_run_id,
                tracker_source.source_type,
                source_release_key,
                version,
                digest,
                "sha256" if digest else None,
                None,
                None,
                identity_key,
                identity_key,
                release.name,
                tag_name,
                release.published_at.isoformat(),
                release.url,
                getattr(release, "changelog_url", None),
                1 if release.prerelease else 0,
                release.body,
                release.commit_sha,
                storage._dump_json(raw_payload),
                timestamp,
                timestamp,
            ),
        )

        source_row = await (
            await db.execute(
                """
                SELECT id, version, tag_name, published_at, commit_sha
                FROM source_release_history
                WHERE tracker_source_id = ? AND immutable_key = ?
                """,
                (tracker_source.id, identity_key),
            )
        ).fetchone()
        if source_row is None:
            raise ValueError("Failed to read source release history row")
        source_history_id = source_row["id"]
        source_history_ids_by_identity[identity_key] = source_history_id

        normalized_existing_commit = storage._normalize_release_value(source_row["commit_sha"])
        normalized_new_commit = storage._normalize_release_value(release.commit_sha)
        preserved_published_at = release.published_at.isoformat()

        # Decide whether the existing published_at should be preserved.
        #
        # The goal is to treat published_at as "time of the most recent
        # content change", so the timestamp stays stable when nothing
        # about the release has actually changed.
        #
        # - Non-container sources (github/gitlab/gitea/helm): preserve when
        #   version and commit_sha match, i.e. the upstream API returned
        #   the exact same release metadata we already stored.
        # - Container sources: preserve when version matches AND digest
        #   is unchanged. If the existing digest is null (backfilled
        #   history or a prior fetch where manifest HEAD failed) treat a
        #   now-available digest as the "first time we learned what this
        #   tag points to", which is NOT a content change, so we still
        #   preserve the existing published_at.
        # - Container sources where we failed to resolve a digest this
        #   fetch (new digest is null): we have no way to tell if the
        #   image changed, so we must NOT reset published_at — preserve
        #   the previous value rather than claim a fake update.
        if source_row["version"] == version:
            if tracker_source.source_type == "container":
                digest_matches_or_absent = (
                    normalized_new_commit is None
                    or normalized_existing_commit is None
                    or normalized_existing_commit == normalized_new_commit
                )
                if digest_matches_or_absent:
                    preserved_published_at = source_row["published_at"]
            elif (
                normalized_existing_commit is not None
                and normalized_existing_commit == normalized_new_commit
            ):
                preserved_published_at = source_row["published_at"]

        if storage._should_replace_source_history_display(
            source_type=tracker_source.source_type,
            version=version,
            tag_name=tag_name,
            existing_version=source_row["version"],
            existing_tag_name=source_row["tag_name"],
        ):
            await db.execute(
                """
                UPDATE source_release_history
                SET source_release_key = ?,
                    version = ?,
                    digest = ?,
                    digest_algorithm = ?,
                    name = ?,
                    tag_name = ?,
                    published_at = ?,
                    url = ?,
                    changelog_url = ?,
                    prerelease = ?,
                    body = ?,
                    commit_sha = ?,
                    raw_payload = ?
                WHERE id = ?
                """,
                (
                    source_release_key,
                    version,
                    digest,
                    "sha256" if digest else None,
                    release.name,
                    tag_name,
                    preserved_published_at,
                    release.url,
                    getattr(release, "changelog_url", None),
                    1 if release.prerelease else 0,
                    release.body,
                    release.commit_sha,
                    storage._dump_json(raw_payload),
                    source_history_id,
                ),
            )

        await db.execute(
            """
            INSERT OR IGNORE INTO source_release_run_observations
            (source_fetch_run_id, source_release_history_id, observed_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (source_fetch_run_id, source_history_id, timestamp, timestamp),
        )

    await db.commit()
    return source_history_ids_by_identity


async def get_source_release_history_releases_by_source(
    storage: "SQLiteStorage",
    tracker_source_id: int,
) -> list[Release]:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    rows = await (
        await db.execute(
            """
            SELECT *
            FROM source_release_history
            WHERE tracker_source_id = ?
            ORDER BY published_at DESC, id DESC
            """,
            (tracker_source_id,),
        )
    ).fetchall()

    releases: list[Release] = []
    for row in rows:
        raw_payload = storage._load_json(row["raw_payload"])
        releases.append(
            Release(
                tracker_name="",
                tracker_type=row["source_type"],
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
                created_at=datetime.fromisoformat(row["created_at"]),
            )
        )

    return releases


async def get_source_release_history_id(
    storage: "SQLiteStorage",
    tracker_source_id: int,
    identity_key: str,
) -> int | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    row = await (
        await db.execute(
            """
            SELECT id
            FROM source_release_history
            WHERE tracker_source_id = ? AND immutable_key = ?
            """,
            (tracker_source_id, identity_key),
        )
    ).fetchone()
    return row["id"] if row else None


async def get_source_release_history_digests(
    storage: "SQLiteStorage",
    tracker_source_id: int,
    tag_names: list[str],
) -> dict[str, str | None]:
    """
    Return {tag_name → existing digest} for any rows already present.

    Used by incremental fetchers (primarily the container tracker) to skip
    additional metadata lookups on tags that are already stored with a
    known digest. Missing tags are absent from the returned mapping.
    """
    if not tag_names:
        return {}

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    # Build placeholders for the IN clause; SQLite doesn't support array params.
    placeholders = ",".join("?" for _ in tag_names)
    rows = await (
        await db.execute(
            f"""
            SELECT tag_name, commit_sha
            FROM source_release_history
            WHERE tracker_source_id = ? AND tag_name IN ({placeholders})
            """,
            (tracker_source_id, *tag_names),
        )
    ).fetchall()

    return {row["tag_name"]: row["commit_sha"] for row in rows}


async def upsert_tracker_release_history(
    storage: "SQLiteStorage",
    aggregate_tracker_id: int,
    release: Release,
    *,
    primary_source_release_history_id: int,
    supporting_source_release_history_ids: list[int] | None = None,
    source_type: str | None = None,
) -> tuple[int, bool]:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row

    identity_key = storage.release_identity_key_for_source(release, source_type=source_type)
    digest = storage._release_digest_value(release, source_type=source_type)
    version, _, _ = storage._release_version_metadata(release, source_type=source_type)
    timestamp = datetime.now().isoformat()

    cursor = await db.execute(
        """
        INSERT OR IGNORE INTO tracker_release_history
        (aggregate_tracker_id, identity_key, immutable_key, version, digest, digest_algorithm, digest_media_type, digest_platform, primary_source_release_history_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            aggregate_tracker_id,
            identity_key,
            identity_key,
            version,
            digest,
            "sha256" if digest else None,
            None,
            None,
            primary_source_release_history_id,
            timestamp,
        ),
    )
    is_new = cursor.rowcount > 0

    row = await (
        await db.execute(
            """
            SELECT id
            FROM tracker_release_history
            WHERE aggregate_tracker_id = ? AND immutable_key = ?
            """,
            (aggregate_tracker_id, identity_key),
        )
    ).fetchone()
    if row is None:
        raise ValueError("Failed to read tracker release history row")
    tracker_release_history_id = row["id"]

    await db.execute(
        """
        UPDATE tracker_release_history
        SET version = ?,
            digest = ?,
            digest_algorithm = ?,
            digest_media_type = ?,
            digest_platform = ?,
            primary_source_release_history_id = ?
        WHERE id = ?
        """,
        (
            version,
            digest,
            "sha256" if digest else None,
            None,
            None,
            primary_source_release_history_id,
            tracker_release_history_id,
        ),
    )

    await db.execute(
        """
        UPDATE tracker_release_history_sources
        SET contribution_kind = 'supporting'
        WHERE tracker_release_history_id = ?
          AND contribution_kind = 'primary'
        """,
        (tracker_release_history_id,),
    )

    await db.execute(
        """
        INSERT OR IGNORE INTO tracker_release_history_sources
        (tracker_release_history_id, source_release_history_id, contribution_kind, created_at)
        VALUES (?, ?, 'primary', ?)
        """,
        (tracker_release_history_id, primary_source_release_history_id, timestamp),
    )
    await db.execute(
        """
        UPDATE tracker_release_history_sources
        SET contribution_kind = 'primary'
        WHERE tracker_release_history_id = ?
          AND source_release_history_id = ?
        """,
        (tracker_release_history_id, primary_source_release_history_id),
    )

    for source_release_history_id in supporting_source_release_history_ids or []:
        if source_release_history_id == primary_source_release_history_id:
            continue
        await db.execute(
            """
            INSERT OR IGNORE INTO tracker_release_history_sources
            (tracker_release_history_id, source_release_history_id, contribution_kind, created_at)
            VALUES (?, ?, 'supporting', ?)
            """,
            (tracker_release_history_id, source_release_history_id, timestamp),
        )

    await db.commit()
    return tracker_release_history_id, is_new


async def get_tracker_release_history_releases(
    storage: "SQLiteStorage",
    aggregate_tracker_id: int,
) -> list[Release]:
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
                   srh.source_type,
                   srh.name,
                   srh.tag_name,
                   srh.version,
                   srh.published_at,
                   srh.url,
                   srh.changelog_url,
                   srh.prerelease,
                   srh.body,
                   srh.commit_sha,
                   srh.raw_payload
            FROM tracker_release_history trh
            JOIN source_release_history srh ON srh.id = trh.primary_source_release_history_id
            WHERE trh.aggregate_tracker_id = ?
            ORDER BY trh.created_at DESC, trh.id DESC
            """,
            (aggregate_tracker_id,),
        )
    ).fetchall()

    releases: list[Release] = []
    for row in rows:
        raw_payload = storage._load_json(row["raw_payload"])
        releases.append(
            Release(
                id=row["tracker_release_history_id"],
                tracker_name="",
                tracker_type=row["source_type"],
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
                created_at=datetime.fromisoformat(row["tracker_created_at"]),
            )
        )

    return releases
