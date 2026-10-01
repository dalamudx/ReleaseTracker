"""Consistent database/key backups and non-destructive offline restoration.

Archives contain secrets and must be stored as credentials. Checksums detect
corruption, not malicious replacement; only restore trusted archives.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import uuid
import zipfile

from .. import __version__
from ..paths import backend_dir
from .system_keys import SystemKeyManager

FORMAT = 1
MAX_DATABASE_BYTES = 2 * 1024**3
MAX_KEY_BYTES = 65536
MEMBERS = {"manifest.json", "releases.db", "system-secrets.json"}


def backup_options():
    try:
        hours = int(os.environ.get("RELEASETRACKER_BACKUP_INTERVAL_HOURS", "0"))
        retain = int(os.environ.get("RELEASETRACKER_BACKUP_RETENTION", "7"))
    except ValueError as exc:
        raise ValueError("Backup interval and retention must be integers") from exc
    if not 0 <= hours <= 168 or not 1 <= retain <= 100:
        raise ValueError("Backup interval must be 0–168 hours and retention 1–100")
    return hours, retain


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _versions(db):
    return sorted(str(row[0]) for row in db.execute("SELECT version FROM schema_migrations"))


def _validate_keys(raw):
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not all(
        isinstance(payload.get(key), str) for key in ("jwt_secret", "encryption_key")
    ):
        raise ValueError("Invalid key file")
    SystemKeyManager.validate_jwt_secret(payload["jwt_secret"])
    SystemKeyManager.validate_encryption_key(payload["encryption_key"])
    if payload.get("pending_encryption_key"):
        raise ValueError("Finish pending encryption key rotation before taking a backup")
    return payload


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _create_archive(db_path, keys, destination):
    destination = Path(destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = f"releasetracker-{time.time_ns()}-{uuid.uuid4().hex[:8]}.zip"
    with tempfile.TemporaryDirectory(prefix=".backup-", dir=destination) as temporary:
        root = Path(temporary)
        database = root / "releases.db"
        deadline = time.monotonic() + 120

        def progress(_status, _remaining, _total):
            if time.monotonic() > deadline:
                raise TimeoutError("Database backup exceeded 120 seconds")

        with closing(
            sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
        ) as source:
            with closing(sqlite3.connect(database)) as target:
                source.backup(target, pages=256, progress=progress)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("Database integrity check failed")
                migrations = _versions(target)
        database.chmod(0o600)
        if database.stat().st_size > MAX_DATABASE_BYTES:
            raise ValueError("Database exceeds backup size limit")
        (root / "system-secrets.json").write_bytes(keys)
        (root / "system-secrets.json").chmod(0o600)
        manifest = {
            "format": FORMAT,
            "version": __version__,
            "created_at": time.time(),
            "migrations": migrations,
            "sha256": {name: _digest(root / name) for name in MEMBERS - {"manifest.json"}},
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        archive = root / "archive.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for member in sorted(MEMBERS):
                output.write(root / member, member)
        archive.chmod(0o600)
        with archive.open("rb") as handle:
            os.fsync(handle.fileno())
        result = destination / name
        os.replace(archive, result)
        _sync_directory(destination)
    return result


class InstanceBackup:
    def __init__(self, storage, key_manager, directory=None):
        self.storage = storage
        self.key_manager = key_manager
        self.directory = Path(directory) if directory else Path(storage.db_path).parent / "backups"
        self.lock = asyncio.Lock()
        self.last_success = 0.0
        self.failures = 0

    async def create(self, *, retain=7):
        try:
            return await self._create(retain=retain)
        except Exception:
            self.failures += 1
            raise

    async def _create(self, *, retain):
        if not 1 <= retain <= 100:
            raise ValueError("Backup retention must be between 1 and 100")
        if self.lock.locked():
            raise ValueError("A backup is already running")
        async with self.lock:
            # Match the key rotation lock order. Ordinary database writes may
            # continue; SQLite's backup API produces a consistent snapshot.
            async with self.key_manager.lock:
                async with self.storage.encryption_rotation_lock:
                    keys = self.key_manager.secrets_path.read_bytes()
                    if len(keys) > MAX_KEY_BYTES:
                        raise ValueError("Invalid key file size")
                    payload = _validate_keys(keys)
                    if (
                        payload["encryption_key"] != self.key_manager.encryption_key
                        or payload["jwt_secret"] != self.key_manager.jwt_secret
                    ):
                        raise ValueError("Key file differs from active encryption key")
                    worker = asyncio.create_task(
                        asyncio.to_thread(
                            _create_archive, self.storage.db_path, keys, self.directory
                        )
                    )
                    cancelled = False
                    while True:
                        try:
                            result = await asyncio.shield(worker)
                            break
                        except asyncio.CancelledError:
                            # Repeated request cancellation must not release the
                            # key locks while the native backup thread is active.
                            cancelled = True
                            if worker.cancelled():
                                raise
                    if cancelled:
                        raise asyncio.CancelledError
            self.last_success = time.time()
            # Only complete archives from this namespace are eligible; never
            # prune on failure or follow links to unrelated files.
            backups = sorted(self.directory.glob("releasetracker-*.zip"), reverse=True)
            for old in backups[retain:]:
                if old.is_file() and not old.is_symlink():
                    old.unlink()
            return result


def validate_archive(archive, directory):
    """Extract only allowlisted regular files into a private staging directory."""
    root = Path(directory).resolve()
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) != 3 or {e.filename for e in entries} != MEMBERS:
            raise ValueError("Invalid backup members")
        for entry in entries:
            limit = MAX_DATABASE_BYTES if entry.filename == "releases.db" else MAX_KEY_BYTES
            mode = entry.external_attr >> 16
            if entry.file_size > limit or (mode & 0o170000) not in (0, 0o100000):
                raise ValueError("Invalid backup member size or type")
            with source.open(entry) as data, (root / entry.filename).open("xb") as target:
                shutil.copyfileobj(data, target, length=1024 * 1024)
            (root / entry.filename).chmod(0o600)
    manifest = json.loads((root / "manifest.json").read_text())
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ValueError("Unsupported backup format")
    if not isinstance(manifest.get("sha256"), dict):
        raise ValueError("Invalid backup checksums")
    for name in MEMBERS - {"manifest.json"}:
        if manifest["sha256"].get(name) != _digest(root / name):
            raise ValueError("Backup checksum mismatch")
    _validate_keys((root / "system-secrets.json").read_bytes())
    with closing(sqlite3.connect((root / "releases.db").as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup database integrity check failed")
        versions = _versions(db)
        supported = sorted(
            p.name.split("_", 1)[0] for p in (backend_dir() / "dbmate/migrations").glob("*.sql")
        )
        if versions != manifest.get("migrations") or versions != supported:
            raise ValueError(
                "Backup schema does not match this application; restore using the matching image"
            )
    asyncio.run(_validate_decryption(root))
    return manifest


async def _validate_decryption(root):
    from ..storage.sqlite import SQLiteStorage

    manager = SystemKeyManager(root / "system-secrets.json")
    await manager.initialize()
    storage = SQLiteStorage(str(root / "releases.db"), system_key_manager=manager)
    try:
        inventory = await storage.get_encryption_key_inventory()
        if inventory["undecryptable_count"]:
            raise ValueError("Backup keys cannot decrypt stored credentials or snapshots")
    finally:
        await storage.close()


def restore_to_new_directory(archive, destination):
    """Never replace live data; operator must stop the instance and switch mounts."""
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Restore destination must not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=destination.parent) as temporary:
        root = Path(temporary)
        manifest = validate_archive(archive, root)
        # Validation includes decryption with the archived keys.
        for file in root.iterdir():
            with file.open("rb") as handle:
                os.fsync(handle.fileno())
        # mkdir is an exclusive reservation: a concurrent restore cannot be
        # silently overwritten. Failures remove only our own new directory.
        destination.mkdir(mode=0o700)
        try:
            for name in MEMBERS:
                os.replace(root / name, destination / name)
            _sync_directory(destination)
            _sync_directory(destination.parent)
        except BaseException:
            shutil.rmtree(destination)
            raise
    return manifest
