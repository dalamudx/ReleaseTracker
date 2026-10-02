from __future__ import annotations

import sqlite3

import pytest

from releasetracker import cli
from releasetracker.models import LoginRequest, User
from releasetracker.services.auth import AuthService, pwd_context
from releasetracker.services.system_keys import SystemKeyManager
from releasetracker.storage.sqlite import SQLiteStorage


def test_restore_review_cli_requires_confirmation_and_preserves_revoked_tasks(
    tmp_path, monkeypatch, capsys
):

    db_path = tmp_path / "releases.db"
    monkeypatch.setenv("RELEASETRACKER_DB_PATH", str(db_path))
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO settings VALUES('restore.review_required','{}')")
        db.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY,state TEXT)")
        db.execute("INSERT INTO tasks VALUES(1,'cancelled')")

    with pytest.raises(SystemExit):
        cli.main(["acknowledge-restore"])
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM settings").fetchone()[0] == 1
    assert cli.main(["acknowledge-restore", "--confirm-reviewed"]) == 0
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM settings").fetchone()[0] == 0
        assert db.execute("SELECT state FROM tasks").fetchone()[0] == "cancelled"
    assert "Old tasks and approvals remain revoked" in capsys.readouterr().out


def test_audit_database_is_read_only_and_rejects_orphans(tmp_path, monkeypatch):

    db_path = tmp_path / "releases.db"
    monkeypatch.setenv("RELEASETRACKER_DB_PATH", str(db_path))
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE parents(id INTEGER PRIMARY KEY)")
        db.execute(
            "CREATE TABLE children(id INTEGER PRIMARY KEY,parent_id INTEGER REFERENCES parents(id))"
        )
        db.execute("INSERT INTO children VALUES(1,999)")
    with pytest.raises(SystemExit):
        cli.main(["audit-database"])
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT parent_id FROM children").fetchone()[0] == 999
        db.execute("INSERT INTO parents VALUES(999)")
    assert cli.main(["audit-database"]) == 0


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
    monkeypatch.setenv("RELEASETRACKER_DB_PATH", str(data_dir / "releases.db"))
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
