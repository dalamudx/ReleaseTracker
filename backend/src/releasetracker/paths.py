"""Filesystem locations shared by the server, CLI and container entrypoint."""

from __future__ import annotations

import os
from pathlib import Path

# Retired override; still inspected so an upgrade cannot bootstrap over old data.
LEGACY_DB_PATH_ENV = "RELEASETRACKER_DB_PATH"


def backend_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """One fixed data directory; no override can split the server, CLI and dbmate."""
    return backend_dir() / "data"


def database_path() -> Path:
    return data_dir() / "releases.db"


def system_secrets_path() -> Path:
    # Keys must travel with the database they encrypt.
    return database_path().parent / "system-secrets.json"


def legacy_database_path() -> Path | None:
    """Configured value of the retired override, or None when it names the fixed file."""
    raw = os.environ.get(LEGACY_DB_PATH_ENV, "").strip()
    if not raw:
        return None
    candidate = Path(raw).resolve()
    return None if candidate == database_path().resolve() else candidate


def reject_legacy_database_override() -> None:
    """Require an explicit switch away from a declared custom database."""
    legacy = legacy_database_path()
    if legacy is None:
        return
    state = "still holds a database" if legacy.is_file() else "is not mounted"
    raise RuntimeError(
        f"{LEGACY_DB_PATH_ENV} is retired and {legacy} {state}. "
        f"Verify the database, system secrets and backups in {data_dir()}, "
        f"then unset {LEGACY_DB_PATH_ENV} to use that directory."
    )
