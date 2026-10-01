"""Consistent database/key backups and non-destructive offline restoration.

Archives contain secrets and must be stored as credentials. Checksums detect
corruption, not malicious replacement; only restore trusted archives.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timezone
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
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone():
        return []
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


def _create_archive(db_path, keys, destination, *, reason="manual_or_scheduled"):
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
            "reason": reason,
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


def pending_migrations(db_path, migrations_dir):
    """Migration versions present in the image but not applied to this database."""
    available = {
        path.name.split("_", 1)[0]
        for path in Path(migrations_dir).glob("*.sql")
        if path.name.split("_", 1)[0].isdigit()
    }
    database = Path(db_path)
    if not database.is_file():
        return []  # Fresh installation: nothing to protect.
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        applied = set(_versions(db))
    return sorted(available - applied)


def pre_migration_backup(db_path, keys_path, directory, migrations_dir):
    """Create a restore point before schema changes; returns the archive or None."""
    pending = pending_migrations(db_path, migrations_dir)
    if not pending:
        return None
    keys_file = Path(keys_path)
    if not keys_file.is_file():
        raise ValueError("Database exists but system-secrets.json is missing; refusing to migrate")
    keys = keys_file.read_bytes()
    if len(keys) > MAX_KEY_BYTES:
        raise ValueError("Invalid key file size")
    _validate_keys(keys)
    return _create_archive(db_path, keys, directory, reason="pre_migration")


BACKUP_STATUS_SETTING = "system.instance_backup_status"


def _failure_code(error: BaseException) -> str:
    """Finite codes only; exception text may contain paths or key material."""
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, OSError):
        return "storage_error"
    if isinstance(error, ValueError):
        text = str(error)
        if "already running" in text:
            return "already_running"
        if "integrity" in text:
            return "integrity_failed"
        if "size limit" in text:
            return "too_large"
        if "rotation" in text or "key" in text.lower():
            return "key_unavailable"
        return "validation_failed"
    return "failed"


def verify_archive(archive, allow_older_schema=False) -> None:
    """Read the written ZIP back: checksums, SQLite integrity and decryption."""
    with tempfile.TemporaryDirectory(prefix=".verify-", dir=Path(archive).parent) as temporary:
        validate_archive(archive, temporary, allow_older_schema=allow_older_schema)


def retention_tiers():
    """Opt-in daily/weekly points in addition to the most recent N archives."""
    try:
        days = int(os.environ.get("RELEASETRACKER_BACKUP_DAILY_RETENTION", "0"))
        weeks = int(os.environ.get("RELEASETRACKER_BACKUP_WEEKLY_RETENTION", "0"))
    except ValueError as exc:
        raise ValueError("Backup retention tiers must be integers") from exc
    if not 0 <= days <= 90 or not 0 <= weeks <= 52:
        raise ValueError("Backup daily retention must be 0–90 and weekly retention 0–52")
    return days, weeks


def archive_created_at(path):
    """Generated IDs include creation nanoseconds; copying/touching is not a backup."""
    parts = path.stem.split("-")
    if (
        len(parts) == 3
        and parts[0] == "releasetracker"
        and parts[1].isdigit()
        and 18 <= len(parts[1]) <= 20
    ):
        return int(parts[1]) / 1e9
    return path.stat().st_mtime  # Compatibility with externally named legacy archives.


def retention_candidates(paths, retain, days, weeks, *, now=None):
    """Keep newest per UTC calendar bucket, never count symlinks as restore points."""
    now = time.time() if now is None else now
    archives = sorted(
        (p for p in paths if p.is_file() and not p.is_symlink()),
        key=lambda p: (archive_created_at(p), p.name),
        reverse=True,
    )
    keep = set(archives[:retain])
    daily, weekly = set(), set()
    for path in archives:
        stamp = archive_created_at(path)
        date = datetime.fromtimestamp(stamp, timezone.utc)
        day, week = date.date(), date.isocalendar()[:2]
        age = max(0, now - stamp)
        if days and age <= days * 86400 and day not in daily:
            keep.add(path)
            daily.add(day)
        if weeks and age <= weeks * 7 * 86400 and week not in weekly:
            keep.add(path)
            weekly.add(week)
    return [p for p in archives if p not in keep]


def _create_verified_archive(db_path, keys, destination):
    """An unverified ZIP is private and cannot displace a working restore point."""
    destination = Path(destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".unverified-", dir=destination) as temporary:
        archive = _create_archive(db_path, keys, temporary)
        verify_archive(archive)
        result = destination / archive.name
        os.replace(archive, result)
        _sync_directory(destination)
        return result


async def _finish_thread(function, *args):
    """Cancellation must not release the file lock while a native worker is active."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(worker)
            break
        except asyncio.CancelledError:
            cancelled = True
            if worker.cancelled():
                raise
    if cancelled:
        raise asyncio.CancelledError
    return result


