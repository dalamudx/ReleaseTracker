"""Scheduled work resumes from persisted history across restarts and edits."""

import json
import os
import sqlite3
from datetime import datetime, timedelta

import pytest

from releasetracker import cli
from releasetracker.models import TrackerStatus
from releasetracker.scheduler import ReleaseScheduler
from releasetracker.scheduler_host import SchedulerHost
from releasetracker.services import instance_backup as backup


@pytest.mark.asyncio
async def test_tracker_schedule_resumes_from_last_check(storage):
    scheduler = ReleaseScheduler(storage, scheduler_host=SchedulerHost())
    interval = 6 * 3600
    soon = await scheduler._next_tracker_check("never-checked", interval)
    assert timedelta(seconds=4) < soon - datetime.now() < timedelta(seconds=121)

    last = datetime.now() - timedelta(hours=2)
    await storage.update_tracker_status(
        TrackerStatus(name="recent", type="github", enabled=True, last_check=last)
    )
    resumed = await scheduler._next_tracker_check("recent", interval)
    assert abs((resumed - (last + timedelta(seconds=interval))).total_seconds()) < 1

    await storage.update_tracker_status(
        TrackerStatus(
            name="overdue",
            type="github",
            enabled=True,
            last_check=datetime.now() - timedelta(hours=7),
        )
    )
    overdue = await scheduler._next_tracker_check("overdue", interval)
    assert overdue - datetime.now() < timedelta(seconds=121)


@pytest.mark.asyncio
async def test_interval_job_uses_next_run_time_and_jitter():
    host = SchedulerHost()
    due = datetime.now() + timedelta(hours=3)
    host.add_interval_job(
        "tracker", "app", lambda: None, seconds=21600, next_run_time=due, jitter=300
    )
    await host.start()
    try:
        job = host.get_job("tracker", "app")
        assert abs((job.next_run_time.replace(tzinfo=None) - due).total_seconds()) < 1
        assert job.trigger.jitter == 300
    finally:
        await host.shutdown()


def migration_db(path, applied):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)")
        db.executemany("INSERT INTO schema_migrations VALUES (?)", [(v,) for v in applied])
        db.execute("CREATE TABLE sample(id INTEGER)")


def write_keys(path):
    from cryptography.fernet import Fernet

    path.write_text(
        json.dumps({"jwt_secret": "x" * 64, "encryption_key": Fernet.generate_key().decode()})
    )


def test_pre_migration_backup_only_when_pending(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for version in ("20260101000001", "20260102000001"):
        (migrations / f"{version}_step.sql").write_text("-- migrate:up\n")
    db = tmp_path / "releases.db"
    keys = tmp_path / "system-secrets.json"
    write_keys(keys)
    out = tmp_path / "backups"

    assert backup.pre_migration_backup(tmp_path / "missing.db", keys, out, migrations) is None
    migration_db(db, ["20260101000001", "20260102000001"])
    assert backup.pre_migration_backup(db, keys, out, migrations) is None

    (migrations / "20260103000001_new.sql").write_text("-- migrate:up\n")
    archive = backup.pre_migration_backup(db, keys, out, migrations)
    with backup.zipfile.ZipFile(archive) as zipped:
        manifest = json.loads(zipped.read("manifest.json"))
    assert manifest["reason"] == "pre_migration"
    assert manifest["migrations"] == ["20260101000001", "20260102000001"]
    (tmp_path / "check").mkdir()
    # A pre-upgrade restore point must be restored with the previous image.
    with pytest.raises(ValueError, match="matching image"):
        backup.validate_archive(archive, tmp_path / "check")

    keys.unlink()
    with pytest.raises(ValueError, match="missing"):
        backup.pre_migration_backup(db, keys, out, migrations)


def test_pre_migration_cli_honors_opt_out_and_fails_closed(tmp_path, monkeypatch):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "20260101000001_step.sql").write_text("-- migrate:up\n")
    db = tmp_path / "releases.db"
    migration_db(db, [])
    monkeypatch.setenv("RELEASETRACKER_DB_PATH", str(db))
    monkeypatch.setenv("DBMATE_MIGRATIONS_DIR", str(migrations))
    monkeypatch.setenv("RELEASETRACKER_BACKUP_DIR", str(tmp_path / "out"))
    with pytest.raises(SystemExit) as error:
        cli.main(["pre-migration-backup"])  # keys missing: refuse to migrate
    assert error.value.code == 1
    monkeypatch.setenv("RELEASETRACKER_PRE_MIGRATION_BACKUP", "0")
    assert cli.main(["pre-migration-backup"]) == 0
    monkeypatch.delenv("RELEASETRACKER_PRE_MIGRATION_BACKUP")
    write_keys(tmp_path / "system-secrets.json")
    assert cli.main(["pre-migration-backup"]) == 0
    assert len(list((tmp_path / "out").glob("releasetracker-*.zip"))) == 1


@pytest.mark.asyncio
async def test_backup_schedule_resumes_from_newest_archive(storage, system_key_manager, tmp_path):
    service = backup.InstanceBackup(storage, system_key_manager, directory=tmp_path)
    assert service.latest_archive_time() is None
    archive = await service.create()
    created = backup.archive_created_at(archive)
    old = archive.stat().st_mtime - 3600
    os.utime(archive, (old, old))  # File copying/touching must not reset creation history.
    assert (
        backup.InstanceBackup(storage, system_key_manager, directory=tmp_path).last_success
        == created
    )
