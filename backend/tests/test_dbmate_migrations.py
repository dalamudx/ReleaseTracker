from __future__ import annotations

import sqlite3

from db_helpers import (
    apply_dbmate_migrations,
    dbmate_migrations_dir,
    rollback_dbmate_migrations,
)


def test_dbmate_migrations_are_single_release_baseline():
    migration_files = sorted(dbmate_migrations_dir().glob("*.sql"))

    assert [path.name for path in migration_files] == ["20000101000001_initial_schema.sql"]

    content = migration_files[0].read_text(encoding="utf-8")
    assert "-- migrate:up" in content
    assert "-- migrate:down" in content


def test_apply_dbmate_migrations_builds_full_schema(tmp_path):
    migrations_dir = dbmate_migrations_dir()
    db_path = tmp_path / "dbmate.db"

    applied_versions = apply_dbmate_migrations(db_path, migrations_dir)

    assert applied_versions == ["20000101000001"]

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "releases" not in tables
        assert "release_history" not in tables
        assert "aggregate_trackers" in tables
        assert "executor_snapshots" in tables
        assert "executor_service_bindings" in tables
        assert "executor_desired_state" in tables
        assert "schema_migrations" in tables

        executor_run_history_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(executor_run_history)").fetchall()
        }
        assert "diagnostics" in executor_run_history_columns

        tracker_columns = {row[1] for row in conn.execute("PRAGMA table_info(trackers)").fetchall()}
        assert "github_fetch_mode" in tracker_columns
        assert "fetch_timeout" in tracker_columns

        notifier_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(notifiers)").fetchall()
        }
        assert "language" in notifier_columns

        source_history_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(source_release_history)").fetchall()
        }
        tracker_history_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(tracker_release_history)").fetchall()
        }
        current_release_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(tracker_current_releases)").fetchall()
        }
        assert "immutable_key" in source_history_columns
        assert "immutable_key" in tracker_history_columns
        assert "immutable_key" in current_release_columns

        unique_indexes_by_table = {
            "source_release_history": "idx_source_release_history_immutable_key",
            "tracker_release_history": "idx_tracker_release_history_immutable_key",
            "tracker_current_releases": "idx_tracker_current_releases_immutable_key",
        }
        for table_name, index_name in unique_indexes_by_table.items():
            index_rows = conn.execute(f"PRAGMA index_list({table_name})").fetchall()
            matching_rows = [row for row in index_rows if row[1] == index_name]
            assert matching_rows, f"Missing {index_name}"
            assert matching_rows[0][2] == 1, f"{index_name} must be unique"
    finally:
        conn.close()


def test_apply_dbmate_migrations_records_baseline_version(tmp_path):
    db_path = tmp_path / "dbmate-versions.db"

    apply_dbmate_migrations(db_path, dbmate_migrations_dir())

    conn = sqlite3.connect(db_path)
    try:
        versions = [
            row[0]
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version ASC"
            ).fetchall()
        ]
        assert versions == ["20000101000001"]
    finally:
        conn.close()


def test_full_rollback_removes_all_migrated_tables_and_versions(tmp_path):
    db_path = tmp_path / "rollback-full.db"

    apply_dbmate_migrations(db_path, dbmate_migrations_dir())
    rolled_back_versions = rollback_dbmate_migrations(db_path, dbmate_migrations_dir())

    assert rolled_back_versions == ["20000101000001"]

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "trackers" not in tables
        assert "runtime_connections" not in tables
        assert "schema_migrations" in tables

        versions = conn.execute("SELECT version FROM schema_migrations").fetchall()
        assert versions == []
    finally:
        conn.close()


def test_full_rollback_allows_reapply_on_same_database(tmp_path):
    db_path = tmp_path / "rollback-reapply.db"
    migrations_dir = dbmate_migrations_dir()

    first_apply_versions = apply_dbmate_migrations(db_path, migrations_dir)
    rollback_dbmate_migrations(db_path, migrations_dir)
    second_apply_versions = apply_dbmate_migrations(db_path, migrations_dir)

    assert second_apply_versions == first_apply_versions

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "aggregate_trackers" in tables
        assert "executors" in tables

        versions = [
            row[0]
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version ASC"
            ).fetchall()
        ]
        assert versions == first_apply_versions
    finally:
        conn.close()
