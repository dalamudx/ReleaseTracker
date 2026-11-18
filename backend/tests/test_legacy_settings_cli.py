"""The explicit legacy policy import must never create or migrate a database."""

from pathlib import Path

import pytest

from releasetracker import cli


async def test_import_refuses_missing_database_and_keys(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(cli.paths, "database_path", lambda: data_dir / "releases.db")
    monkeypatch.setattr(cli.paths, "system_secrets_path", lambda: data_dir / "system-secrets.json")
    # Missing database must fail before SQLiteStorage.initialize can bootstrap one.
    with pytest.raises(ValueError, match="missing"):
        await cli._import_legacy_settings()
    assert not (data_dir / "releases.db").exists()
    assert not (data_dir / "system-secrets.json").exists()

    (data_dir / "releases.db").write_bytes(b"placeholder")
    with pytest.raises(ValueError, match="missing"):
        await cli._import_legacy_settings()
    assert not (data_dir / "system-secrets.json").exists()


async def test_import_writes_settings_only_and_keeps_saved_values(
    storage, system_key_manager, monkeypatch
):
    from releasetracker.services.instance_policy import IMPORT_MARKER

    await storage.set_setting("system.worker_poll_seconds", "9")
    db_path, keys_path = Path(storage.db_path), system_key_manager.secrets_path
    monkeypatch.setattr(cli.paths, "database_path", lambda: db_path)
    monkeypatch.setattr(cli.paths, "system_secrets_path", lambda: keys_path)
    for name in (
        "RELEASETRACKER_MUTATION_CONCURRENCY",
        "RELEASETRACKER_WORKER_POLL_SECONDS",
        "RELEASETRACKER_CORS_ORIGINS",
        "RELEASETRACKER_PRE_MIGRATION_BACKUP",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RELEASETRACKER_MUTATION_CONCURRENCY", "3")

    imported = await cli._import_legacy_settings()

    assert imported == 1
    settings = await storage.get_all_settings_with_updated_at()
    assert settings["system.mutation_concurrency"][0] == "3"
    assert settings["system.worker_poll_seconds"][0] == "9"  # Saved value wins.
    assert settings[IMPORT_MARKER][0] == "true"
    # A retry after the marker reports nothing new and preserves edits.
    await storage.set_setting("system.mutation_concurrency", "2")
    assert await cli._import_legacy_settings() == 0
    assert await storage.get_setting("system.mutation_concurrency") == "2"
