"""Local operator commands for ReleaseTracker."""

from __future__ import annotations

import argparse
import asyncio
import getpass
from pathlib import Path

from .services.auth import AuthService
from .services.system_keys import SystemKeyManager
from .storage.sqlite import SQLiteStorage


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[2]


async def _reset_admin_password() -> None:
    password = getpass.getpass("New administrator password: ")
    confirmation = getpass.getpass("Confirm new administrator password: ")
    if password != confirmation:
        raise ValueError("Administrator passwords do not match")

    data_dir = _backend_dir() / "data"
    key_manager = SystemKeyManager(data_dir / "system-secrets.json")
    await key_manager.initialize()
    storage = SQLiteStorage(str(data_dir / "releases.db"), system_key_manager=key_manager)
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
    args = parser.parse_args(argv)

    try:
        if args.command == "reset-admin-password":
            asyncio.run(_reset_admin_password())
            print("Administrator password reset complete; existing sessions were revoked.")
            return 0
    except ValueError as exc:
        parser.exit(1, f"error: {exc}\n")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
