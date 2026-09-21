from __future__ import annotations

import json
import sqlite3

from db_helpers import (
    apply_dbmate_migrations,
    dbmate_migrations_dir,
    iter_dbmate_up_sql,
    rollback_dbmate_migrations,
)


def test_dbmate_migrations_are_single_release_baseline():
    migration_files = sorted(dbmate_migrations_dir().glob("*.sql"))

    # The initial baseline plus any feature migrations that ship on top of it.
    assert migration_files, "expected at least one migration file"
    assert migration_files[0].name == "20000101000001_initial_schema.sql"

    for path in migration_files:
        content = path.read_text(encoding="utf-8")
        assert "-- migrate:up" in content, f"missing migrate:up in {path.name}"
        assert "-- migrate:down" in content, f"missing migrate:down in {path.name}"


def test_legacy_executor_health_profiles_migrate_to_readiness(tmp_path):
    migration = next(
        sql
        for path, sql in iter_dbmate_up_sql(dbmate_migrations_dir())
        if path.name == "20260920000004_unify_executor_health_check.sql"
    )
    conn = sqlite3.connect(tmp_path / "health-config.db")
    try:
        conn.execute("CREATE TABLE executors (id INTEGER PRIMARY KEY, health_check TEXT NOT NULL)")
        legacy_disabled = {
            "strategy": "none",
            "use_default_strategy": False,
            "failure_policy": "mark_failed",
            "grace_period_seconds": 0,
            "attempt_timeout_seconds": 0,
            "interval_seconds": 0,
            "probe_window_seconds": 0,
        }
        legacy_enabled = legacy_disabled | {
            "strategy": "runtime_native",
            "attempt_timeout_seconds": 10,
            "interval_seconds": 5,
            "probe_window_seconds": 180,
        }
        current = legacy_enabled | {
            "readiness_enabled": False,
            "readiness_timeout_seconds": 42,
            "notify_result": True,
        }
        conn.executemany(
            "INSERT INTO executors(id, health_check) VALUES (?, ?)",
            [
                (1, json.dumps(legacy_disabled)),
                (2, json.dumps(legacy_enabled)),
                (3, json.dumps(current)),
            ],
        )
        conn.executescript(migration)
        profiles = {
            row[0]: json.loads(row[1])
            for row in conn.execute("SELECT id, health_check FROM executors ORDER BY id")
        }
        assert profiles[1]["readiness_enabled"] is False
        assert profiles[2]["readiness_enabled"] is True
        assert profiles[1]["use_system_readiness_defaults"] is True
        assert profiles[1]["readiness_timeout_seconds"] == 600
        assert profiles[3]["readiness_timeout_seconds"] == 42
        assert profiles[3]["strategy"] == "none"
        assert profiles[3]["notify_result"] is True
    finally:
        conn.close()


def test_apply_dbmate_migrations_builds_full_schema(tmp_path):
    migrations_dir = dbmate_migrations_dir()
    db_path = tmp_path / "dbmate.db"

    applied_versions = apply_dbmate_migrations(db_path, migrations_dir)

    assert applied_versions[0] == "20000101000001"
    assert sorted(applied_versions) == applied_versions, "migrations must apply in order"

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
        assert "ssh_compose_ownership" in tables
        assert "executor_service_bindings" in tables
        assert "executor_desired_state" in tables
        assert "source_release_aliases" in tables
        assert "source_release_alias_run_observations" in tables
        assert "schema_migrations" in tables

        oauth_state_info = conn.execute("PRAGMA table_info(oauth_states)").fetchall()
        oauth_state_columns = {row[1] for row in oauth_state_info}
        assert oauth_state_columns == {
            "state",
            "provider_slug",
            "code_verifier",
            "nonce",
            "flow_type",
            "initiating_admin_user_id",
            "browser_binding_hash",
            "expires_at",
        }
        browser_binding_column = next(
            row for row in oauth_state_info if row[1] == "browser_binding_hash"
        )
        assert browser_binding_column[3] == 1, "browser binding must be NOT NULL"
        assert (
            conn.execute("SELECT value FROM settings WHERE key = 'system.admin_user_id'").fetchone()
            is None
        )

        executor_run_history_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(executor_run_history)").fetchall()
        }
        assert "diagnostics" in executor_run_history_columns
        assert "executor_snapshot_claims" in tables
        claim_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(executor_snapshot_claims)").fetchall()
        }
        assert claim_columns == {
            "snapshot_id",
            "executor_id",
            "executor_run_id",
            "claimed_at",
        }

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
        assert "merged_into_tracker_release_history_id" in tracker_history_columns
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


