import sqlite3
import importlib.util
import sys
import json
from datetime import datetime
from pathlib import Path

import pytest
import aiosqlite
from db_helpers import initialize_storage_with_schema, dbmate_migrations_dir

sqlite_path = (
    Path(__file__).resolve().parents[1] / "src" / "releasetracker" / "storage" / "sqlite.py"
)
spec = importlib.util.spec_from_file_location("releasetracker.storage.sqlite", sqlite_path)
assert spec is not None
assert spec.loader is not None
sqlite_module = importlib.util.module_from_spec(spec)
sys.modules["releasetracker.storage.sqlite"] = sqlite_module
spec.loader.exec_module(sqlite_module)

SQLiteStorage = sqlite_module.SQLiteStorage
Release = sqlite_module.Release
AggregateTracker = sqlite_module.AggregateTracker
TrackerSource = sqlite_module.TrackerSource


async def _create_test_storage(db_path: Path):
    from releasetracker.services.system_keys import SystemKeyManager

    key_manager = SystemKeyManager(db_path.parent / f"{db_path.stem}-system-secrets.json")
    await key_manager.initialize()
    return SQLiteStorage(str(db_path), system_key_manager=key_manager)


def test_executor_image_selection_mode_migration_updates_existing_executor_table(
    tmp_path
):
    db_path = tmp_path / "releases.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                config TEXT NOT NULL DEFAULT '{}',
                secrets TEXT,
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS executors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                runtime_type TEXT NOT NULL,
                runtime_connection_id INTEGER NOT NULL,
                tracker_name TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                update_mode TEXT NOT NULL,
                target_ref TEXT NOT NULL DEFAULT '{}',
                maintenance_window TEXT,
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (runtime_connection_id) REFERENCES runtime_connections(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()

        cursor = conn.execute("PRAGMA table_info(executors)")
        columns_before = [row[1] for row in cursor.fetchall()]
        assert "image_selection_mode" not in columns_before
    finally:
        conn.close()


def test_executor_snapshots_migration_updates_existing_executor_schema(tmp_path):
    db_path = tmp_path / "releases.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                config TEXT NOT NULL DEFAULT '{}',
                secrets TEXT,
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS executors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                runtime_type TEXT NOT NULL,
                runtime_connection_id INTEGER NOT NULL,
                tracker_name TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                update_mode TEXT NOT NULL,
                target_ref TEXT NOT NULL DEFAULT '{}',
                maintenance_window TEXT,
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (runtime_connection_id) REFERENCES runtime_connections(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS executor_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                executor_id INTEGER NOT NULL UNIQUE,
                last_run_at TEXT,
                last_result TEXT,
                last_error TEXT,
                last_version TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (executor_id) REFERENCES executors(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS executor_run_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                executor_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                from_version TEXT,
                to_version TEXT,
                message TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (executor_id) REFERENCES executors(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()

        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='executor_snapshots'"
        )
        assert cursor.fetchone() is None
    finally:
        conn.close()



@pytest.mark.asyncio
async def test_get_stats_ignores_canonical_only_rows_without_redesign_truth_projection(tmp_path):
    db_path = tmp_path / "canonical-only-stats.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        observed_at = "2026-04-22T00:00:00+00:00"

        conn = sqlite3.connect(db_path)
        try:
            tracker_cursor = conn.execute(
                "INSERT INTO aggregate_trackers (name, enabled, changelog_policy, primary_changelog_source_id, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "canonical-only-stats",
                    1,
                    "primary_source",
                    None,
                    None,
                    observed_at,
                    observed_at,
                ),
            )
            aggregate_tracker_id = tracker_cursor.lastrowid
            source_cursor = conn.execute(
                "INSERT INTO aggregate_tracker_sources (aggregate_tracker_id, source_key, source_type, enabled, credential_name, source_config, source_rank, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    aggregate_tracker_id,
                    "repo",
                    "github",
                    1,
                    None,
                    '{"repo": "owner/canonical-only-stats"}',
                    0,
                    observed_at,
                    observed_at,
                ),
            )
            tracker_source_id = source_cursor.lastrowid
            conn.execute(
                "UPDATE aggregate_trackers SET primary_changelog_source_id = ? WHERE id = ?",
                (tracker_source_id, aggregate_tracker_id),
            )
            observation_cursor = conn.execute(
                "INSERT INTO source_release_observations (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tracker_source_id,
                    "v9.9.9",
                    "Canonical-only 9.9.9",
                    "v9.9.9",
                    "9.9.9",
                    observed_at,
                    "https://example.com/releases/v9.9.9",
                    None,
                    0,
                    "canonical-only body",
                    None,
                    '{"source_type":"github"}',
                    observed_at,
                    observed_at,
                    observed_at,
                ),
            )
            observation_id = observation_cursor.lastrowid
            canonical_cursor = conn.execute(
                "INSERT INTO canonical_releases (aggregate_tracker_id, canonical_key, version, name, tag_name, published_at, url, changelog_url, prerelease, body, primary_observation_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    aggregate_tracker_id,
                    "9.9.9",
                    "9.9.9",
                    "Canonical-only 9.9.9",
                    "v9.9.9",
                    observed_at,
                    "https://example.com/releases/v9.9.9",
                    None,
                    0,
                    "canonical-only body",
                    observation_id,
                    observed_at,
                    observed_at,
                ),
            )
            canonical_release_id = canonical_cursor.lastrowid
            conn.execute(
                "INSERT INTO canonical_release_observations (canonical_release_id, source_release_observation_id, contribution_kind, created_at) VALUES (?, ?, ?, ?)",
                (canonical_release_id, observation_id, "primary", observed_at),
            )
            conn.commit()
        finally:
            conn.close()

        stats = await storage.get_stats()

        assert stats.total_releases == 0
        assert stats.recent_releases == 0
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_initialize_preserves_existing_helm_observations_and_canonicals_idempotently(
    tmp_path
):
    db_path = tmp_path / "releases.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        observed_at = "2025-04-03T00:00:00+00:00"

        conn = sqlite3.connect(db_path)
        try:
            tracker_cursor = conn.execute(
                "INSERT INTO aggregate_trackers (name, enabled, changelog_policy, primary_changelog_source_id, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "migration-helm-backfill",
                    1,
                    "primary_source",
                    None,
                    None,
                    observed_at,
                    observed_at,
                ),
            )
            aggregate_tracker_id = tracker_cursor.lastrowid
            source_cursor = conn.execute(
                "INSERT INTO aggregate_tracker_sources (aggregate_tracker_id, source_key, source_type, enabled, credential_name, source_config, source_rank, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    aggregate_tracker_id,
                    "helm",
                    "helm",
                    1,
                    None,
                    '{"repo": "https://charts.example.com", "chart": "demo"}',
                    0,
                    observed_at,
                    observed_at,
                ),
            )
            tracker_source_id = source_cursor.lastrowid
            conn.execute(
                "UPDATE aggregate_trackers SET primary_changelog_source_id = ? WHERE id = ?",
                (tracker_source_id, aggregate_tracker_id),
            )
            observation_cursor = conn.execute(
                "INSERT INTO source_release_observations (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tracker_source_id,
                    "2.3.4-chart.9",
                    "Helm 2.3.4 chart.9",
                    "2.3.4-chart.9",
                    "2.3.4-chart.9",
                    observed_at,
                    "https://charts.example.com/demo-2.3.4-chart.9.tgz",
                    None,
                    0,
                    "helm body",
                    None,
                    '{"source_type": "helm", "appVersion": "2.3.4", "version": "2.3.4-chart.9"}',
                    observed_at,
                    observed_at,
                    observed_at,
                ),
            )
            observation_id = observation_cursor.lastrowid
            canonical_cursor = conn.execute(
                "INSERT INTO canonical_releases (aggregate_tracker_id, canonical_key, version, name, tag_name, published_at, url, changelog_url, prerelease, body, primary_observation_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    aggregate_tracker_id,
                    "2.3.4-chart.9",
                    "2.3.4-chart.9",
                    "Helm 2.3.4 chart.9",
                    "2.3.4-chart.9",
                    observed_at,
                    "https://charts.example.com/demo-2.3.4-chart.9.tgz",
                    None,
                    0,
                    "helm body",
                    observation_id,
                    observed_at,
                    observed_at,
                ),
            )
            canonical_release_id = canonical_cursor.lastrowid
            conn.execute(
                "INSERT INTO canonical_release_observations (canonical_release_id, source_release_observation_id, contribution_kind, created_at) VALUES (?, ?, ?, ?)",
                (canonical_release_id, observation_id, "primary", observed_at),
            )
            conn.commit()
        finally:
            conn.close()

        await storage.initialize()
        await storage.initialize()

        conn = sqlite3.connect(db_path)
        try:
            observation_row = conn.execute(
                "SELECT source_release_key, tag_name, version, raw_payload FROM source_release_observations WHERE tracker_source_id = ?",
                (tracker_source_id,),
            ).fetchone()
            canonical_rows = conn.execute(
                "SELECT id, canonical_key, version, tag_name, primary_observation_id FROM canonical_releases WHERE aggregate_tracker_id = ? ORDER BY canonical_key ASC",
                (aggregate_tracker_id,),
            ).fetchall()
            provenance_rows = conn.execute(
                "SELECT canonical_release_id, source_release_observation_id, contribution_kind FROM canonical_release_observations"
            ).fetchall()
        finally:
            conn.close()

        assert observation_row is not None
        assert observation_row[0] == "2.3.4-chart.9"
        assert observation_row[1] == "2.3.4-chart.9"
        assert observation_row[2] == "2.3.4-chart.9"
        assert observation_row[3] == (
            '{"source_type": "helm", "appVersion": "2.3.4", "version": "2.3.4-chart.9"}'
        )

        assert len(canonical_rows) == 1
        assert canonical_rows[0][1:] == (
            "2.3.4-chart.9",
            "2.3.4-chart.9",
            "2.3.4-chart.9",
            observation_id,
        )
        assert provenance_rows == [(canonical_rows[0][0], observation_id, "primary")]
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_initialize_bootstraps_empty_database(tmp_path):
    db_path = tmp_path / "missing-schema.db"
    storage = await _create_test_storage(db_path)

    await storage.initialize()

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        versions = [
            row[0]
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version ASC"
            ).fetchall()
        ]
    finally:
        conn.close()

    assert "releases" not in tables
    assert "release_history" not in tables
    assert "aggregate_trackers" in tables
    assert "canonical_releases" in tables
    assert len(versions) == len(list(dbmate_migrations_dir().glob("*.sql")))

    await storage.close()


