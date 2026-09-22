"""Encrypted snapshot lifecycle; legacy SSH envelopes remain compatible."""

from __future__ import annotations

import json
from types import SimpleNamespace

from ..models import ExecutorSnapshot
from .executor_snapshot_crypto import KIND as GENERIC_KIND, encrypt
from .snapshot_integrity import build_snapshot_integrity, verify_snapshot_integrity
from .ssh_transport import SSHOperationError

KIND = "ssh_compose_encrypted_v1"


async def save_snapshot(storage, executor_id, run_id, payload):
    async with storage._encryption_rotation_lock:
        encrypted = storage.fernet.encrypt(json.dumps(payload).encode()).decode()
        return await storage.create_executor_snapshot(
            ExecutorSnapshot(
                executor_id=executor_id,
                executor_run_id=run_id,
                trigger="pre_update",
                snapshot_data={"kind": KIND, "secret": encrypted},
                locked=True,
            ),
            _encryption_lock_held=True,
        )


async def load_snapshot(storage, executor_id, snapshot_id):
    async with storage._encryption_rotation_lock:
        snapshot = await storage.get_executor_snapshot_by_id(executor_id, snapshot_id)
        if snapshot is None or snapshot.snapshot_data.get("kind") != KIND:
            raise SSHOperationError("snapshot", "snapshot_not_found_or_incompatible")
        try:
            if verify_snapshot_integrity(snapshot) != "verified":
                raise ValueError()
            data = json.loads(storage.fernet.decrypt(snapshot.snapshot_data["secret"].encode()))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except Exception:
            raise SSHOperationError("snapshot", "snapshot_integrity_or_key_invalid") from None


async def _rows(storage, db):
    cursor = await db.execute(
        "SELECT id, snapshot_data, snapshot_format_version, snapshot_sha256, snapshot_size_bytes "
        "FROM executor_snapshots"
    )
    for row in await cursor.fetchall():
        # A malformed JSON row must never silently become an empty valid snapshot.
        payload = json.loads(row["snapshot_data"])
        if not isinstance(payload, dict):
            raise ValueError("Invalid executor snapshot object")
        yield row, payload


def _decrypt_generic(fernet, payload):
    value = json.loads(fernet.decrypt(payload["secret"].encode()))
    if not isinstance(value, dict):
        raise ValueError("Invalid executor snapshot plaintext")
    return value


async def snapshot_inventory(storage, db):
    counts = {"ssh_compose_snapshot": 0, "executor_snapshot": 0}
    invalid = 0
    async for row, data in _rows(storage, db):
        kind = data.get("kind")
        if kind not in {KIND, GENERIC_KIND}:
            continue
        counts["ssh_compose_snapshot" if kind == KIND else "executor_snapshot"] += 1
        try:
            logical = data if kind == KIND else _decrypt_generic(storage.fernet, data)
            verify_snapshot_integrity(SimpleNamespace(**{**dict(row), "snapshot_data": logical}))
            if kind == KIND:
                storage.fernet.decrypt(data["secret"].encode())
        except Exception:
            invalid += 1
    return {key: count for key, count in counts.items() if count}, invalid


async def prepare_snapshot_rotation(storage, db, old_fernet, new_fernet):
    updates = []
    counts = {}
    async for row, data in _rows(storage, db):
        try:
            kind = data.get("kind")
            logical = _decrypt_generic(old_fernet, data) if kind == GENERIC_KIND else data
            status = verify_snapshot_integrity(
                SimpleNamespace(**{**dict(row), "snapshot_data": logical})
            )
            if kind == KIND:
                if status != "verified":
                    raise ValueError()
                rotated = {
                    "kind": KIND,
                    "secret": new_fernet.encrypt(
                        old_fernet.decrypt(data["secret"].encode())
                    ).decode(),
                }
                integrity = build_snapshot_integrity(rotated)
                digest, size = integrity.sha256, integrity.size_bytes
                key = "ssh_compose_snapshot"
            else:
                rotated = encrypt(SimpleNamespace(fernet=new_fernet), logical)
                # Preserve provenance: rotation must not bless legacy or corrupted data.
                digest, size = row["snapshot_sha256"], row["snapshot_size_bytes"]
                key = "executor_snapshot"
            counts[key] = counts.get(key, 0) + 1
            updates.append((json.dumps(rotated), digest, size, row["id"]))
        except Exception:
            raise ValueError("Cannot rotate invalid executor snapshot") from None
    return updates, counts


async def migrate_legacy_snapshots(storage) -> int:
    """Encrypt old rows atomically without repairing or replacing integrity metadata."""
    # Lightweight lifespan fakes and external storage implementations may not
    # expose snapshot storage; the real SQLiteStorage always does.
    if not hasattr(storage, "encryption_rotation_lock") or not hasattr(storage, "_get_connection"):
        return 0
    async with storage.encryption_rotation_lock:
        db = await storage._get_connection()
        await db.execute("BEGIN IMMEDIATE")
        try:
            updates = []
            async for row, payload in _rows(storage, db):
                if payload.get("kind") in {KIND, GENERIC_KIND}:
                    continue
                # Preserve even invalid history for audit; reads still reject its hash.
                updates.append((json.dumps(encrypt(storage, payload)), row["id"]))
            await db.executemany(
                "UPDATE executor_snapshots SET snapshot_data=? WHERE id=?", updates
            )
            await db.commit()
            return len(updates)
        except BaseException:
            await db.rollback()
            raise
