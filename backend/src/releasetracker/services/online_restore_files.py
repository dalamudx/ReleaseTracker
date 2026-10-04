"""Durable paired switching. The caller must own the directory and close all DB users."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile

from .instance_backup import _sync_directory, restore_to_new_directory


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def regular(path):
    return stat.S_ISREG(path.lstat().st_mode)


def atomic_copy(source, target):
    if not regular(source) or target.is_symlink():
        raise ValueError("Unsafe restore path")
    descriptor, temporary = tempfile.mkstemp(prefix=".restore-write-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        _sync_directory(target.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=".restore-state-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare_archive(archive, directory):
    copy = directory / "archive.zip"
    atomic_copy(archive, copy)
    fingerprint = digest(copy)
    manifest = restore_to_new_directory(copy, directory / "restored")
    # Old fetch requests also belong to the discarded timeline. Future manual
    # fetches remain possible once an administrator reviews the restored state.
    with sqlite3.connect(directory / "restored" / "releases.db") as db:
        db.execute(
            "UPDATE tasks SET state='cancelled',owner=NULL,lease_until=NULL,error_code='restore_review_required' WHERE kind='fetch' AND state IN ('queued','retry_wait','running','needs_attention')"
        )
        db.execute(
            "UPDATE task_attempts SET state='interrupted',finished_at=strftime('%s','now'),error_code='restore_review_required' WHERE finished_at IS NULL"
        )
        db.commit()
    return fingerprint, manifest


class RestoreFiles:
    def __init__(self, db_path):
        self.db = Path(db_path).absolute()
        self.keys = self.db.parent / "system-secrets.json"
        self.root = self.db.parent / (".online-restore-" + self.db.name)
        self.receipt_file = self.root / "receipt.json"
        self.previous = self.root / "previous"
        self.safety = self.root / "safety.zip"
        self.owner = None
        self.directory_owner = None

    def acquire(self):
        import fcntl

        self.db.parent.mkdir(parents=True, exist_ok=True)
        if self.db.is_symlink() or self.keys.is_symlink() or self.root.is_symlink():
            raise ValueError("Online restoration requires regular data paths")
        self.root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)
        # Different db filenames in the same directory still share keys.
        # Keep the local lock too, for compatibility with an existing owner.
        shared = os.open(
            self.db.parent / ".releasetracker-data.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        descriptor = None
        try:
            fcntl.flock(shared, fcntl.LOCK_EX | fcntl.LOCK_NB)
            descriptor = os.open(
                self.root / "owner.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
            )
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            os.close(shared)
            raise
        self.directory_owner = shared
        self.owner = descriptor

    def release(self):
        if self.owner is not None:
            os.close(self.owner)
            self.owner = None
        if self.directory_owner is not None:
            os.close(self.directory_owner)
            self.directory_owner = None

    def load_receipt(self):
        if not self.receipt_file.exists():
            return None
        if not regular(self.receipt_file):
            raise ValueError("Unsafe restore journal")
        return json.loads(self.receipt_file.read_text())

    def persist(self, receipt):
        write_json(self.receipt_file, receipt)

    def save_original(self):
        # SQLite must have no remaining clients. Fail on a busy checkpoint;
        # never copy a db while leaving committed state in its old WAL.
        with sqlite3.connect(self.db) as db:
            if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                raise ValueError("Database is still in use")
        self.previous.mkdir(mode=0o700, exist_ok=True)
        for name, source in (("releases.db", self.db), ("system-secrets.json", self.keys)):
            atomic_copy(source, self.previous / name)
        write_json(
            self.previous / "checksums.json",
            {name: digest(self.previous / name) for name in ("releases.db", "system-secrets.json")},
        )

    def install(self, directory):
        # Durable journal is written BEFORE this first replacement. Recovery
        # can repair either interruption point using the original paired copy.
        for name, target in (("releases.db", self.db), ("system-secrets.json", self.keys)):
            atomic_copy(directory / name, target)
        for suffix in ("-wal", "-shm"):
            Path(str(self.db) + suffix).unlink(missing_ok=True)
        _sync_directory(self.db.parent)

    def rollback(self):
        hashes = json.loads((self.previous / "checksums.json").read_text())
        for name in ("releases.db", "system-secrets.json"):
            if digest(self.previous / name) != hashes.get(name):
                raise ValueError("Original recovery pair is damaged")
        self.install(self.previous)

    def recover(self):
        receipt = self.load_receipt()
        if receipt and receipt["state"] not in ("succeeded", "failed"):
            if receipt.get("original_saved") and receipt["phase"] in (
                "switching",
                "reloading",
                "rolling_back",
                "failed_closed",
            ):
                self.rollback()
                receipt["rolled_back"] = True
            receipt.update(state="failed", phase="finished", error_code="restore_interrupted")
            self.persist(receipt)
        # Prepared plans are never approved across a process restart.
        plans = self.root / "plans"
        if plans.exists():
            shutil.rmtree(plans)
        plans.mkdir(mode=0o700)
        return receipt
