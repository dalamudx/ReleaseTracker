"""Snapshot service: history listing, pruning, and redaction (Req 19, 21).

The module now covers:

- ``InFlightRollbackRegistry`` — Phase A in-memory set used by retention
  pruning to avoid deleting a snapshot that a running rollback relies on.
- ``SnapshotRedactor`` — recursive, deterministic redactor applied both
  at write time (in the scheduler capture path) and at read time (when
  listing or fetching snapshots through the API).
- ``SnapshotService`` — public facade: ``list_snapshots``, ``get_snapshot``,
  ``prune_after_insert``, and the redactor helper.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from ..models import ExecutorSnapshot
    from ..storage.sqlite import SQLiteStorage


logger = logging.getLogger(__name__)


# Fixed non-reversible marker the redactor substitutes for every secret
# value it identifies (Req 21.3). Keeping it a constant makes golden
# snapshots deterministic for testing (Req 21.5).
REDACTED_MARKER = "***REDACTED***"


# Dict key names whose values are replaced unconditionally, no matter how
# deeply nested. Lower-cased comparison.
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


# Case-insensitive suffix matches for env-var style naming conventions
# used by Portainer stacks (Req 21.1). Each suffix is matched against
# the full key / ``name`` field.
_SENSITIVE_SUFFIX_PATTERN = re.compile(
    r".*(?:_password|_token|_secret|_key|_api_key|_auth)$",
    re.IGNORECASE,
)


# Runtime-specific branches apply additional rules beyond the always-redact
# keys above: Kubernetes Secret fields, Portainer Env list entries, Helm
# ``.Values.*.secret=true`` markers, and runtime_connection embedded
# material. The runtime_type passed to ``redact`` selects the branch.


@dataclass
class InFlightRollbackRegistry:
    """Registry of snapshot ids currently consumed by a running rollback.

    Retention pruning (:meth:`SnapshotService.prune_after_insert`) excludes
    any ids registered here so that a rollback in progress cannot race
    with a concurrent capture and lose its source-of-truth snapshot
    (Req 16.3).
    """

    _ids: set[int] = field(default_factory=set)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def register(self, snapshot_id: int) -> None:
        async with self._lock:
            self._ids.add(snapshot_id)

    async def unregister(self, snapshot_id: int) -> None:
        async with self._lock:
            self._ids.discard(snapshot_id)

    async def snapshot_ids(self) -> set[int]:
        """Return a copy of the registered ids at call time."""
        async with self._lock:
            return set(self._ids)


@dataclass(frozen=True)
class SnapshotListItemView:
    """Thin DTO used by list endpoints and UI tables."""

    id: int
    created_at: datetime
    trigger: str
    image_at_capture: str | None
    executor_run_id: int | None
    unredacted_persisted: bool


@dataclass(frozen=True)
class SnapshotDetailView(SnapshotListItemView):
    """List item + the redacted ``snapshot_data`` payload."""

    snapshot_data: dict[str, Any]


@dataclass(frozen=True)
class PaginatedSnapshotsView:
    items: list[SnapshotListItemView]
    total: int
    page: int
    page_size: int


class SnapshotRedactor:
    """Deterministic redactor applied to ``snapshot_data`` payloads.

    Write-time use (in the scheduler / adapter capture path) substitutes
    secrets in-place so persisted rows carry no plaintext material.
    Read-time use (in :meth:`SnapshotService.get_snapshot`) protects
    against older rows captured before the write-time hook landed; those
    rows also get the ``unredacted_persisted=true`` marker at write time
    so operators can be warned.
    """

    def redact(
        self,
        snapshot_data: Any,
        *,
        runtime_type: str | None = None,
    ) -> tuple[Any, bool]:
        """Return ``(redacted_payload, needs_unredacted_marker)``.

        ``needs_unredacted_marker`` is set to True when the adapter is
        one that currently cannot exhaustively identify secrets at
        persistence time (e.g., Helm rendered values). Phase E leaves
        the flag conservatively False because the built-in patterns
        cover the common cases; follow-up work may flip specific
        runtimes once golden fixtures validate them.
        """
        if snapshot_data is None:
            return None, False

        needs_marker = False

        # Apply runtime-specific branches first so they can delete
        # entire sub-objects before the generic walk runs.
        if runtime_type == "portainer":
            snapshot_data = self._redact_portainer(snapshot_data)
        elif runtime_type == "kubernetes":
            snapshot_data = self._redact_kubernetes(snapshot_data)

        return self._walk(snapshot_data), needs_marker

    # ---- Generic walk ----------------------------------------------------

    def _walk(self, node: Any) -> Any:
        if isinstance(node, dict):
            redacted: dict[Any, Any] = {}
            for key, value in node.items():
                if isinstance(key, str) and self._is_sensitive_key(key):
                    redacted[key] = REDACTED_MARKER
                else:
                    redacted[key] = self._walk(value)
            return redacted
        if isinstance(node, list):
            return [self._walk(entry) for entry in node]
        return node

    @staticmethod
    def _is_sensitive_key(key: str) -> bool:
        lowered = key.lower()
        if lowered in _ALWAYS_REDACT_KEYS:
            return True
        return _SENSITIVE_SUFFIX_PATTERN.fullmatch(lowered) is not None

    # ---- Portainer stack -------------------------------------------------

    def _redact_portainer(self, snapshot_data: dict[str, Any]) -> dict[str, Any]:
        """Portainer stack snapshots carry Env lists with ``{name, value}`` pairs.

        We redact any entry whose ``name`` matches a sensitive suffix or
        exact name, and we drop any embedded ``runtime_connection`` block
        (it should never be persisted inside a snapshot; defense in depth).
        """
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

    # ---- Kubernetes -----------------------------------------------------

    def _redact_kubernetes(self, snapshot_data: dict[str, Any]) -> dict[str, Any]:
        """Kubernetes / Helm snapshots may include Secret objects and Helm
        values whose top-level ``.Values.*.secret`` signals sensitive data.
        """
        if not isinstance(snapshot_data, dict):
            return snapshot_data
        result = copy.deepcopy(snapshot_data)

        resources = result.get("resources")
        if isinstance(resources, list):
            for resource in resources:
                if isinstance(resource, dict) and resource.get("kind") == "Secret":
                    for key in ("data", "stringData"):
                        if isinstance(resource.get(key), dict):
                            resource[key] = {
                                name: REDACTED_MARKER for name in resource[key]
                            }

        helm_values = result.get("values")
        if isinstance(helm_values, dict):
            # Naive walk: any key named ``secret`` or where a sibling
            # ``secret: true`` appears triggers redaction of the sibling
            # values. Phase E's heuristic is deliberately conservative;
            # a richer .Values.*.secret parser can land when golden
            # fixtures validate it.
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


class SnapshotService:
    def __init__(
        self,
        storage: "SQLiteStorage",
        registry: InFlightRollbackRegistry | None = None,
        redactor: SnapshotRedactor | None = None,
    ) -> None:
        self._storage = storage
        self._registry = registry or InFlightRollbackRegistry()
        self._redactor = redactor or SnapshotRedactor()

    @property
    def registry(self) -> InFlightRollbackRegistry:
        return self._registry

    @property
    def redactor(self) -> SnapshotRedactor:
        return self._redactor

    async def prune_after_insert(
        self,
        executor_id: int,
        retention: int,
        exclude_ids: set[int] | None = None,
    ) -> list[int]:
        if retention < 1:
            logger.warning(
                "snapshot retention count %s is below minimum; skipping prune for executor %s",
                retention,
                executor_id,
            )
            return []

        excluded = (
            set(exclude_ids) if exclude_ids is not None else await self._registry.snapshot_ids()
        )

        snapshots = await self._storage.list_executor_snapshots(
            executor_id,
            limit=10_000,
            offset=0,
        )
        if len(snapshots) <= retention:
            return []

        overflow = snapshots[retention:]
        prune_ids = [s.id for s in overflow if s.id is not None and s.id not in excluded]
        if not prune_ids:
            return []

        deleted = await self._storage.delete_executor_snapshots(executor_id, prune_ids)
        if deleted:
            for snapshot_id in prune_ids:
                logger.info(
                    "pruned executor snapshot id=%s executor_id=%s",
                    snapshot_id,
                    executor_id,
                )
        return prune_ids

    # ---- Read APIs -------------------------------------------------------

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
        snapshot = await self._storage.get_executor_snapshot_by_id(
            executor_id, snapshot_id
        )
        if snapshot is None:
            return None
        redacted_payload, needs_marker = self._redactor.redact(
            snapshot.snapshot_data,
            runtime_type=runtime_type,
        )
        del needs_marker  # Read-time redaction never flips the persisted flag.
        return SnapshotDetailView(
            id=snapshot.id or 0,
            created_at=snapshot.created_at,
            trigger=snapshot.trigger,
            image_at_capture=snapshot.image_at_capture,
            executor_run_id=snapshot.executor_run_id,
            unredacted_persisted=snapshot.unredacted_persisted,
            snapshot_data=redacted_payload if isinstance(redacted_payload, dict) else {},
        )

    # ---- Helpers --------------------------------------------------------

    def _to_list_item(self, snapshot: "ExecutorSnapshot") -> SnapshotListItemView:
        return SnapshotListItemView(
            id=snapshot.id or 0,
            created_at=snapshot.created_at,
            trigger=snapshot.trigger,
            image_at_capture=snapshot.image_at_capture,
            executor_run_id=snapshot.executor_run_id,
            unredacted_persisted=snapshot.unredacted_persisted,
        )

    # ---- Write-time redaction helper ------------------------------------

    def redact_for_persist(
        self,
        snapshot_data: dict[str, Any],
        *,
        runtime_type: str,
    ) -> tuple[dict[str, Any], bool]:
        """Apply write-time redaction and return the flag for the row.

        The returned ``needs_unredacted_marker`` is stored on the
        ``executor_snapshots`` row as ``unredacted_persisted`` so the UI
        can warn operators that the adapter could not guarantee full
        redaction (Req 21.2).
        """
        redacted, needs_marker = self._redactor.redact(
            snapshot_data, runtime_type=runtime_type
        )
        if not isinstance(redacted, dict):
            redacted = {}
        return redacted, needs_marker


__all__ = [
    "InFlightRollbackRegistry",
    "PaginatedSnapshotsView",
    "REDACTED_MARKER",
    "SnapshotDetailView",
    "SnapshotListItemView",
    "SnapshotRedactor",
    "SnapshotService",
]
