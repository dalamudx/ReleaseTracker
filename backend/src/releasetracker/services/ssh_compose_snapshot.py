"""Strict encrypted SSH file snapshots, including key rotation and integrity checks."""

from __future__ import annotations

import json
from types import SimpleNamespace

from ..models import ExecutorSnapshot
from .snapshot_integrity import build_snapshot_integrity, verify_snapshot_integrity
from .ssh_transport import SSHOperationError

KIND = "ssh_compose_encrypted_v1"


async def save_snapshot(storage, executor_id, run_id, payload):
    async with storage._encryption_rotation_lock:
        # Do not use legacy _encrypt(): it permits plaintext fallback.
        encrypted = storage.fernet.encrypt(json.dumps(payload).encode()).decode()
        return await storage.create_executor_snapshot(
            ExecutorSnapshot(
                executor_id=executor_id,
                executor_run_id=run_id,
                trigger="pre_update",
                snapshot_data={"kind": KIND, "secret": encrypted},
                locked=True,
            )
        )


async def load_snapshot(storage, executor_id, snapshot_id):
    async with storage._encryption_rotation_lock:
        snapshot = await storage.get_executor_snapshot_by_id(executor_id, snapshot_id)
        if snapshot is None or snapshot.snapshot_data.get("kind") != KIND:
            raise SSHOperationError("snapshot", "snapshot_not_found_or_incompatible")
        try:
            if verify_snapshot_integrity(snapshot) != "verified":
                raise ValueError()
            # Never treat failed decryption as legacy plaintext.
            data = json.loads(storage.fernet.decrypt(snapshot.snapshot_data["secret"].encode()))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except Exception:
            raise SSHOperationError("snapshot", "snapshot_integrity_or_key_invalid") from None


async def _rows(storage, db):
    cursor = await db.execute(
        "SELECT id, snapshot_data, snapshot_format_version, snapshot_sha256, snapshot_size_bytes FROM executor_snapshots"
    )
    for row in await cursor.fetchall():
        payload = storage._load_json(row["snapshot_data"])
        if payload.get("kind") == KIND:
            yield row, payload


async def snapshot_inventory(storage, db):
    count = invalid = 0
    async for _, data in _rows(storage, db):
        count += 1
        try:
            storage.fernet.decrypt(data["secret"].encode())
        except Exception:
            invalid += 1
    return count, invalid


async def prepare_snapshot_rotation(storage, db, old_fernet, new_fernet):
    updates = []
    async for row, data in _rows(storage, db):
        try:
            snapshot = SimpleNamespace(**{**dict(row), "snapshot_data": data})
            if verify_snapshot_integrity(snapshot) != "verified":
                raise ValueError()
            data["secret"] = new_fernet.encrypt(
                old_fernet.decrypt(data["secret"].encode())
            ).decode()
            integrity = build_snapshot_integrity(data)
            updates.append((json.dumps(data), integrity.sha256, integrity.size_bytes, row["id"]))
        except Exception:
            raise ValueError("Cannot rotate invalid SSH Compose snapshot") from None
    return updates