class InstanceBackup:
    def __init__(self, storage, key_manager, directory=None):
        self.storage = storage
        self.key_manager = key_manager
        self.directory = Path(directory) if directory else Path(storage.db_path).parent / "backups"
        self.lock = asyncio.Lock()
        self.last_success = self.latest_archive_time() or 0.0
        self.failures = 0

    def latest_archive_time(self):
        """Newest complete archive time; persisted across restarts by the files."""
        try:
            times = [
                archive_created_at(path)
                for path in self.directory.glob("releasetracker-*.zip")
                if path.is_file() and not path.is_symlink()
            ]
        except OSError:
            return None
        return max(times, default=None)

    async def status(self):
        raw = await self.storage.get_setting(BACKUP_STATUS_SETTING)
        try:
            value = json.loads(raw) if raw else {}
        except ValueError:
            value = {}
        return value if isinstance(value, dict) else {}

    async def _record(self, **changes):
        await self.storage.set_setting(
            BACKUP_STATUS_SETTING, json.dumps((await self.status()) | changes)
        )

    async def create(self, *, retain=7, scheduled=False):
        if self.lock.locked():
            raise ValueError("A backup is already running")
        async with self.lock:
            try:
                days, weeks = retention_tiers()
                archive = await self._create(retain=retain)
                # No cleanup or success timestamp until ZIP, DB and keys verify.
                self.last_success = time.time()
                await self._record(
                    last_success_at=self.last_success,
                    last_verified_at=self.last_success,
                    consecutive_failures=0,
                    last_error_code=None,
                    last_failure_phase=None,
                )
                for old in retention_candidates(
                    self.directory.glob("releasetracker-*.zip"), retain, days, weeks
                ):
                    old.unlink()
                return archive
            except Exception as error:
                self.failures += 1
                await self._failed(_failure_code(error), scheduled=scheduled)
                raise

    async def verify_latest(self):
        """Re-read a stored restore point daily; never delete or rewrite it."""
        if self.lock.locked():
            return False  # A creation in progress will verify its own new archive.
        async with self.lock:
            archives = sorted(
                (
                    p
                    for p in self.directory.glob("releasetracker-*.zip")
                    if p.is_file() and not p.is_symlink()
                ),
                key=lambda p: (archive_created_at(p), p.name),
                reverse=True,
            )
            if not archives:
                return False
            try:
                await _finish_thread(verify_archive, archives[0], True)
                status = await self.status()
                changes = {"last_verified_at": time.time()}
                if status.get("last_failure_phase") == "verification":
                    changes.update(
                        consecutive_failures=0, last_error_code=None, last_failure_phase=None
                    )
                await self._record(**changes)
                return True
            except Exception as error:
                self.failures += 1
                await self._failed(_failure_code(error), scheduled=True, phase="verification")
                raise

    async def _failed(self, code, *, scheduled, phase="creation"):
        status = await self.status()
        failures = int(status.get("consecutive_failures") or 0) + 1
        await self._record(
            last_failure_at=time.time(),
            last_error_code=code,
            consecutive_failures=failures,
            last_failure_phase=(
                "creation"
                if status.get("last_failure_phase") == "creation"
                and status.get("consecutive_failures")
                else phase
            ),
        )
        if not scheduled:
            return  # The administrator saw the failure in the UI already.
        from .release_notification_outbox import enqueue_system_alert

        try:
            await enqueue_system_alert(
                self.storage,
                # One alert per failure streak; a success resets the streak.
                (
                    f"backup_failed:{status.get('last_success_at') or 0}:"
                    f"{phase}:{status.get('last_verified_at') or 0 if phase == 'verification' else 0}"
                ),
                {
                    "tracker_name": "ReleaseTracker",
                    "entity": "instance_backup",
                    "error": f"Scheduled backup failed ({code})",
                    "reason": code,
                    "consecutive_failures": failures,
                },
            )
        except Exception:
            pass  # Alerting must never mask the original backup failure.

    async def _create(self, *, retain):
        if not 1 <= retain <= 100:
            raise ValueError("Backup retention must be between 1 and 100")
        # Match the rotation lock order and keep both until native work ends.
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
                return await _finish_thread(
                    _create_verified_archive, self.storage.db_path, keys, self.directory
                )


def validate_archive(archive, directory, *, allow_older_schema=False):
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
        compatible = versions == supported or (
            allow_older_schema and bool(versions) and versions == supported[: len(versions)]
        )
        if versions != manifest.get("migrations") or not compatible:
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
        # A restored point must not resurrect already revoked browser/API
        # sessions or replay a stale OIDC callback. Preserve the original ZIP.
        with closing(sqlite3.connect(root / "releases.db")) as db:
            db.execute("DELETE FROM sessions")
            db.execute("DELETE FROM oauth_states")
            db.commit()
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
