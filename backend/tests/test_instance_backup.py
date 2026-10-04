import asyncio
import json
from pathlib import Path
import sqlite3
import threading
import zipfile

import pytest

from releasetracker import cli
from releasetracker.services import instance_backup as backup
from releasetracker.main import app


@pytest.mark.asyncio
async def test_online_snapshot_and_restore(storage, system_key_manager, tmp_path, auth_service):
    from releasetracker.models import LoginRequest
    from releasetracker.services.auth import pwd_context

    await auth_service.ensure_admin_user()
    admin = await storage.get_user_by_username("admin")
    await storage.update_user_password(admin.id, pwd_context.hash("backup-password"))
    await auth_service.login(LoginRequest(username="admin", password="backup-password"))
    await storage.save_oauth_state(
        "restore-state", "provider", "verifier", "nonce", "login", "hash"
    )
    await storage.set_setting("backup-probe", "snapshot")
    service = backup.InstanceBackup(storage, system_key_manager)
    archive = await service.create()
    await storage.set_setting("backup-probe", "changed-after-backup")
    assert archive.stat().st_mode & 0o777 == 0o600
    destination = tmp_path / "restored"
    manifest = await asyncio.to_thread(backup.restore_to_new_directory, archive, destination)
    assert manifest["format"] == 1
    assert (
        destination / "system-secrets.json"
    ).read_bytes() == system_key_manager.secrets_path.read_bytes()
    db = sqlite3.connect(destination / "releases.db")
    try:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM oauth_states").fetchone()[0] == 0
        assert (
            db.execute("SELECT value FROM settings WHERE key='backup-probe'").fetchone()[0]
            == "snapshot"
        )
    finally:
        db.close()
    with pytest.raises(ValueError, match="must not exist"):
        backup.restore_to_new_directory(archive, destination)
    assert await storage.get_setting("backup-probe") == "changed-after-backup"
    assert service.last_success > 0


@pytest.mark.asyncio
async def test_retention_only_after_success(storage, system_key_manager):
    service = backup.InstanceBackup(storage, system_key_manager)
    first = await service.create(retain=1)
    second = await service.create(retain=1)
    assert not first.exists() and second.exists()
    system_key_manager._stage_encryption_key_rotation_locked(
        system_key_manager.generate_encryption_key()
    )
    with pytest.raises(ValueError, match="pending"):
        await service.create(retain=1)
    assert second.exists()


@pytest.mark.asyncio
async def test_cancellation_keeps_rotation_locks_until_snapshot_finishes(
    storage, system_key_manager, monkeypatch
):
    started, finish = threading.Event(), threading.Event()

    def worker(*args):
        started.set()
        assert finish.wait(5)
        return Path("unused")

    monkeypatch.setattr(backup, "_create_verified_archive", worker)
    service = backup.InstanceBackup(storage, system_key_manager)
    task = asyncio.create_task(service.create())
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert system_key_manager.lock.locked()
    assert storage.encryption_rotation_lock.locked()
    task.cancel()
    await asyncio.sleep(0)
    assert storage.encryption_rotation_lock.locked()
    with pytest.raises(ValueError, match="already running"):
        await service.create()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not system_key_manager.lock.locked()


@pytest.mark.asyncio
async def test_disk_failure_preserves_last_backup(storage, system_key_manager, monkeypatch):
    service = backup.InstanceBackup(storage, system_key_manager)
    first = await service.create(retain=1)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(backup, "_create_archive", fail)
    with pytest.raises(OSError):
        await service.create(retain=1)
    assert first.exists()
    assert service.failures == 1


