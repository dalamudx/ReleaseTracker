"""Persisted backup and restore settings. No environment-variable fallback."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

BACKUP_INTERVAL = "system.backup_interval_hours"
BACKUP_RETENTION = "system.backup_retention"
PRE_MIGRATION_BACKUP = "system.pre_migration_backup"
ONLINE_RESTORE = "system.online_restore"

BACKUP_BOUNDS = {
    BACKUP_INTERVAL: (0, 8760),
    BACKUP_RETENTION: (1, 100),
}
BACKUP_DEFAULTS = {
    BACKUP_INTERVAL: "0",
    BACKUP_RETENTION: "7",
}


def normalize_backup_setting(key, value):
    value = str(value).strip()
    lower, upper = BACKUP_BOUNDS[key]
    if not value.isascii() or not value.isdecimal() or not lower <= int(value) <= upper:
        raise ValueError(f"{key} must be an integer from {lower} to {upper}")
    return str(int(value))


def stored_setting(db_path, key):
    """Read one saved setting before any service starts; None if absent or unreadable."""
    path = Path(db_path)
    if not path.is_file():
        return None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else str(row[0])


def pre_migration_backup_enabled(db_path):
    """Unknown, unreadable and older databases keep the restore point."""
    return stored_setting(db_path, PRE_MIGRATION_BACKUP) != "false"


def online_restore_enabled(db_path):
    """Saved policy decides, but only POSIX file systems may enable online restore."""
    if os.name != "posix":
        return False
    return stored_setting(db_path, ONLINE_RESTORE) != "false"


def pre_migration_directory(db_path):
    """Derive backup directory beside database without starting services."""
    return Path(db_path).parent / "backups"
