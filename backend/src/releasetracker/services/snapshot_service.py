"""Snapshot service: history listing, pruning, and redaction."""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from .snapshot_integrity import SnapshotIntegrityError, verify_snapshot_integrity

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from ..models import ExecutorSnapshot
    from ..storage.sqlite import SQLiteStorage


logger = logging.getLogger(__name__)


REDACTED_MARKER = "***REDACTED***"


_ALWAYS_REDACT_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "client_secret",
        "private_key",
        "authorization",
        "auth",
        "cookie",
        "set-cookie",
        "bearer",
    }
)


_SENSITIVE_SUFFIX_PATTERN = re.compile(
    r".*(?:_password|_token|_secret|_key|_api_key|_auth)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SnapshotListItemView:
    id: int
    created_at: datetime
    trigger: str
    image_at_capture: str | None
    executor_run_id: int | None
    unredacted_persisted: bool
    locked: bool
    integrity_status: Literal["verified", "legacy_unverified", "invalid"]
    snapshot_format_version: int | None
    snapshot_sha256: str | None
    snapshot_size_bytes: int | None


@dataclass(frozen=True)
class SnapshotDetailView(SnapshotListItemView):
    snapshot_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaginatedSnapshotsView:
    items: list[SnapshotListItemView]
    total: int
    page: int
    page_size: int


class SnapshotRedactor:
    """Deterministic redactor applied to ``snapshot_data`` payloads."""

    def redact(
        self,
        snapshot_data: Any,
        *,
        runtime_type: str | None = None,
    ) -> tuple[Any, bool]:
        if snapshot_data is None:
            return None, False

        needs_marker = False

        if runtime_type == "portainer":
            snapshot_data = self._redact_portainer(snapshot_data)
        elif runtime_type == "kubernetes":
            snapshot_data = self._redact_kubernetes(snapshot_data)

        return self._walk(snapshot_data), needs_marker

    def _walk(self, node: Any) -> Any:
        if isinstance(node, dict):
            redacted: dict[Any, Any] = {}
            for key, value in node.items():
                if isinstance(key, str) and self._is_sensitive_key(key):
                    redacted[key] = REDACTED_MARKER
                elif isinstance(key, str) and key.lower() in {
                    "env",
                    "environment",
                    "env_vars",
                    "environment_variables",
                }:
                    redacted[key] = self._redact_environment_list(value)
                else:
                    redacted[key] = self._walk(value)
            return redacted
        if isinstance(node, list):
            return [self._walk(entry) for entry in node]
        return node

    def _redact_environment_list(self, value: Any) -> Any:
        if not isinstance(value, list):
            return self._walk(value)
        result = []
        for entry in value:
            if not isinstance(entry, str) or "=" not in entry:
                result.append(self._walk(entry))
                continue
            name, raw_value = entry.split("=", 1)
            result.append(
                f"{name}={REDACTED_MARKER}"
                if self._is_sensitive_key(name)
                else f"{name}={raw_value}"
            )
        return result

    @staticmethod
    def _is_sensitive_key(key: str) -> bool:
        lowered = key.lower()
        if lowered in _ALWAYS_REDACT_KEYS:
            return True
        return _SENSITIVE_SUFFIX_PATTERN.fullmatch(lowered) is not None

    def _redact_portainer(self, snapshot_data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(snapshot_data, dict):
            return snapshot_data
        result = copy.deepcopy(snapshot_data)

        env = result.get("env")
        if isinstance(env, list):
            for entry in env:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                if not isinstance(name, str):
                    continue
                if self._is_sensitive_key(name):
                    if "value" in entry:
                        entry["value"] = REDACTED_MARKER

        result.pop("runtime_connection", None)
        return result

    def _redact_kubernetes(self, snapshot_data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(snapshot_data, dict):
            return snapshot_data
        result = copy.deepcopy(snapshot_data)

        resources = result.get("resources")
        if isinstance(resources, list):
            for resource in resources:
                if isinstance(resource, dict) and resource.get("kind") == "Secret":
                    for key in ("data", "stringData"):
                        if isinstance(resource.get(key), dict):
                            resource[key] = {name: REDACTED_MARKER for name in resource[key]}

        helm_values = result.get("values")
        if isinstance(helm_values, dict):
            self._redact_helm_values(helm_values)

        result.pop("runtime_connection", None)
        return result

    def _redact_helm_values(self, node: Any) -> None:
        if isinstance(node, dict):
            if node.get("secret") is True:
                for key in list(node.keys()):
                    if key == "secret":
                        continue
                    if isinstance(node[key], (dict, list)):
                        self._redact_helm_values(node[key])
                    else:
                        node[key] = REDACTED_MARKER
                return
            for value in node.values():
                self._redact_helm_values(value)
        elif isinstance(node, list):
            for entry in node:
                self._redact_helm_values(entry)


class SnapshotInUseError(RuntimeError):
    """Raised when a snapshot is being consumed by an in-flight rollback."""


class SnapshotLockedError(RuntimeError):
    """Raised when a locked snapshot is targeted for deletion."""


class SnapshotService:
    def __init__(
        self,
        storage: "SQLiteStorage",
        redactor: SnapshotRedactor | None = None,
    ) -> None:
        self._storage = storage
        self._redactor = redactor or SnapshotRedactor()

    @property
    def redactor(self) -> SnapshotRedactor:
        return self._redactor

    async def prune_after_insert(
        self,
        executor_id: int,
        retention: int,
    ) -> list[int]:
        if retention < 1:
            logger.warning(
                "snapshot retention count %s is below minimum; skipping prune for executor %s",
                retention,
                executor_id,
            )
            return []

        snapshots = await self._storage.list_executor_snapshots(
            executor_id,
            limit=10_000,
            offset=0,
        )
        if len(snapshots) <= retention:
            return []

        overflow = snapshots[retention:]
        prune_ids = [s.id for s in overflow if s.id is not None and not s.locked]
        if not prune_ids:
            return []

        deleted_count = await self._storage.delete_executor_snapshots(executor_id, prune_ids)
        if not deleted_count:
            return []
        deleted_ids = [
            snapshot_id
            for snapshot_id in prune_ids
            if await self._storage.get_executor_snapshot_by_id(executor_id, snapshot_id) is None
        ]
        for snapshot_id in deleted_ids:
            logger.info(
                "pruned executor snapshot id=%s executor_id=%s",
                snapshot_id,
                executor_id,
            )
        return deleted_ids

    async def list_snapshots(
        self,
        executor_id: int,
        *,
        page: int,
        page_size: int,
    ) -> PaginatedSnapshotsView:
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size

        snapshots = await self._storage.list_executor_snapshots(
            executor_id, limit=page_size, offset=offset
        )
        total = await self._storage.count_executor_snapshots(executor_id)
        items = [self._to_list_item(snapshot) for snapshot in snapshots]
        return PaginatedSnapshotsView(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_snapshot(
        self,
        executor_id: int,
        snapshot_id: int,
        *,
        runtime_type: str | None = None,
    ) -> SnapshotDetailView | None:
        snapshot = await self._storage.get_executor_snapshot_by_id(executor_id, snapshot_id)
        if snapshot is None:
            return None
        redacted_payload, needs_marker = self._redactor.redact(
            snapshot.snapshot_data,
            runtime_type=runtime_type,
        )
        del needs_marker
        return SnapshotDetailView(
            id=snapshot.id or 0,
            created_at=snapshot.created_at,
            trigger=snapshot.trigger,
            image_at_capture=snapshot.image_at_capture,
            executor_run_id=snapshot.executor_run_id,
            unredacted_persisted=snapshot.unredacted_persisted,
            locked=snapshot.locked,
            integrity_status=self._integrity_status(snapshot),
            snapshot_format_version=snapshot.snapshot_format_version,
            snapshot_sha256=snapshot.snapshot_sha256,
            snapshot_size_bytes=snapshot.snapshot_size_bytes,
            snapshot_data=redacted_payload if isinstance(redacted_payload, dict) else {},
        )

    async def delete_snapshot(self, executor_id: int, snapshot_id: int) -> bool:
        """Delete a snapshot scoped to an executor unless rollback is consuming it or it is locked."""
        snapshot = await self._storage.get_executor_snapshot_by_id(executor_id, snapshot_id)
        if snapshot is None:
            return False

        if snapshot.locked:
            raise SnapshotLockedError("Snapshot is locked and cannot be deleted")

        deleted = await self._storage.delete_executor_snapshots(executor_id, [snapshot_id])
        if deleted:
            return True

        current = await self._storage.get_executor_snapshot_by_id(executor_id, snapshot_id)
        if current is not None and current.locked:
            raise SnapshotLockedError("Snapshot became locked before it could be deleted")
        if await self._storage.is_executor_snapshot_claimed(
            executor_id=executor_id, snapshot_id=snapshot_id
        ):
            raise SnapshotInUseError("Snapshot is currently in use by a rollback")
        return False

    async def set_snapshot_locked(
        self, executor_id: int, snapshot_id: int, *, locked: bool
    ) -> bool:
        """Lock or unlock a snapshot. Returns True when found and updated."""
        return await self._storage.set_executor_snapshot_locked(
            executor_id, snapshot_id, locked=locked
        )

    def _to_list_item(self, snapshot: "ExecutorSnapshot") -> SnapshotListItemView:
        return SnapshotListItemView(
            id=snapshot.id or 0,
            created_at=snapshot.created_at,
            trigger=snapshot.trigger,
            image_at_capture=snapshot.image_at_capture,
            executor_run_id=snapshot.executor_run_id,
            unredacted_persisted=snapshot.unredacted_persisted,
            locked=snapshot.locked,
            integrity_status=self._integrity_status(snapshot),
            snapshot_format_version=snapshot.snapshot_format_version,
            snapshot_sha256=snapshot.snapshot_sha256,
            snapshot_size_bytes=snapshot.snapshot_size_bytes,
        )

    @staticmethod
    def _integrity_status(
        snapshot: "ExecutorSnapshot",
    ) -> Literal["verified", "legacy_unverified", "invalid"]:
        try:
            return verify_snapshot_integrity(snapshot)  # type: ignore[return-value]
        except SnapshotIntegrityError:
            return "invalid"

    def redact_for_persist(
        self,
        snapshot_data: dict[str, Any],
        *,
        runtime_type: str,
    ) -> tuple[dict[str, Any], bool]:
        redacted, needs_marker = self._redactor.redact(snapshot_data, runtime_type=runtime_type)
        if not isinstance(redacted, dict):
            redacted = {}
        return redacted, needs_marker


__all__ = [
    "PaginatedSnapshotsView",
    "REDACTED_MARKER",
    "SnapshotDetailView",
    "SnapshotInUseError",
    "SnapshotLockedError",
    "SnapshotListItemView",
    "SnapshotRedactor",
    "SnapshotService",
]
