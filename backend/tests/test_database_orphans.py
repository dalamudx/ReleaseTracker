"""Historical repairs preserve valid data and never hide unrelated relation corruption."""

from contextlib import closing
from datetime import datetime, timezone
import sqlite3
import asyncio
from pathlib import Path

import pytest

from releasetracker.models import AggregateTracker, TrackerSource, Release, ExecutorSnapshot
from releasetracker.services.database_orphans import prune_orphans
from releasetracker.services.instance_backup import InstanceBackup, validate_archive
from releasetracker.services.database_integrity import validate_relations


async def history(storage, name):
    tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name=name,
            primary_changelog_source_key="upstream",
            sources=[
                TrackerSource(
                    source_key="upstream", source_type="github", source_config={"repo": "owner/app"}
                )
            ],
        )
    )
    source = tracker.sources[0]
    release = Release(
        tracker_name=name,
        tracker_type="github",
        version="1.0.0",
        name="1.0.0",
        tag_name="v1.0.0",
        commit_sha="a" * 40,
        channel_name="stable",
        published_at=datetime.now(timezone.utc),
        url="https://github.com/owner/app/releases/1",
    )
    run_id = await storage.create_source_fetch_run(source.id, trigger_mode="manual")
    await storage.append_source_history_for_run(
        run_id, source, [release], aggregate_tracker_id=tracker.id
    )
    identity = storage.release_identity_key_for_source(release, source_type="github")
    history_id = await storage.get_source_release_history_id(source.id, identity)
    await storage.upsert_tracker_release_history(
        tracker.id, release, primary_source_release_history_id=history_id, source_type="github"
    )
    await storage.save_source_observations(tracker.id, source, [release])
    return tracker


async def legacy_orphans(storage):
    tracker = await history(storage, "deleted")
    with closing(sqlite3.connect(storage.db_path)) as db:
        db.execute(
            "DELETE FROM aggregate_tracker_sources WHERE aggregate_tracker_id=?", (tracker.id,)
        )
        db.execute("DELETE FROM aggregate_trackers WHERE id=?", (tracker.id,))
        db.commit()
    return tracker


async def test_delete_tracker_cascades_history_with_foreign_keys_disabled(storage):
    deleted = await history(storage, "deleted")
    retained = await history(storage, "retained")
    await storage.delete_aggregate_tracker(deleted.name)
    with closing(sqlite3.connect(storage.db_path)) as db:
        validate_relations(db)
        assert db.execute("SELECT COUNT(*) FROM source_release_history").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT COUNT(*) FROM tracker_release_history WHERE aggregate_tracker_id=?",
                (retained.id,),
            ).fetchone()[0]
            == 1
        )


async def test_delete_executor_cascades_snapshots(storage):
    from test_executor_snapshots_history import _create_executor

    executor_id = await _create_executor(storage)
    await storage.create_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={"image": "app:1"},
            trigger="pre_update",
            image_at_capture="app:1",
        )
    )
    assert await storage.delete_executor_config(executor_id)
    with closing(sqlite3.connect(storage.db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM executor_snapshots").fetchone()[0] == 0
        validate_relations(db)


async def test_prune_is_idempotent_and_keeps_valid_history(storage):
    await legacy_orphans(storage)
    await history(storage, "retained")
    with closing(sqlite3.connect(storage.db_path)) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall()
        counts = prune_orphans(db)
        assert counts["source_release_history"] == 1
        assert counts["tracker_release_history"] == 1
        validate_relations(db)
        assert prune_orphans(db) == {}
        assert db.execute("SELECT COUNT(*) FROM source_release_history").fetchone()[0] == 1
        db.commit()


async def test_backup_repairs_copy_without_mutating_live_history(
    storage, system_key_manager, tmp_path
):
    await legacy_orphans(storage)
    backup = InstanceBackup(storage, system_key_manager, tmp_path / "backups")
    archive = await backup.create()
    (tmp_path / "inspected").mkdir()
    manifest = await asyncio.to_thread(validate_archive, archive, tmp_path / "inspected")
    assert manifest["pruned_orphans"]["source_release_history"] == 1
    with closing(sqlite3.connect(storage.db_path)) as live:
        assert live.execute("PRAGMA foreign_key_check").fetchall()
        assert live.execute("SELECT COUNT(*) FROM source_release_history").fetchone()[0] == 1
    with closing(sqlite3.connect(tmp_path / "inspected" / "releases.db")) as restored:
        validate_relations(restored)


async def test_unknown_relations_are_not_silently_pruned(storage):
    with closing(sqlite3.connect(storage.db_path)) as db:
        db.execute(
            "CREATE TABLE unknown_table (id INTEGER PRIMARY KEY, tracker_id INTEGER REFERENCES aggregate_trackers(id) ON DELETE CASCADE)"
        )
        db.execute("INSERT INTO unknown_table VALUES(1,99999)")
        assert prune_orphans(db) == {}
        with pytest.raises(ValueError, match="foreign key"):
            validate_relations(db)
        assert db.execute("SELECT COUNT(*) FROM unknown_table").fetchone()[0] == 1


async def test_cli_dry_run_and_backed_up_cleanup(
    storage, system_key_manager, tmp_path, monkeypatch, capsys
):
    from releasetracker import cli

    await legacy_orphans(storage)
    monkeypatch.setattr(cli, "database_path", lambda: Path(storage.db_path).resolve())
    monkeypatch.setattr(cli, "system_secrets_path", lambda: system_key_manager.secrets_path)
    assert cli.main(["prune-orphans", "--dry-run"]) == 0
    assert '"dry_run": true' in capsys.readouterr().out
    with closing(sqlite3.connect(storage.db_path)) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall()
    with pytest.raises(SystemExit):
        cli.main(["prune-orphans"])
    assert cli.main(["prune-orphans", "--confirm-stopped"]) == 0
    assert list((Path(storage.db_path).parent / "backups").glob("*.zip"))
    with closing(sqlite3.connect(storage.db_path)) as db:
        validate_relations(db)


async def test_tracker_cascade_rolls_back_when_parent_delete_fails(storage):
    tracker = await history(storage, "delete-blocked")
    db = await storage._get_connection()
    await db.execute(
        "CREATE TRIGGER reject_tracker_delete BEFORE DELETE ON aggregate_trackers BEGIN SELECT RAISE(ABORT, 'delete blocked'); END"
    )
    await db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="delete blocked"):
        await storage.delete_aggregate_tracker(tracker.name)
    cursor = await db.execute("SELECT COUNT(*) FROM source_release_history")
    assert (await cursor.fetchone())[0] == 1
    cursor = await db.execute("SELECT COUNT(*) FROM aggregate_trackers WHERE id=?", (tracker.id,))
    assert (await cursor.fetchone())[0] == 1


async def test_executor_cascade_rolls_back_when_child_delete_fails(storage):
    from test_executor_snapshots_history import _create_executor

    executor_id = await _create_executor(storage)
    await storage.create_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data={"image": "app:1"},
            trigger="pre_update",
            image_at_capture="app:1",
        )
    )
    db = await storage._get_connection()
    await db.execute(
        "CREATE TRIGGER reject_snapshot_delete BEFORE DELETE ON executor_snapshots BEGIN SELECT RAISE(ABORT, 'delete blocked'); END"
    )
    await db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="delete blocked"):
        await storage.delete_executor_config(executor_id)
    assert await storage.get_executor_config(executor_id) is not None
    assert len(await storage.list_executor_snapshots(executor_id)) == 1
