import os
from pathlib import Path
import subprocess
import sys

import pytest

from releasetracker import paths
from releasetracker.services.backup_configuration import pre_migration_directory


def test_default_paths_live_under_backend_data(monkeypatch):
    monkeypatch.delenv(paths.DB_PATH_ENV, raising=False)
    assert paths.database_path() == paths.backend_dir() / "data" / "releases.db"
    assert paths.system_secrets_path() == paths.backend_dir() / "data" / "system-secrets.json"
    assert paths.database_path().relative_to(paths.backend_dir()) == Path("data/releases.db")
    assert pre_migration_directory(paths.database_path()).relative_to(paths.backend_dir()) == Path(
        "data/backups"
    )


def test_db_path_env_matches_entrypoint_and_keeps_keys_beside_database(monkeypatch, tmp_path):
    db_path = tmp_path / "state" / "rt.db"
    monkeypatch.setenv(paths.DB_PATH_ENV, f"  {db_path}  ")
    assert paths.database_path() == db_path
    assert paths.system_secrets_path() == db_path.parent / "system-secrets.json"


@pytest.mark.parametrize("configured", [None, "custom/state.db"])
def test_entrypoint_migration_uses_app_database_path(monkeypatch, tmp_path, configured):
    db_path = tmp_path / configured if configured else None
    if db_path:
        monkeypatch.setenv(paths.DB_PATH_ENV, f"  {db_path}  ")
    else:
        monkeypatch.delenv(paths.DB_PATH_ENV, raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REAL_PYTHON", sys.executable)
    monkeypatch.setenv("MIGRATION_ARGS", str(tmp_path / "migration-args"))
    monkeypatch.setenv("PYTHONPATH", str(paths.backend_dir() / "src"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = "-m" ]; then exit 0; fi\n'
        'exec "$REAL_PYTHON" "$@"\n'
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
    assert pre_migration_directory(paths.database_path()) == paths.database_path().parent / "backups"
    assert paths.system_secrets_path().parent == paths.database_path().parent