@pytest.mark.asyncio
async def test_valid_checksum_but_wrong_key_is_rejected(storage, system_key_manager, tmp_path):
    import hashlib

    db = await storage._get_connection()
    encrypted = storage.fernet.encrypt(b"credential-secret").decode()
    await db.execute(
        "INSERT INTO credentials(name,type,token,created_at,updated_at) VALUES ('test','github',?,'2026-01-01','2026-01-01')",
        (encrypted,),
    )
    await db.commit()
    archive = await backup.InstanceBackup(storage, system_key_manager).create()
    with zipfile.ZipFile(archive) as source:
        payload = json.loads(source.read("system-secrets.json"))
    payload["encryption_key"] = system_key_manager.generate_encryption_key()
    wrong = json.dumps(payload).encode()

    def change(name, data):
        if name == "system-secrets.json":
            return wrong
        if name == "manifest.json":
            value = json.loads(data)
            value["sha256"]["system-secrets.json"] = hashlib.sha256(wrong).hexdigest()
            return json.dumps(value)
        return data

    bad = tmp_path / "wrong-key.zip"
    rewrite_archive(archive, bad, change)
    with pytest.raises(ValueError, match="cannot decrypt"):
        await asyncio.to_thread(backup.restore_to_new_directory, bad, tmp_path / "bad-restore")
    assert not (tmp_path / "bad-restore").exists()


@pytest.mark.asyncio
async def test_cli_inspects_and_restores_valid_archive(storage, system_key_manager, tmp_path):
    archive = await backup.InstanceBackup(storage, system_key_manager).create()
    assert await asyncio.to_thread(cli.main, ["inspect-backup", str(archive)]) == 0
    assert (
        await asyncio.to_thread(
            cli.main,
            [
                "restore-backup",
                str(archive),
                "--destination",
                str(tmp_path / "cli-restore"),
                "--confirm-stopped",
            ],
        )
        == 0
    )


def rewrite_archive(source, destination, change):
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, "w") as output:
        for name in original.namelist():
            output.writestr(name, change(name, original.read(name)))


@pytest.mark.asyncio
async def test_corruption_and_schema_mismatch_leave_no_destination(
    storage, system_key_manager, tmp_path
):
    archive = await backup.InstanceBackup(storage, system_key_manager).create()
    bad = tmp_path / "bad.zip"
    rewrite_archive(
        archive, bad, lambda name, data: b"corrupt" if name == "system-secrets.json" else data
    )
    with pytest.raises(ValueError, match="checksum"):
        await asyncio.to_thread(backup.restore_to_new_directory, bad, tmp_path / "corrupt")
    assert not (tmp_path / "corrupt").exists()

    def newer(name, data):
        if name == "manifest.json":
            value = json.loads(data)
            value["migrations"] = ["99999999999999"]
            return json.dumps(value)
        return data

    rewrite_archive(archive, bad, newer)
    with pytest.raises(ValueError, match="schema"):
        await asyncio.to_thread(backup.restore_to_new_directory, bad, tmp_path / "newer")
    assert not (tmp_path / "newer").exists()


