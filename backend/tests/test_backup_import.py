import asyncio
import io
import threading
import zipfile

import pytest

from releasetracker.main import app
from releasetracker.services import instance_backup as backup

pytestmark = pytest.mark.asyncio


async def chunks(*values):
    for value in values:
        yield value


async def test_import_validated_backup_without_restoring_or_overwriting(
    storage, system_key_manager, authed_client, monkeypatch
):
    service = backup.InstanceBackup(storage, system_key_manager)
    monkeypatch.setattr(app.state, "instance_backup", service, raising=False)
    await storage.set_setting("import-probe", "before")
    source = await service.create()
    content = source.read_bytes()
    keys = system_key_manager.secrets_path.read_bytes()
    await storage.set_setting("import-probe", "live-data")
    result = authed_client.post(
        "/api/backups/import", content=content, headers={"Content-Type": "application/zip"}
    )
    assert result.status_code == 201, result.text
    imported = service.archive(result.json()["name"])
    assert imported != source and source.exists()
    assert imported.read_bytes() == content
    assert imported.stat().st_mode & 0o777 == 0o600
    assert abs(backup.archive_created_at(source) - backup.archive_created_at(imported)) < 1
    assert await storage.get_setting("import-probe") == "live-data"
    assert system_key_manager.secrets_path.read_bytes() == keys
    assert len(authed_client.get("/api/backups").json()["items"]) == 2
    assert authed_client.get(f"/api/backups/{imported.name}/download").content == content
    assert not list(service.directory.glob(".import-*"))


async def test_import_rejects_invalid_archive_and_cleans_staging(
    storage, system_key_manager, authed_client, monkeypatch
):
    service = backup.InstanceBackup(storage, system_key_manager)
    monkeypatch.setattr(app.state, "instance_backup", service, raising=False)
    source = await service.create()
    invalid = io.BytesIO()
    with zipfile.ZipFile(invalid, "w") as output:
        output.writestr("../system-secrets.json", "unsafe")
    for content in (b"not a ZIP", invalid.getvalue()):
        response = authed_client.post(
            "/api/backups/import", content=content, headers={"Content-Type": "application/zip"}
        )
        assert response.status_code == 400 and response.json()["detail"] == "backup_import_invalid"
        assert service.archives() == [source]
        assert not list(service.directory.glob(".import-*"))
    # A real archive with an altered database must not be published either.
    tampered = io.BytesIO()
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(tampered, "w") as output:
        for name in original.namelist():
            output.writestr(name, b"broken" if name == "releases.db" else original.read(name))
    with pytest.raises(backup.BackupManagementError, match="backup_import_invalid"):
        await service.import_archive(chunks(tampered.getvalue()))
    assert service.archives() == [source]
    assert not service.lock.locked()
    assert not list(service.directory.glob(".import-*"))


async def test_import_limits_chunked_upload_and_rejects_busy(
    storage, system_key_manager, authed_client, monkeypatch
):
    service = backup.InstanceBackup(storage, system_key_manager)
    monkeypatch.setattr(app.state, "instance_backup", service, raising=False)
    monkeypatch.setattr(backup, "MAX_ARCHIVE_BYTES", 10)
    with pytest.raises(backup.BackupManagementError, match="backup_import_too_large"):
        await service.import_archive(chunks(b"12345678", b"abcdefgh"))
    assert not list(service.directory.iterdir())
    oversized = authed_client.post(
        "/api/backups/import", content=b"x" * 11, headers={"Content-Type": "application/zip"}
    )
    assert oversized.status_code == 413
    assert authed_client.post("/api/backups/import", json={}).status_code == 415
    async with service.lock:
        with pytest.raises(backup.BackupManagementError, match="backup_busy"):
            await service.import_archive(chunks(b"zip"))
    service._restore_pins.add("restore-in-progress")
    with pytest.raises(backup.BackupManagementError, match="backup_busy"):
        await service.import_archive(chunks(b"zip"))
    service._restore_pins.clear()
    authed_client.headers.pop("Authorization")
    assert (
        authed_client.post(
            "/api/backups/import", content=b"zip", headers={"Content-Type": "application/zip"}
        ).status_code
        == 401
    )


async def test_cancelled_upload_removes_partial_file(storage, system_key_manager):
    service = backup.InstanceBackup(storage, system_key_manager)
    started = asyncio.Event()

    async def interrupted():
        yield b"partial"
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(service.import_archive(interrupted()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not service.lock.locked()
    assert not list(service.directory.iterdir())


async def test_cancelled_validation_holds_lock_until_worker_exits(
    storage, system_key_manager, monkeypatch
):
    service = backup.InstanceBackup(storage, system_key_manager)
    started, finish = threading.Event(), threading.Event()

    def validate(*args):
        started.set()
        assert finish.wait(5)

    monkeypatch.setattr(backup, "_import_verified_archive", validate)
    task = asyncio.create_task(service.import_archive(chunks(b"archive")))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert service.lock.locked()
        assert list(service.directory.glob(".import-*"))
    finally:
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not service.lock.locked()
    assert not list(service.directory.iterdir())
