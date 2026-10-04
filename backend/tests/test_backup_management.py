"""Backup lifecycle uses only temporary SQLite and private synthetic archives."""

import asyncio
import threading
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request
from starlette.responses import FileResponse

from releasetracker.main import app
from releasetracker.routers import backups
from releasetracker.services import instance_backup as module


async def archives(storage, manager, count=2):
    service = module.InstanceBackup(storage, manager)
    paths = [await service.create(retain=10) for _ in range(count)]
    return service, paths


async def test_unmanaged_file_is_not_a_backup_or_pruning_candidate(storage, system_key_manager):
    service, paths = await archives(storage, system_key_manager)
    unrelated = service.directory / "releasetracker-unmanaged.zip"
    unrelated.write_bytes(b"keep unrelated content")
    assert unrelated not in service.archives()
    assert service.latest_archive_time() == module.archive_created_at(paths[-1])
    newest = await service.create(retain=1)
    assert unrelated.read_bytes() == b"keep unrelated content"
    assert await service.verify_latest()
    with pytest.raises(module.BackupManagementError, match="last_local_backup"):
        await service.delete(newest.name)


async def test_delete_disk_error_preserves_archive_and_hides_paths(
    storage, system_key_manager, authed_client, monkeypatch
):
    service, paths = await archives(storage, system_key_manager)
    monkeypatch.setattr(app.state, "instance_backup", service, raising=False)

    def fail(path):
        raise PermissionError("private path not for clients")

    monkeypatch.setattr(module, "_unlink_archive", fail)
    response = authed_client.request(
        "DELETE", f"/api/backups/{paths[0].name}", json={"confirm_name": paths[0].name}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "backup_delete_failed"
    assert "private path" not in response.text
    assert all(path.exists() for path in paths)


async def test_delete_updates_inventory_but_not_live_data(storage, system_key_manager):
    await storage.set_setting("lifecycle-probe", "unchanged")
    service, paths = await archives(storage, system_key_manager, 3)
    await service.delete(paths[-1].name)
    assert not paths[-1].exists() and all(path.exists() for path in paths[:-1])
    assert service.last_success == module.archive_created_at(paths[-2])
    assert (await service.status())["last_verified_at"] == 0
    assert await storage.get_setting("lifecycle-probe") == "unchanged"
    await service.delete(paths[0].name)
    with pytest.raises(module.BackupManagementError, match="last_local_backup"):
        await service.delete(paths[1].name)
    assert paths[1].exists()


@pytest.mark.parametrize(
    "name", ["../releases.db", "system-secrets.json", "other.zip", "releasetracker-1-deadbeef.zip"]
)
async def test_delete_rejects_bad_or_missing_names(storage, system_key_manager, name):
    service, _ = await archives(storage, system_key_manager)
    with pytest.raises(module.BackupManagementError) as exc:
        await service.delete(name)
    assert exc.value.status_code == 404


async def test_delete_and_download_reject_symlink_or_directory(
    storage, system_key_manager, tmp_path
):
    service, _ = await archives(storage, system_key_manager)
    target = tmp_path / "unrelated-file"
    target.write_bytes(b"keep")
    link = service.directory / "releasetracker-1-deadbeef.zip"
    link.symlink_to(target)
    folder = service.directory / "releasetracker-2-deadbeef.zip"
    folder.mkdir()
    for path in (link, folder):
        for action in (service.delete, service.acquire_download):
            with pytest.raises(module.BackupManagementError):
                await action(path.name)
    assert target.read_bytes() == b"keep" and link.is_symlink() and folder.is_dir()


async def test_download_protects_archive_from_delete_and_retention(storage, system_key_manager):
    service, paths = await archives(storage, system_key_manager)
    await service.acquire_download(paths[0].name)
    await service.acquire_download(paths[0].name)
    with pytest.raises(module.BackupManagementError, match="backup_in_use"):
        await service.delete(paths[0].name)
    new = await service.create(retain=1)
    assert paths[0].exists() and not paths[1].exists() and new.exists()
    service.release_download(paths[0].name)
    assert service._downloads[paths[0].name] == 1
    service.release_download(paths[0].name)
    await service.delete(paths[0].name)
    assert not paths[0].exists() and new.exists()


async def test_delete_busy_preserves_all_archives(storage, system_key_manager):
    service, paths = await archives(storage, system_key_manager)
    async with service.lock:
        for action in (service.delete, service.acquire_download):
            with pytest.raises(module.BackupManagementError, match="backup_busy"):
                await action(paths[0].name)
    assert all(path.exists() for path in paths)


async def test_delete_cancellation_does_not_release_lock_early(
    storage, system_key_manager, monkeypatch
):
    service, paths = await archives(storage, system_key_manager)
    started, finish = threading.Event(), threading.Event()
    original = module._unlink_archive

    def unlink(path):
        started.set()
        assert finish.wait(5)
        original(path)

    monkeypatch.setattr(module, "_unlink_archive", unlink)
    task = asyncio.create_task(service.delete(paths[-1].name))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert service.lock.locked()
        with pytest.raises(module.BackupManagementError, match="backup_busy"):
            await service.delete(paths[0].name)
    finally:
        finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not service.lock.locked() and not paths[-1].exists() and paths[0].exists()
    assert service.last_success == module.archive_created_at(paths[0])
    assert (await service.status())["last_verified_at"] == 0


@pytest.mark.parametrize("cancel", [False, True])
async def test_download_response_releases_borrow_on_failure(
    storage, system_key_manager, monkeypatch, cancel
):
    service, paths = await archives(storage, system_key_manager)
    monkeypatch.setattr(app.state, "instance_backup", service, raising=False)
    request = Request({"type": "http", "app": app})
    response = await backups.download_backup(paths[0].name, request)
    error = asyncio.CancelledError() if cancel else OSError("closed network")
    monkeypatch.setattr(FileResponse, "__call__", AsyncMock(side_effect=error))
    with pytest.raises(type(error)):
        await response({}, None, None)
    assert service._downloads == {}


async def test_backup_management_api_confirmation_auth_and_policy(
    storage, system_key_manager, authed_client, monkeypatch
):
    service, paths = await archives(storage, system_key_manager)
    monkeypatch.setattr(app.state, "instance_backup", service, raising=False)
    monkeypatch.setenv("RELEASETRACKER_BACKUP_DAILY_RETENTION", "14")
    monkeypatch.setenv("RELEASETRACKER_BACKUP_WEEKLY_RETENTION", "4")
    inventory = authed_client.get("/api/backups").json()
    assert inventory["daily_retention"] == 14 and inventory["weekly_retention"] == 4
    assert inventory["total_size"] == sum(path.stat().st_size for path in paths)
    name = paths[0].name
    assert (
        authed_client.request(
            "DELETE", f"/api/backups/{name}", json={"confirm_name": "wrong"}
        ).status_code
        == 400
    )
    assert paths[0].exists()
    assert authed_client.request(
        "DELETE", f"/api/backups/{name}", json={"confirm_name": name}
    ).json() == {"deleted": name}
    assert not paths[0].exists()
    response = authed_client.request(
        "DELETE", f"/api/backups/{paths[1].name}", json={"confirm_name": paths[1].name}
    )
    assert response.status_code == 409 and response.json()["detail"] == "last_local_backup"
    response = authed_client.get(
        f"/api/backups/{paths[1].name}/download", headers={"Range": "bytes=0-1"}
    )
    assert response.status_code == 206 and response.content == b"PK" and service._downloads == {}
    authed_client.headers.pop("Authorization")
    assert (
        authed_client.request(
            "DELETE", f"/api/backups/{paths[1].name}", json={"confirm_name": paths[1].name}
        ).status_code
        == 401
    )
    assert paths[1].exists()