def test_archive_traversal_is_rejected(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("../escape", "secret")
    with pytest.raises(ValueError, match="members"):
        backup.restore_to_new_directory(archive, tmp_path / "restore")
    assert not (tmp_path / "escape").exists()


def test_cli_requires_stopped_acknowledgment(tmp_path):
    with pytest.raises(SystemExit) as exc:
        cli.main(["restore-backup", "unused.zip", "--destination", str(tmp_path / "new")])
    assert exc.value.code == 1


@pytest.mark.asyncio
async def test_backup_api_auth_confirmation_and_download(
    storage, system_key_manager, authed_client, monkeypatch
):
    service = backup.InstanceBackup(storage, system_key_manager)
    monkeypatch.setattr(app.state, "instance_backup", service, raising=False)
    assert authed_client.post("/api/backups", json={}).status_code == 400
    created = authed_client.post("/api/backups", json={"include_secrets_confirmed": True})
    assert created.status_code == 201, created.text
    name = created.json()["name"]
    assert authed_client.get("/api/backups").json()["items"][0]["name"] == name
    response = authed_client.get(f"/api/backups/{name}/download")
    assert response.status_code == 200 and response.content[:2] == b"PK"
    assert response.headers["cache-control"] == "no-store"
    assert authed_client.get("/api/backups/system-secrets.json/download").status_code == 404
    authed_client.headers.pop("Authorization")
    assert authed_client.get("/api/backups").status_code == 401


@pytest.mark.asyncio
async def test_scheduled_failure_records_status_and_alerts_once(
    storage, system_key_manager, monkeypatch, authed_client
):
    from releasetracker.main import app

    created = await storage.create_notifier(
        {
            "name": "ops",
            "type": "webhook",
            "url": "https://hooks.example/x",
            "events": ["error"],
            "enabled": True,
        }
    )
    service = backup.InstanceBackup(storage, system_key_manager)
    good = await service.create()
    assert (await service.status())["consecutive_failures"] == 0

    def fail(*args, **kwargs):
        raise OSError("/secret/path disk full")

    monkeypatch.setattr(backup, "_create_archive", fail)
    for _ in range(2):
        with pytest.raises(OSError):
            await service.create(scheduled=True)
    status = await service.status()
    assert status["consecutive_failures"] == 2
    assert status["last_error_code"] == "storage_error"
    assert "secret" not in json.dumps(status)
    db = await storage._get_connection()
    alerts = await (
        await db.execute("SELECT notifier_id,event,release FROM release_notification_outbox")
    ).fetchall()
    assert len(alerts) == 1 and alerts[0][0] == created.id and alerts[0][1] == "error"
    assert "secret" not in alerts[0][2]

    app.state.instance_backup = service
    await storage.set_setting("system.backup_interval_hours", "24")
    listed = authed_client.get("/api/backups").json()
    assert listed["consecutive_failures"] == 2 and listed["last_error_code"] == "storage_error"
    assert listed["overdue"] is False and listed["last_success_at"] == pytest.approx(
        good.stat().st_mtime
    )


@pytest.mark.asyncio
async def test_api_archive_timestamp_is_not_changed_by_file_modification(
    storage, system_key_manager, authed_client, monkeypatch
):
    import os

    service = backup.InstanceBackup(storage, system_key_manager)
    archive = await service.create()
    created = backup.archive_created_at(archive)
    os.utime(archive, (created + 90000, created + 90000))
    app.state.instance_backup = service
    await storage.set_setting("system.backup_interval_hours", "24")
    result = authed_client.get("/api/backups").json()
    assert result["items"][0]["created_at"] == pytest.approx(created, rel=0, abs=0.001)
    assert result["last_success_at"] == pytest.approx(created, rel=0, abs=0.001)


@pytest.mark.asyncio
async def test_failed_zip_verification_cannot_prune_or_publish(
    storage, system_key_manager, monkeypatch
):
    service = backup.InstanceBackup(storage, system_key_manager)
    good = await service.create(retain=1)
    last_success = service.last_success

    def fail(archive):
        assert archive.parent != service.directory
        assert list(service.directory.glob("releasetracker-*.zip")) == [good]
        raise ValueError("Backup checksum mismatch")

    monkeypatch.setattr(backup, "verify_archive", fail)
    with pytest.raises(ValueError, match="checksum"):
        await service.create(retain=1)
    assert list(service.directory.glob("releasetracker-*.zip")) == [good]
    assert not list(service.directory.glob(".unverified-*"))
    assert service.last_success == last_success


@pytest.mark.asyncio
async def test_verification_cancellation_holds_all_locks(storage, system_key_manager, monkeypatch):
    started, finish = threading.Event(), threading.Event()
    original = backup.verify_archive

    def verify(archive):
        started.set()
        assert finish.wait(10)
        return original(archive)

    monkeypatch.setattr(backup, "verify_archive", verify)
    service = backup.InstanceBackup(storage, system_key_manager)
    task = asyncio.create_task(service.create())
    try:
        assert await asyncio.to_thread(started.wait, 10)
        task.cancel()
        await asyncio.sleep(0)
        assert service.lock.locked() and system_key_manager.lock.locked()
        assert storage.encryption_rotation_lock.locked()
        assert not list(service.directory.glob("releasetracker-*.zip"))
        with pytest.raises(ValueError, match="already running"):
            await service.create()
    finally:
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not service.lock.locked()
    assert len(list(service.directory.glob("releasetracker-*.zip"))) == 1


def test_archive_creation_order_survives_copy_or_corrupt_file_mtime_changes(tmp_path):
    import os

    older = tmp_path / "releasetracker-1700000000000000000-abcd0000.zip"
    newer = tmp_path / "releasetracker-1700000900000000000-abcd0000.zip"
    older.write_text("old")
    newer.write_text("new")
    os.utime(older, (1900000000, 1900000000))
    os.utime(newer, (1800000000, 1800000000))
    assert backup.archive_created_at(older) == 1700000000
    assert backup.retention_candidates([older, newer], 1, 0, 0) == [older]
    service = backup.InstanceBackup(
        type("Storage", (), {"db_path": str(tmp_path / "db")})(), None, directory=tmp_path
    )
    assert service.latest_archive_time() == 1700000900


def test_retention_tiers_keep_recent_and_utc_daily_weekly_points(tmp_path):
    import os

    now = 1800000000
    paths = []
    for index, age in enumerate([0, 100, 86400, 2 * 86400, 8 * 86400, 16 * 86400, 40 * 86400]):
        path = tmp_path / f"releasetracker-{index}-abc.zip"
        path.write_text("test")
        os.utime(path, (now - age, now - age))
        paths.append(path)
    deleted = backup.retention_candidates(paths, 1, 3, 4, now=now)
    assert paths[0] not in deleted
    assert paths[1] in deleted  # same day's older backup
    assert paths[2] not in deleted and paths[3] not in deleted
    assert paths[4] not in deleted and paths[5] not in deleted
    assert paths[6] in deleted
    assert backup.retention_candidates(paths, 1, 0, 0, now=now) == paths[1:]


def test_invalid_retention_tiers_rejected(monkeypatch):
    monkeypatch.setenv("RELEASETRACKER_BACKUP_DAILY_RETENTION", "91")
    with pytest.raises(ValueError):
        backup.retention_tiers()


@pytest.mark.asyncio
async def test_corrupt_written_archive_is_reported(storage, system_key_manager, monkeypatch):
    service = backup.InstanceBackup(storage, system_key_manager)

    def corrupt(archive):
        raise ValueError("Backup database integrity check failed")

    monkeypatch.setattr(backup, "verify_archive", corrupt)
    with pytest.raises(ValueError):
        await service.create()
    assert (await service.status())["last_error_code"] == "integrity_failed"


@pytest.mark.asyncio
async def test_daily_verification_detects_corruption_without_deleting(
    storage, system_key_manager, monkeypatch
):
    service = backup.InstanceBackup(storage, system_key_manager)
    archive = await service.create()
    assert await service.verify_latest()
    assert (await service.status())["last_verified_at"]
    original = archive.read_bytes()
    archive.write_bytes(b"not a ZIP")
    with pytest.raises(zipfile.BadZipFile):
        await service.verify_latest()
    assert archive.exists()
    assert (await service.status())["consecutive_failures"] == 1
    archive.write_bytes(original)
    assert await service.verify_latest()
    assert (await service.status())["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_daily_verification_accepts_old_schema_but_restore_remains_strict(
    storage, system_key_manager, monkeypatch, tmp_path
):
    service = backup.InstanceBackup(storage, system_key_manager)
    archive = await service.create()
    future = tmp_path / "future" / "dbmate" / "migrations"
    future.mkdir(parents=True)
    for existing in (backup.backend_dir() / "dbmate/migrations").glob("*.sql"):
        (future / existing.name).write_text("-- test")
    (future / "99999999999999_future.sql").write_text("-- test")
    monkeypatch.setattr(backup, "backend_dir", lambda: tmp_path / "future")
    assert await service.verify_latest()
    with pytest.raises(ValueError, match="schema"):
        backup.restore_to_new_directory(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.asyncio
async def test_manual_backup_creation_rejects_overlap_until_verified(
    storage, system_key_manager, monkeypatch
):
    service = backup.InstanceBackup(storage, system_key_manager)
    async with service.lock:
        with pytest.raises(ValueError, match="already running"):
            await service.create()
        assert await service.verify_latest() is False


@pytest.mark.parametrize("hours,retain", [("8761", "7"), ("-1", "7"), ("x", "7"), ("24", "0")])
async def test_invalid_schedule_fails_closed(storage, hours, retain):
    await storage.set_setting("system.backup_interval_hours", hours)
    await storage.set_setting("system.backup_retention", retain)
    with pytest.raises(ValueError):
        await backup.backup_options(storage)
