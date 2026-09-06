"""Deterministic integrity metadata for persisted executor snapshots."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

CURRENT_SNAPSHOT_FORMAT_VERSION = 1
MAX_EXECUTOR_SNAPSHOT_BYTES = 2 * 1024 * 1024


class SnapshotIntegrityError(ValueError):
    """Snapshot data is malformed, oversized, or differs from its recorded digest."""


@dataclass(frozen=True)
class SnapshotIntegrity:
    format_version: int
    sha256: str
    size_bytes: int


def canonical_snapshot_bytes(snapshot_data: dict[str, Any]) -> bytes:
    """Encode a snapshot deterministically before it is written or verified."""
    if not isinstance(snapshot_data, dict):
        raise SnapshotIntegrityError("snapshot data must be an object")
    try:
        encoded = json.dumps(
            snapshot_data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SnapshotIntegrityError("snapshot data is not JSON serializable") from exc
    if len(encoded) > MAX_EXECUTOR_SNAPSHOT_BYTES:
        raise SnapshotIntegrityError(
            f"snapshot exceeds {MAX_EXECUTOR_SNAPSHOT_BYTES} byte safety limit"
        )
    return encoded


def build_snapshot_integrity(snapshot_data: dict[str, Any]) -> SnapshotIntegrity:
    encoded = canonical_snapshot_bytes(snapshot_data)
    return SnapshotIntegrity(
        format_version=CURRENT_SNAPSHOT_FORMAT_VERSION,
        sha256=hashlib.sha256(encoded).hexdigest(),
        size_bytes=len(encoded),
    )


def verify_snapshot_integrity(snapshot: Any) -> str:
    """Return ``verified`` or ``legacy_unverified``; reject tampered snapshots."""
    format_version = getattr(snapshot, "snapshot_format_version", None)
    sha256 = getattr(snapshot, "snapshot_sha256", None)
    size_bytes = getattr(snapshot, "snapshot_size_bytes", None)
    if format_version is None and sha256 is None and size_bytes is None:
        return "legacy_unverified"
    if format_version != CURRENT_SNAPSHOT_FORMAT_VERSION:
        raise SnapshotIntegrityError(f"unsupported snapshot format version: {format_version!r}")
    if not isinstance(sha256, str) or len(sha256) != 64:
        raise SnapshotIntegrityError("snapshot integrity digest is invalid")
    if not isinstance(size_bytes, int) or size_bytes < 0:
        raise SnapshotIntegrityError("snapshot integrity size is invalid")

    integrity = build_snapshot_integrity(getattr(snapshot, "snapshot_data", None))
    if integrity.size_bytes != size_bytes:
        raise SnapshotIntegrityError("snapshot size does not match persisted integrity metadata")
    if not hmac.compare_digest(integrity.sha256, sha256):
        raise SnapshotIntegrityError("snapshot digest does not match persisted data")
    return "verified"
