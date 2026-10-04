"""Persisted global backup settings. No environment-variable fallback."""

from pathlib import Path

BACKUP_INTERVAL = "system.backup_interval_hours"
BACKUP_RETENTION = "system.backup_retention"
BACKUP_DEFAULTS = {BACKUP_INTERVAL: "0", BACKUP_RETENTION: "7"}


def normalize_backup_setting(key, value):
    value = str(value).strip()
    lower, upper = (0, 8760) if key == BACKUP_INTERVAL else (1, 100)
    if not value.isascii() or not value.isdecimal() or not lower <= int(value) <= upper:
        raise ValueError(f"{key} must be an integer from {lower} to {upper}")
    return str(int(value))


def pre_migration_directory(db_path):
    """Derive backup directory beside database without starting services."""
    return Path(db_path).parent / "backups"
