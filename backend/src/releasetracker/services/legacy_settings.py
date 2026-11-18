"""Explicit, offline import of policy variables from a previous release."""

import os
from datetime import datetime

from .instance_policy import IMPORT_MARKER, POLICY_DEFAULTS, normalize_policy
from .metrics_credential import METRICS_TOKEN_SETTING_KEY, encode_token


async def import_legacy_settings(db) -> int:
    """Keep saved values (including empty ones) and commit the import exactly once."""
    await db.execute("BEGIN IMMEDIATE")
    try:
        async with db.execute("SELECT key FROM settings") as cursor:
            saved = {row[0] for row in await cursor.fetchall()}
        if IMPORT_MARKER in saved:
            await db.rollback()
            return 0
        imported = {}
        for key in POLICY_DEFAULTS:
            name = (
                "FORWARDED_ALLOW_IPS"
                if key == "system.forwarded_allow_ips"
                else "RELEASETRACKER_" + key.removeprefix("system.").upper()
            )
            if key in saved or name not in os.environ:
                continue
            value = os.environ[name].strip()
            if key in {"system.online_restore", "system.pre_migration_backup"}:
                if value not in {"0", "1"}:
                    raise ValueError(f"{name} must be 0 or 1")
                value = "true" if value == "1" else "false"
            elif key in {"system.cors_origins", "system.forwarded_allow_ips"}:
                value = value.replace(",", "\n")
            imported[key] = normalize_policy(key, value)
        credential = METRICS_TOKEN_SETTING_KEY
        legacy_token = os.environ.get("RELEASETRACKER_METRICS_TOKEN")
        if credential not in saved and legacy_token is not None:
            # Earlier releases disabled scraping for environment tokens shorter than 32.
            imported[credential] = encode_token(legacy_token) if len(legacy_token) >= 32 else ""
        now = datetime.now().isoformat()
        await db.executemany(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            [(key, value, now) for key, value in {**imported, IMPORT_MARKER: "true"}.items()],
        )
        await db.commit()
        return len(imported)
    except BaseException:
        await db.rollback()
        raise
