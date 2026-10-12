"""Upgrade populated legacy tables without cascading away history or aliases."""

import json
import shutil
import sqlite3
import subprocess
import os
from pathlib import Path
import sys
import zipfile
import pytest

from tests.db_helpers import dbmate_migrations_dir, iter_dbmate_up_sql
from tests.test_container_published_at_stability import _fresh_container_tracker, _append
from tests.test_release_selection_semantics import release

from tests.test_executor_snapshots_history import _create_executor


def _migrate_time_semantics(db_path, up_sql):
    dbmate = shutil.which("dbmate")
    if dbmate:
        result = subprocess.run(
            [
                dbmate,
                "--url",
                f"sqlite:{db_path}",
                "--migrations-dir",
                str(dbmate_migrations_dir()),
                "--no-dump-schema",
                "migrate",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout + result.stderr
    try:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(up_sql)
    except sqlite3.Error as exc:
        return 1, str(exc)
    return 0, ""


def _make_migrate(db_path, tmp_path, monkeypatch):
    dbmate = shutil.which("dbmate")
    if not dbmate:
        pytest.skip("Real dbmate required to test the Makefile migration entrypoint")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Avoid uv syncing the active pytest environment; still run the real shell,
    # CLI backup, dbmate binary and migration SQL.
    for name, content in {
        "uv": '#!/bin/sh\nshift 2\nexec "$@"\n',
        "python": '#!/bin/sh\nexec "$REAL_PYTHON" "$@"\n',
    }.items():
        executable = bin_dir / name
        executable.write_text(content)
        executable.chmod(0o755)
    monkeypatch.setenv("REAL_PYTHON", sys.executable)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("RELEASETRACKER_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RELEASETRACKER_PRE_MIGRATION_BACKUP", raising=False)
    monkeypatch.setenv("DBMATE_NO_DUMP_SCHEMA", "1")
    monkeypatch.setenv("DBMATE_SCHEMA_FILE", str(tmp_path / "schema.sql"))
    result = subprocess.run(
        ["make", "dbmate-migrate", f"UV={bin_dir / 'uv'}"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout + result.stderr


@pytest.mark.parametrize(
    "damage", ["clean", "known", "set_null", "unknown_fk", "task_relation", "invalid_json"]
)
@pytest.mark.parametrize("driver", ["dbmate", "make"])
@pytest.mark.asyncio
async def test_populated_time_migration_preserves_identities_and_audit(
    storage,
    tmp_path,
    damage,
    driver,
    monkeypatch,
    system_key_manager,
):
    tracker = await _fresh_container_tracker(storage, "isolated-migration-nginx")
    executor_id = await _create_executor(storage, name="isolated-migration-executor")
    items = [
        release("1.2.3", digest="sha256:" + "a" * 64),
        release("1.2.2", digest="sha256:" + "b" * 64),
    ]
    for item in items:
        item.tracker_name = tracker.name
        await _append(storage, tracker, item)
    await storage.save_source_observations(tracker.id, tracker.sources[0], items)
    history = await storage.get_source_release_history_releases_by_source(tracker.sources[0].id)
    for item in history:
        await storage.upsert_tracker_release_history(
            tracker.id, item, primary_source_release_history_id=item.id, source_type="container"
        )
    await storage.refresh_tracker_current_releases(tracker.id, history, source_type="container")

    # Build the actual pre-upgrade schema, then copy only this test's synthetic data.
    migrations = iter_dbmate_up_sql(dbmate_migrations_dir())
    legacy_path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(legacy_path)
    legacy.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)")
    for path, sql in migrations:
        if path.name.startswith("20261012000001_"):
            break
        legacy.executescript(sql)
        legacy.execute("INSERT INTO schema_migrations VALUES (?)", (path.name.split("_", 1)[0],))
        legacy.commit()
    legacy.execute("PRAGMA foreign_keys=OFF")
    with sqlite3.connect(storage.db_path) as source:
        tables = [
            r[0]
            for r in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations'"
            )
        ]
        tables = [
            table
            for table in tables
            if legacy.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
        ]
        for table in tables:
            if not legacy.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                continue
            columns = [r[1] for r in source.execute(f'PRAGMA table_info("{table}")')]
            for row in source.execute(f'SELECT * FROM "{table}"'):
                if (
                    table == "settings"
                    and row[columns.index("key")] == "internal.release_selection_rebuild_pending"
                ):
                    continue
                values = [
                    (
                        "2026-01-01T00:00:00+00:00"
                        if name == "published_at" and value is None
                        else value
                    )
                    for name, value in zip(columns, row)
                ]
                legacy.execute(
                    f'INSERT OR REPLACE INTO "{table}" VALUES ({",".join("?" for _ in values)})',
                    values,
                )
    for table in ("source_release_history", "source_release_observations"):
        legacy.execute(
            f"""UPDATE {table} SET raw_payload=json_set(raw_payload,
            '$.published_at_source', CASE WHEN version='1.2.3' THEN 'artifact_created' ELSE 'first_observed' END)"""
        )
    if damage != "clean":
        legacy.execute(
            "UPDATE source_release_aliases SET source_release_history_id=999999 WHERE id=(SELECT MIN(id) FROM source_release_aliases)"
        )
    if damage == "set_null":
        legacy.execute(
            "UPDATE canonical_releases SET primary_observation_id=999999 WHERE id=(SELECT MIN(id) FROM canonical_releases)"
        )
    if damage == "unknown_fk":
        legacy.execute(
            "CREATE TABLE unknown_table(id INTEGER PRIMARY KEY, tracker_id INTEGER REFERENCES aggregate_trackers(id) ON DELETE CASCADE)"
        )
        legacy.execute("INSERT INTO unknown_table VALUES (1,999999)")
    task_state = "succeeded" if damage == "task_relation" else "running"
    legacy.execute(
        "INSERT INTO tasks(id,kind,resource_key,dedupe_key,target_label,payload,state,max_retries,due_at,created_at,updated_at) VALUES (1,'deploy','fixture','fixture','nginx','{}',?,0,0,0,0)",
        (task_state,),
    )
    legacy.execute(
        "INSERT INTO executor_run_history(id,executor_id,started_at,status,created_at) VALUES (1,?,'2026-01-01','running','2026-01-01')",
        (executor_id,),
    )
    legacy.execute(
        "INSERT INTO deployment_observations(task_id,run_id,executor_id,resource_scope,executor_config,verification,finalization,started_at,deadline,due_at) VALUES (1,1,?,'{}','{}','{}','{}',0,60,0)",
        (executor_id,),
    )
    if damage == "invalid_json":
        # Failure after cleanup and table rebuild must restore both rows and schema.
        legacy.execute(
            "UPDATE source_release_history SET raw_payload='broken-json' WHERE id=(SELECT MIN(id) FROM source_release_history)"
        )
    legacy.commit()
    counts = {
        table: legacy.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables
    }
    assert all(
        counts[table] == 2
        for table in (
            "canonical_releases",
            "source_release_history",
            "source_release_observations",
            "tracker_current_releases",
        )
    )
    identities = legacy.execute(
        "SELECT id,tracker_source_id,immutable_key FROM source_release_history ORDER BY id"
    ).fetchall()
    assert bool(legacy.execute("PRAGMA foreign_key_check").fetchall()) == (damage != "clean")
    before_migration = list(legacy.iterdump())
    legacy.close()

    keys = system_key_manager.secrets_path.read_bytes()
    (tmp_path / "system-secrets.json").write_bytes(keys)
    if driver == "make":
        code, message = _make_migrate(legacy_path, tmp_path, monkeypatch)
        archives = list((tmp_path / "backups").glob("*.zip"))
        assert len(archives) == 1
        with zipfile.ZipFile(archives[0]) as archive:
            assert archive.read("system-secrets.json") == keys
            assert json.loads(archive.read("manifest.json"))["pruned_orphans"] == {}
            snapshot = tmp_path / "pre-migration.db"
            snapshot.write_bytes(archive.read("releases.db"))
        with sqlite3.connect(snapshot) as conn:
            assert list(conn.iterdump()) == before_migration
    else:
        code, message = _migrate_time_semantics(
            legacy_path,
            next(sql for path, sql in migrations if path.name.startswith("20261012000001_")),
        )
    if damage in {"unknown_fk", "task_relation", "invalid_json"}:
        expected = {
            "unknown_fk": "release_time_unrepairable_foreign_keys_run_audit_database",
            "task_relation": "release_time_invalid_deployment_observation_relations",
            "invalid_json": "malformed JSON",
        }[damage]
        assert code != 0
        assert expected in message
        with sqlite3.connect(legacy_path) as conn:
            assert list(conn.iterdump()) == before_migration
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version='20261012000001'"
                ).fetchone()[0]
                == 0
            )
        return
    assert code == 0, message
    if damage in {"known", "set_null"}:
        counts["source_release_aliases"] -= 1
        counts["source_release_alias_run_observations"] -= 2
        counts["settings"] += 1  # Audit of migration-owned orphan cleanup.
    with sqlite3.connect(legacy_path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        counts["settings"] += 1  # One-time local projection rebuild marker.
        audit = conn.execute(
            "SELECT value FROM settings WHERE key='internal.release_time_pruned_orphans'"
        ).fetchone()
        expected_cleanup = (
            {"source_release_aliases": 1, "source_release_alias_run_observations": 2}
            if damage != "clean"
            else {}
        )
        if damage == "set_null":
            expected_cleanup["canonical_releases"] = 1
            assert (
                conn.execute(
                    "SELECT primary_observation_id FROM canonical_releases ORDER BY id LIMIT 1"
                ).fetchone()[0]
                is None
            )
        assert (json.loads(audit[0]) if audit else {}) == expected_cleanup
        assert conn.execute("SELECT state FROM tasks WHERE id=1").fetchone()[0] == "running"
        assert (
            conn.execute("SELECT state FROM deployment_observations WHERE task_id=1").fetchone()[0]
            == "waiting"
        )
        assert counts == {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables
        }
        assert (
            identities
            == conn.execute(
                "SELECT id,tracker_source_id,immutable_key FROM source_release_history ORDER BY id"
            ).fetchall()
        )
        rows = conn.execute(
            "SELECT version,published_at,raw_payload FROM source_release_history"
        ).fetchall()
        for version, published, raw in rows:
            payload = json.loads(raw)
            assert published is None
            assert payload["legacy_published_at"] == "2026-01-01T00:00:00+00:00"
            assert payload["artifact_created_at"] == (
                "2026-01-01T00:00:00+00:00" if version == "1.2.3" else None
            )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM tracker_current_releases WHERE published_at IS NOT NULL"
            ).fetchone()[0]
            == 0
        )


def test_make_migration_stops_if_safety_backup_cannot_be_created(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE marker(value TEXT)")
        db.execute("INSERT INTO marker VALUES ('must remain untouched')")
        db.commit()
        before = list(db.iterdump())
    # No key file: the shared entrypoint must stop before running dbmate.
    code, message = _make_migrate(db_path, tmp_path, monkeypatch)
    assert code != 0
    assert "system-secrets.json is missing; refusing to migrate" in message
    with sqlite3.connect(db_path) as db:
        assert list(db.iterdump()) == before
    assert not list((tmp_path / "backups").glob("*.zip"))
