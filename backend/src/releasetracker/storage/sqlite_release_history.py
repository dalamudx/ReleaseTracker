"""Source and tracker release-history persistence helpers."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from ..models import Release, ReleaseAliasReference, TrackerSource
from . import sqlite_release_aliases

if TYPE_CHECKING:
    from .sqlite import SQLiteStorage


def _normalize_digest(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized or None


def _digest_algorithm(value: str) -> str | None:
    algorithm, separator, _ = value.partition(":")
    return algorithm if separator and algorithm else None


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


async def reconcile_interrupted_source_fetch_runs(
    storage: "SQLiteStorage", *, finished_at: datetime | None = None
) -> int:
    """Fail runs left active by a previous single-instance process."""
    db = await storage._get_connection()
    finished_at_value = (finished_at or datetime.now()).isoformat()
    cursor = await db.execute(
        """
        UPDATE source_fetch_runs
        SET status = 'failed',
            error_message = 'Source fetch interrupted by application restart',
            finished_at = ?
        WHERE status = 'running'
        """,
        (finished_at_value,),
    )
    await db.commit()
    return max(cursor.rowcount, 0)


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
    observation_time = observed_at or datetime.now()
    timestamp = observation_time.isoformat()
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
                await sqlite_release_aliases.upsert_source_release_alias(
                    storage,
                    db,
                    source_fetch_run_id=source_fetch_run_id,
                    tracker_source=tracker_source,
                    source_release_history_id=int(prior_digest_row["id"]),
                    release=release,
                    observed_at=observation_time,
                )
                continue

        raw_payload: dict[str, Any] = {
            "aggregate_tracker_id": aggregate_tracker_id,
            "source_key": tracker_source.source_key,
            "source_type": tracker_source.source_type,
            "channel_name": release.channel_name,
            "published_at_source": release.published_at_source,
        }
        if app_version is not None:
            raw_payload["appVersion"] = app_version
        if chart_version is not None:
            raw_payload["chartVersion"] = chart_version
        for key, value in {
            "oci_version": release.oci_version,
            "oci_revision": release.oci_revision,
            "oci_source": release.oci_source,
        }.items():
            if value is not None:
                raw_payload[key] = value

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
                SELECT id, version, tag_name, published_at, commit_sha, raw_payload
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
        await sqlite_release_aliases.upsert_source_release_alias(
            storage,
            db,
            source_fetch_run_id=source_fetch_run_id,
            tracker_source=tracker_source,
            source_release_history_id=int(source_history_id),
            release=release,
            observed_at=observation_time,
        )

        normalized_existing_commit = storage._normalize_release_value(source_row["commit_sha"])
        normalized_new_commit = storage._normalize_release_value(release.commit_sha)
        existing_raw_payload = storage._load_json(source_row["raw_payload"])
        for key in ("oci_version", "oci_revision", "oci_source"):
            if key not in raw_payload and existing_raw_payload.get(key) is not None:
                raw_payload[key] = existing_raw_payload[key]
        upgrades_artifact_created = (
            tracker_source.source_type == "container"
            and release.published_at_source == "artifact_created"
            and existing_raw_payload.get("published_at_source") != "artifact_created"
        )
        oci_metadata = {
            key: value
            for key, value in {
                "oci_version": release.oci_version,
                "oci_revision": release.oci_revision,
                "oci_source": release.oci_source,
            }.items()
            if value is not None
        }
        enriches_oci_metadata = any(
            existing_raw_payload.get(key) != value for key, value in oci_metadata.items()
        )
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
                if digest_matches_or_absent and not upgrades_artifact_created:
                    preserved_published_at = source_row["published_at"]
            elif (
                normalized_existing_commit is not None
                and normalized_existing_commit == normalized_new_commit
            ):
                preserved_published_at = source_row["published_at"]

        replaces_display = storage._should_replace_source_history_display(
            source_type=tracker_source.source_type,
            version=version,
            tag_name=tag_name,
            existing_version=source_row["version"],
            existing_tag_name=source_row["tag_name"],
        )
        if replaces_display:
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
        elif upgrades_artifact_created or enriches_oci_metadata:
            if upgrades_artifact_created:
                existing_raw_payload["published_at_source"] = "artifact_created"
            existing_raw_payload.update(oci_metadata)
            await db.execute(
                """
                UPDATE source_release_history
                SET published_at = ?, raw_payload = ?
                WHERE id = ?
                """,
                (
                    (
                        preserved_published_at
                        if upgrades_artifact_created
                        else source_row["published_at"]
                    ),
                    storage._dump_json(existing_raw_payload),
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

    aliases_by_history_id = await sqlite_release_aliases.get_source_release_aliases_by_history_ids(
        storage, [int(row["id"]) for row in rows]
    )
    releases: list[Release] = []
    for row in rows:
        raw_payload = storage._load_json(row["raw_payload"])
        releases.append(
            Release(
                id=int(row["id"]),
                tracker_name="",
                tracker_type=row["source_type"],
                name=row["name"],
                tag_name=row["tag_name"],
                version=row["version"],
                app_version=raw_payload.get("appVersion"),
                chart_version=raw_payload.get("chartVersion"),
                published_at=datetime.fromisoformat(row["published_at"]),
                published_at_source=raw_payload.get("published_at_source"),
                url=row["url"],
                changelog_url=row["changelog_url"],
                prerelease=bool(row["prerelease"]),
                body=row["body"],
                channel_name=raw_payload.get("channel_name"),
                commit_sha=row["commit_sha"],
                artifact_digest=_normalize_digest(row["digest"]),
                oci_version=raw_payload.get("oci_version"),
                oci_revision=raw_payload.get("oci_revision"),
                oci_source=raw_payload.get("oci_source"),
                aliases=[alias["alias"] for alias in aliases_by_history_id.get(int(row["id"]), [])],
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


async def get_correlated_release_candidates(
    storage: "SQLiteStorage", aggregate_tracker_id: int
) -> list[dict[str, Any]]:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    rows = await (
        await db.execute(
            """
            SELECT cr.*, cro.contribution_kind, sro.tracker_source_id,
                   sro.source_release_key AS observation_source_release_key,
                   sro.commit_sha AS observation_commit_sha,
                   sro.raw_payload AS observation_raw_payload,
                   ats.source_type, ats.source_rank,
                   srh.id AS source_release_history_id,
                   srh.digest AS source_digest
            FROM canonical_releases cr
            JOIN canonical_release_observations cro
              ON cro.canonical_release_id = cr.id
            JOIN source_release_observations sro
              ON sro.id = cro.source_release_observation_id
            JOIN aggregate_tracker_sources ats
              ON ats.id = sro.tracker_source_id
            LEFT JOIN source_release_history srh
              ON srh.id = COALESCE(
                    -- Keep outer references in WHERE: older SQLite versions cannot
                    -- resolve them in a correlated subquery's ORDER BY clause.
                    (
                        SELECT candidate.id
                        FROM source_release_history candidate
                        WHERE candidate.tracker_source_id = sro.tracker_source_id
                          AND candidate.digest = sro.commit_sha
                        ORDER BY candidate.id DESC
                        LIMIT 1
                    ),
                    (
                        SELECT candidate.id
                        FROM source_release_history candidate
                        WHERE candidate.tracker_source_id = sro.tracker_source_id
                          AND candidate.source_release_key = sro.source_release_key
                        ORDER BY candidate.id DESC
                        LIMIT 1
                    )
             )
            WHERE cr.aggregate_tracker_id = ?
            ORDER BY cr.id ASC,
                     CASE cro.contribution_kind WHEN 'primary' THEN 0 ELSE 1 END,
                     ats.source_rank ASC,
                     sro.id ASC
            """,
            (aggregate_tracker_id,),
        )
    ).fetchall()
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        canonical_id = int(row["id"])
        candidate = grouped.setdefault(
            canonical_id,
            {
                "canonical_key": row["canonical_key"],
                "release": Release(
                    tracker_name="",
                    tracker_type=row["source_type"],
                    version=row["version"],
                    name=row["name"],
                    tag_name=row["tag_name"],
                    channel_name=storage._load_json(row["observation_raw_payload"]).get(
                        "channel_name"
                    ),
                    url=row["url"],
                    published_at=datetime.fromisoformat(row["published_at"]),
                    prerelease=bool(row["prerelease"]),
                    body=row["body"],
                    commit_sha=row["observation_commit_sha"],
                ),
                "primary_source_history_id": None,
                "source_history_ids": [],
                "tracker_source_ids": [],
                "artifact_digests": [],
            },
        )
        tracker_source_id = int(row["tracker_source_id"])
        if tracker_source_id not in candidate["tracker_source_ids"]:
            candidate["tracker_source_ids"].append(tracker_source_id)
        source_history_id = row["source_release_history_id"]
        if source_history_id is None:
            continue
        source_history_id = int(source_history_id)
        if source_history_id not in candidate["source_history_ids"]:
            candidate["source_history_ids"].append(source_history_id)
        if row["contribution_kind"] == "primary":
            candidate["primary_source_history_id"] = source_history_id
        digest = _normalize_digest(row["source_digest"])
        if digest is not None and digest not in candidate["artifact_digests"]:
            candidate["artifact_digests"].append(digest)
    result: list[dict[str, Any]] = []
    for candidate in grouped.values():
        primary_source_history_id = candidate["primary_source_history_id"]
        source_history_ids = candidate["source_history_ids"]
        if primary_source_history_id is None and source_history_ids:
            primary_source_history_id = source_history_ids[0]
        if primary_source_history_id is None:
            continue
        release = candidate["release"]
        release.tracker_name = str(aggregate_tracker_id)
        digests = candidate.pop("artifact_digests")
        if len(digests) == 1:
            release.artifact_digest = digests[0]
        aliases_by_history_id = (
            await sqlite_release_aliases.get_source_release_aliases_by_history_ids(
                storage, source_history_ids
            )
        )
        release.aliases = sorted(
            {alias["alias"] for aliases in aliases_by_history_id.values() for alias in aliases}
        )
        candidate["primary_source_history_id"] = primary_source_history_id
        result.append(candidate)
    return result


async def merge_tracker_release_history_sources(
    storage: "SQLiteStorage",
    *,
    aggregate_tracker_id: int,
    canonical_tracker_release_history_id: int,
    source_history_ids: list[int],
    artifact_digest: str | None,
) -> None:
    if not source_history_ids:
        return
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    placeholders = ", ".join("?" for _ in source_history_ids)
    async with storage._transaction_lock:
        await db.execute("BEGIN IMMEDIATE")
        try:
            rows = await (
                await db.execute(
                    f"""
                    SELECT DISTINCT tracker_release_history_id
                    FROM tracker_release_history_sources
                    WHERE source_release_history_id IN ({placeholders})
                    """,
                    tuple(source_history_ids),
                )
            ).fetchall()
            for row in rows:
                member_id = int(row["tracker_release_history_id"])
                if member_id == canonical_tracker_release_history_id:
                    continue
                await db.execute(
                    """
                    INSERT OR IGNORE INTO tracker_release_history_sources
                    (tracker_release_history_id, source_release_history_id, contribution_kind, created_at)
                    SELECT ?, source_release_history_id, 'supporting', created_at
                    FROM tracker_release_history_sources
                    WHERE tracker_release_history_id = ?
                    """,
                    (canonical_tracker_release_history_id, member_id),
                )
                await db.execute(
                    """
                    UPDATE tracker_release_history
                    SET merged_into_tracker_release_history_id = ?
                    WHERE id = ? AND aggregate_tracker_id = ?
                    """,
                    (
                        canonical_tracker_release_history_id,
                        member_id,
                        aggregate_tracker_id,
                    ),
                )
            for source_history_id in source_history_ids:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO tracker_release_history_sources
                    (tracker_release_history_id, source_release_history_id, contribution_kind, created_at)
                    VALUES (?, ?, 'supporting', ?)
                    """,
                    (
                        canonical_tracker_release_history_id,
                        source_history_id,
                        datetime.now().isoformat(),
                    ),
                )
            if artifact_digest is not None:
                await db.execute(
                    """
                    UPDATE tracker_release_history
                    SET digest = ?, digest_algorithm = ?
                    WHERE id = ?
                    """,
                    (
                        artifact_digest,
                        _digest_algorithm(artifact_digest),
                        canonical_tracker_release_history_id,
                    ),
                )
            await db.commit()
        except Exception:
            await db.rollback()
            raise


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
              AND trh.merged_into_tracker_release_history_id IS NULL
            ORDER BY trh.created_at DESC, trh.id DESC
            """,
            (aggregate_tracker_id,),
        )
    ).fetchall()

    aliases_by_tracker_history_id: dict[int, list[str]] = {}
    alias_references_by_tracker_history_id: dict[int, list[ReleaseAliasReference]] = {}
    if rows:
        tracker_history_ids = [int(row["tracker_release_history_id"]) for row in rows]
        placeholders = ", ".join("?" for _ in tracker_history_ids)
        alias_rows = await (
            await db.execute(
                f"""
                SELECT trhs.tracker_release_history_id, sra.alias,
                       sra.tracker_source_id, srh.source_type, srh.prerelease,
                       srh.published_at, srh.digest
                FROM tracker_release_history_sources trhs
                JOIN source_release_aliases sra
                  ON sra.source_release_history_id = trhs.source_release_history_id
                JOIN source_release_history srh
                  ON srh.id = sra.source_release_history_id
                WHERE trhs.tracker_release_history_id IN ({placeholders})
                ORDER BY sra.last_observed_at DESC, sra.normalized_alias ASC
                """,
                tuple(tracker_history_ids),
            )
        ).fetchall()
        for alias_row in alias_rows:
            tracker_history_id = int(alias_row["tracker_release_history_id"])
            aliases_by_tracker_history_id.setdefault(tracker_history_id, []).append(
                alias_row["alias"]
            )
            alias_references_by_tracker_history_id.setdefault(tracker_history_id, []).append(
                ReleaseAliasReference(
                    tracker_source_id=int(alias_row["tracker_source_id"]),
                    source_type=alias_row["source_type"],
                    alias=alias_row["alias"],
                    prerelease=bool(alias_row["prerelease"]),
                    published_at=datetime.fromisoformat(alias_row["published_at"]),
                    digest=_normalize_digest(alias_row["digest"]),
                )
            )

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
                artifact_digest=_normalize_digest(row["digest"]),
                aliases=list(
                    dict.fromkeys(
                        aliases_by_tracker_history_id.get(
                            int(row["tracker_release_history_id"]), []
                        )
                    )
                ),
                alias_references=alias_references_by_tracker_history_id.get(
                    int(row["tracker_release_history_id"]), []
                ),
                created_at=datetime.fromisoformat(row["tracker_created_at"]),
            )
        )

    return releases