def test_single_admin_migration_backfills_existing_admin_and_discards_old_states(tmp_path):
    db_path = tmp_path / "upgrade.db"
    migrations = iter_dbmate_up_sql(dbmate_migrations_dir())
    single_admin_index = next(
        index
        for index, (path, _) in enumerate(migrations)
        if path.name.startswith("20260808000001_")
    )
    conn = sqlite3.connect(db_path)
    try:
        for _, up_sql in migrations[:single_admin_index]:
            conn.executescript(up_sql)
        conn.execute("""
            INSERT INTO users (
                username, email, password_hash, status, created_at, oauth_provider, oauth_sub
            )
            VALUES (
                'admin', 'admin@example.com', 'preserved-hash', 'active', '2026-08-08',
                'legacy-provider', 'legacy-subject'
            )
            """)
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
        conn.execute("""
            INSERT INTO oauth_states (state, provider_slug, code_verifier, expires_at)
            VALUES ('old-state', 'provider', 'verifier', '2099-01-01')
            """)
        conn.commit()

        conn.executescript(migrations[single_admin_index][1])

        assert conn.execute(
            "SELECT value FROM settings WHERE key = 'system.admin_user_id'"
        ).fetchone() == (str(admin_id),)
        assert conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (admin_id,)
        ).fetchone() == ("preserved-hash",)
        assert conn.execute(
            "SELECT COUNT(*) FROM settings WHERE key IN (?, ?)",
            ("system.admin_oidc_issuer", "system.admin_oidc_subject"),
        ).fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM oauth_states").fetchone() == (0,)
    finally:
        conn.close()


def test_browser_binding_migration_discards_unbound_oauth_states(tmp_path):
    db_path = tmp_path / "oauth-binding-upgrade.db"
    migrations = iter_dbmate_up_sql(dbmate_migrations_dir())
    migration_index = next(
        index
        for index, (path, _) in enumerate(migrations)
        if path.name.startswith("20260809000001_")
    )
    conn = sqlite3.connect(db_path)
    try:
        for _, up_sql in migrations[:migration_index]:
            conn.executescript(up_sql)
        conn.execute("""
            INSERT INTO oauth_states (
                state, provider_slug, code_verifier, nonce, flow_type, expires_at
            )
            VALUES ('unbound-state', 'provider', 'verifier', 'nonce', 'login', '2099-01-01')
            """)
        conn.commit()

        conn.executescript(migrations[migration_index][1])

        assert conn.execute("SELECT COUNT(*) FROM oauth_states").fetchone() == (0,)
        browser_binding_column = next(
            row
            for row in conn.execute("PRAGMA table_info(oauth_states)").fetchall()
            if row[1] == "browser_binding_hash"
        )
        assert browser_binding_column[3] == 1
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
        assert versions[0] == "20000101000001"
        assert len(versions) >= 1
    finally:
        conn.close()


def test_full_rollback_removes_all_migrated_tables_and_versions(tmp_path):
    db_path = tmp_path / "rollback-full.db"

    applied_versions = apply_dbmate_migrations(db_path, dbmate_migrations_dir())
    rolled_back_versions = rollback_dbmate_migrations(db_path, dbmate_migrations_dir())

    # dbmate rolls back in reverse order.
    assert rolled_back_versions == list(reversed(applied_versions))

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
