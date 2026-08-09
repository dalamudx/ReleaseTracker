from __future__ import annotations

import pytest

from releasetracker import cli
from releasetracker.models import LoginRequest, User
from releasetracker.services.auth import AuthService, pwd_context
from releasetracker.services.system_keys import SystemKeyManager
from releasetracker.storage.sqlite import SQLiteStorage


@pytest.mark.asyncio
async def test_interactive_admin_reset_uses_local_data_and_revokes_marker(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    manager = SystemKeyManager(data_dir / "system-secrets.json")
    await manager.initialize()
    storage = SQLiteStorage(str(data_dir / "releases.db"), system_key_manager=manager)
    await storage.initialize()
    admin = await storage.create_user(
        User(
            username="admin",
            email="admin@example.com",
            password_hash=pwd_context.hash("admin"),
        )
    )
    assert admin.id is not None
    await storage.persist_admin_identity(admin.id)
    auth_service = AuthService(storage, manager)
    await auth_service.ensure_admin_user()
    assert await storage.is_admin_password_reset_required()
    await storage.close()

    answers = iter(["new-operator-password", "new-operator-password"])
    monkeypatch.setattr(cli, "_backend_dir", lambda: tmp_path)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: next(answers))

    await cli._reset_admin_password()

    manager = SystemKeyManager(data_dir / "system-secrets.json")
    await manager.initialize()
    storage = SQLiteStorage(str(data_dir / "releases.db"), system_key_manager=manager)
    try:
        assert not await storage.is_admin_password_reset_required()
        user, _ = await AuthService(storage, manager).login(
            LoginRequest(username="admin", password="new-operator-password")
        )
        assert user.id == admin.id
    finally:
        await storage.close()
