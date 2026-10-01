"""Filesystem locations shared by the server, CLI and container entrypoint."""

from __future__ import annotations

import os
from pathlib import Path

DB_PATH_ENV = "RELEASETRACKER_DB_PATH"


def backend_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def database_path() -> Path:
    """Match docker-entrypoint.sh so migrations and the app use one database."""
    configured = os.environ.get(DB_PATH_ENV, "").strip()
    return Path(configured) if configured else backend_dir() / "data" / "releases.db"


def system_secrets_path() -> Path:
    # Keys must travel with the database they encrypt.
    return database_path().parent / "system-secrets.json"
