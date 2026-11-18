import os
from pathlib import Path
import subprocess
import sys

import pytest

from releasetracker import paths
from releasetracker.services.backup_configuration import pre_migration_directory

# These tests assert the real fixed location, so the data-dir sandbox is off.
pytestmark = pytest.mark.real_data_paths


def test_default_paths_live_under_backend_data():
    assert paths.database_path() == paths.backend_dir() / "data" / "releases.db"
    assert paths.system_secrets_path() == paths.backend_dir() / "data" / "system-secrets.json"
    assert paths.database_path().relative_to(paths.backend_dir()) == Path("data/releases.db")
    assert pre_migration_directory(paths.database_path()).relative_to(paths.backend_dir()) == Path(
        "data/backups"
    )


def test_retired_path_overrides_are_ignored(monkeypatch, tmp_path):
    # Server, CLI, backups and dbmate all follow the fixed location.
    monkeypatch.setenv("RELEASETRACKER_DB_PATH", f"  {tmp_path / 'state' / 'rt.db'}  ")
    monkeypatch.setenv("DATABASE_URL", f"sqlite://{tmp_path / 'state' / 'rt.db'}")
    assert paths.database_path() == paths.backend_dir() / "data" / "releases.db"
    assert paths.system_secrets_path() == paths.backend_dir() / "data" / "system-secrets.json"


@pytest.mark.parametrize("mounted", [True, False])
def test_retired_override_refuses_silent_empty_database(monkeypatch, tmp_path, mounted):
    legacy = tmp_path / "custom" / "rt.db"
    if mounted:
        legacy.parent.mkdir()
        legacy.write_text("original")
    monkeypatch.setenv("RELEASETRACKER_DB_PATH", str(legacy))
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    # A declared custom location blocks a fresh bootstrap even when unmounted.
    with pytest.raises(RuntimeError, match="retired") as error:
        paths.reject_legacy_database_override()
    assert ("not mounted" if not mounted else "holds a database") in str(error.value)
    assert not (tmp_path / "data" / "releases.db").exists()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "releases.db").write_text("another database")
    with pytest.raises(RuntimeError, match="retired"):
        paths.reject_legacy_database_override()
    monkeypatch.delenv("RELEASETRACKER_DB_PATH")
    paths.reject_legacy_database_override()


@pytest.mark.parametrize("declared", ["data/releases.db", "./data/../data/releases.db"])
def test_relative_override_naming_the_fixed_file_is_allowed(monkeypatch, declared):
    monkeypatch.setenv("RELEASETRACKER_DB_PATH", declared)
    monkeypatch.chdir(paths.backend_dir())
    assert paths.legacy_database_path() is None
    paths.reject_legacy_database_override()


def test_relative_override_outside_backend_blocks_empty_bootstrap(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "backend_dir", lambda: tmp_path / "backend")
    monkeypatch.setenv("RELEASETRACKER_DB_PATH", "data/releases.db")
    legacy = tmp_path / "data" / "releases.db"
    legacy.parent.mkdir()
    legacy.touch()

    assert paths.legacy_database_path() == legacy
    with pytest.raises(RuntimeError, match="still holds a database"):
        paths.reject_legacy_database_override()


def test_entrypoint_migration_uses_app_database_path(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REAL_PYTHON", sys.executable)
    monkeypatch.setenv("MIGRATION_ARGS", str(tmp_path / "migration-args"))
    monkeypatch.setenv("PYTHONPATH", str(paths.backend_dir() / "src"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text(
        "#!/bin/sh\n" 'if [ "$1" = "-m" ]; then exit 0; fi\n' 'exec "$REAL_PYTHON" "$@"\n'
    )
    dbmate = bin_dir / "dbmate"
    dbmate.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$MIGRATION_ARGS"\n')
    python.chmod(0o755)
    dbmate.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    script = paths.backend_dir() / "scripts" / "docker-entrypoint.sh"
    subprocess.run(["sh", str(script), "migrate"], check=True, capture_output=True, text=True)
    args = (tmp_path / "migration-args").read_text().splitlines()
    assert args[:2] == ["--url", f"sqlite://{paths.database_path()}"]
    assert args[-1] == "migrate"
    assert (
        pre_migration_directory(paths.database_path()) == paths.database_path().parent / "backups"
    )
    assert paths.system_secrets_path().parent == paths.database_path().parent
