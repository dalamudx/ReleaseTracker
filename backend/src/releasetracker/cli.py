"""Local operator commands for ReleaseTracker."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sqlite3
import tempfile
import zipfile

from .paths import database_path, system_secrets_path
from .services.auth import AuthService
from .services.system_keys import SystemKeyManager
from .storage.sqlite import SQLiteStorage


async def _reset_admin_password() -> None:
    password = getpass.getpass("New administrator password: ")
    confirmation = getpass.getpass("Confirm new administrator password: ")
    if password != confirmation:
        raise ValueError("Administrator passwords do not match")

    key_manager = SystemKeyManager(system_secrets_path())
    await key_manager.initialize()
    storage = SQLiteStorage(str(database_path()), system_key_manager=key_manager)
    try:
        await storage.initialize()
        auth_service = AuthService(storage, key_manager)
        await auth_service.reset_admin_password(password)
    finally:
        await storage.close()


def main(argv: list[str] | None = None) -> int:
    """Run a local operator command."""
    parser = argparse.ArgumentParser(prog="releasetracker")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "reset-admin-password",
        help="interactively reset the stable local administrator password",
    )
    inspect_parser = subcommands.add_parser(
        "inspect-backup", help="validate a trusted backup without restoring"
    )
    inspect_parser.add_argument("archive")
    subcommands.add_parser(
        "pre-migration-backup",
        help="back up the database and keys if dbmate migrations are pending",
    )
    restore_parser = subcommands.add_parser(
        "restore-backup", help="restore to a NEW directory; never overwrite live data"
    )
    restore_parser.add_argument("archive")
    restore_parser.add_argument("--destination", required=True)
    restore_parser.add_argument(
        "--confirm-stopped",
        action="store_true",
        help="confirm the application is stopped before switching data volumes",
    )
    args = parser.parse_args(argv)

    try:
        if args.command in {"inspect-backup", "restore-backup"}:
            from .services.instance_backup import validate_archive, restore_to_new_directory

            if args.command == "inspect-backup":
                with tempfile.TemporaryDirectory(prefix="rt-inspect-") as temporary:
                    manifest = validate_archive(args.archive, temporary)
            else:
                if not args.confirm_stopped:
                    raise ValueError(
                        "Stop the application and pass --confirm-stopped before restoring"
                    )
                manifest = restore_to_new_directory(args.archive, args.destination)
                print(
                    "Restored to a new directory; sessions and OAuth states revoked. Keep the old volume; review pending tasks before enabling deployment."
                )
            print(json.dumps(manifest, indent=2))
            return 0
        if args.command == "pre-migration-backup":
            import os

            from .paths import backend_dir
            from .services.instance_backup import pre_migration_backup

            if os.environ.get("RELEASETRACKER_PRE_MIGRATION_BACKUP", "1").strip() == "0":
                print("Pre-migration backup disabled by RELEASETRACKER_PRE_MIGRATION_BACKUP=0")
                return 0
            db_path = database_path()
            directory = os.environ.get("RELEASETRACKER_BACKUP_DIR") or db_path.parent / "backups"
            migrations = (
                os.environ.get("DBMATE_MIGRATIONS_DIR") or backend_dir() / "dbmate" / "migrations"
            )
            archive = pre_migration_backup(db_path, system_secrets_path(), directory, migrations)
            print(f"Pre-migration backup: {archive}" if archive else "No pending migrations")
            return 0
        if args.command == "reset-admin-password":
            asyncio.run(_reset_admin_password())
            print("Administrator password reset complete; existing sessions were revoked.")
            return 0
    except (ValueError, OSError, KeyError, sqlite3.Error, zipfile.BadZipFile) as exc:
        parser.exit(1, f"error: {exc}\n")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