@pytest.mark.asyncio
async def test_initialize_rejects_legacy_only_database_for_reset_friendly_policy(tmp_path):
    db_path = tmp_path / "legacy-only.db"
    storage = await _create_test_storage(db_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS releases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracker_name TEXT NOT NULL,
                name TEXT NOT NULL,
                tag_name TEXT NOT NULL,
                version TEXT NOT NULL,
                published_at TEXT NOT NULL,
                url TEXT NOT NULL,
                prerelease INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(tracker_name, tag_name)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trackers (
                name TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                repo TEXT,
                project TEXT,
                instance TEXT,
                chart TEXT,
                credential_name TEXT,
                channels TEXT DEFAULT '[]',
                interval INTEGER DEFAULT 60,
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO trackers (name, type, enabled, repo, channels, interval, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-tracker",
                "github",
                1,
                "owner/repo",
                "[]",
                60,
                "2026-04-20T00:00:00+00:00",
                "2026-04-20T00:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="Unsupported legacy-only database schema") as exc_info:
        await storage.initialize()

    error_text = str(exc_info.value)
    assert "Automatic legacy-to-canonical startup migration is unsupported" in error_text
    assert "Reset the local/dev database and restart" in error_text

    conn = sqlite3.connect(db_path)
    try:
        aggregate_tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='aggregate_trackers'"
        ).fetchall()
    finally:
        conn.close()

    assert aggregate_tables == []
    await storage.close()


@pytest.mark.asyncio
async def test_initialize_succeeds_after_dbmate_schema_apply(tmp_path):
    db_path = tmp_path / "preapplied.db"
    storage = await _create_test_storage(db_path)

    await initialize_storage_with_schema(storage)

    conn = sqlite3.connect(db_path)
    try:
        versions = [
            row[0]
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version ASC"
            ).fetchall()
        ]
        assert len(versions) == len(list(dbmate_migrations_dir().glob("*.sql")))
    finally:
        conn.close()

    await storage.close()

