"""Isolated persisted global backup policy and native archive behavior."""

from pathlib import Path
from types import SimpleNamespace
import pytest
import httpx
from fastapi import FastAPI
from releasetracker.dependencies import get_current_admin_user
from releasetracker.routers import settings, backups
from releasetracker.services.instance_backup import InstanceBackup, backup_options
from releasetracker.services.backup_configuration import (
    BACKUP_INTERVAL as HOURS,
    BACKUP_RETENTION as KEEP,
    pre_migration_directory,
)
from releasetracker.scheduler_host import SchedulerHost


@pytest.fixture
async def configured(storage, system_key_manager):
    app = FastAPI()
    app.state.storage = storage
    service = InstanceBackup(storage, system_key_manager)
    app.state.instance_backup = service
    host = SchedulerHost()
    service.scheduler_host = host
    app.include_router(settings.router)
    app.include_router(backups.router)
    app.dependency_overrides[get_current_admin_user] = lambda: SimpleNamespace(id=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://isolated"
    ) as client:
        yield service, host, client
    await host.shutdown()


async def test_removed_env_ignored_defaults_visible(configured, monkeypatch, storage):
    service, host, client = configured
    for key in (
        "RELEASETRACKER_BACKUP_DIR",
        "RELEASETRACKER_BACKUP_RETENTION",
        "RELEASETRACKER_BACKUP_INTERVAL_HOURS",
    ):
        monkeypatch.setenv(key, "invalid-old-value")
    await service.load_configuration()
    await service.reschedule()
    assert await backup_options(storage) == (0, 7)
    assert host.get_job("maintenance", "instance_backup") is None
    data = {item["key"]: item["value"] for item in (await client.get("/api/settings")).json()}
    assert [data[k] for k in (HOURS, KEEP)] == ["0", "7"]
    listing = (await client.get("/api/backups")).json()
    assert listing["directory"] == str(Path(storage.db_path).parent / "backups")
    assert service.directory == Path(storage.db_path).parent / "backups"


async def test_hot_retention_schedule_restart_and_reset(configured, storage, system_key_manager):
    service, host, client = configured
    old = await service.create()
    for key, value in ((KEEP, "1"), (HOURS, "24")):
        result = await client.post("/api/settings", json={"key": key, "value": value})
        assert result.status_code == 200, result.text
    assert host.get_job("maintenance", "instance_backup").trigger.interval.total_seconds() == 86400
    first = await service.create()
    second = await service.create()
    assert not first.exists() and second.exists() and not old.exists()
    data = (await client.get("/api/backups")).json()
    assert (
        data["directory"] == str(Path(storage.db_path).parent / "backups")
        and data["retention"] == 1
        and data["interval_hours"] == 24
    )
    restarted = InstanceBackup(storage, system_key_manager)
    await restarted.load_configuration()
    assert restarted.directory == Path(storage.db_path).parent / "backups"
    assert pre_migration_directory(storage.db_path) == Path(storage.db_path).parent / "backups"
    assert (await client.delete("/api/settings/" + HOURS)).status_code == 200
    assert host.get_job("maintenance", "instance_backup") is None


@pytest.mark.parametrize(
    "key,value",
    [
        (HOURS, "8761"),
        (HOURS, "-1"),
        (HOURS, "1.5"),
        (KEEP, "0"),
        (KEEP, "101"),
    ],
)
async def test_invalid_settings_do_not_persist(configured, storage, key, value):
    _, _, client = configured
    assert (
        await client.post("/api/settings", json={"key": key, "value": value})
    ).status_code == 400
    assert await storage.get_setting(key) is None


async def test_operation_pins_and_busy(configured, storage):
    service, _, client = configured
    async with service.lock:
        assert (
            await client.post("/api/settings", json={"key": HOURS, "value": "24"})
        ).status_code == 409
    archive = await service.create()
    service._restore_pins.add(archive.name)
    try:
        assert (
            await client.post("/api/settings", json={"key": KEEP, "value": "1"})
        ).status_code == 409
    finally:
        service._restore_pins.clear()
