"""Snapshot service for history listing, pruning, and redaction.

Phase A implementation focuses on ``prune_after_insert`` and the in-flight
rollback registry so the executor scheduler can start writing multi-row
history today without waiting for the Phase E list/detail endpoints.
``list_snapshots``, ``get_snapshot``, and ``redact_snapshot_data`` are
scaffolded here with ``NotImplementedError`` so callers fail loudly before
Phase E lands.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from ..storage.sqlite import SQLiteStorage


logger = logging.getLogger(__name__)


@dataclass
class InFlightRollbackRegistry:
    """Thread-safe registry of snapshot ids currently being consumed by a
    running rollback operation.

    Retention pruning (:meth:`SnapshotService.prune_after_insert`) excludes
    any ids registered here so that a rollback in progress cannot race with a
    concurrent capture and lose its source-of-truth snapshot (Req 16.3).
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


class SnapshotService:
    """Operations over the executor snapshot history table.

    Phase A wiring uses this class solely for retention pruning. Phase E will
    fill in list/detail/redaction methods. Keeping them as declared stubs
    makes the dependency surface visible to callers today.
    """

    def __init__(
        self,
        storage: "SQLiteStorage",
        registry: InFlightRollbackRegistry | None = None,
    ) -> None:
        self._storage = storage
        self._registry = registry or InFlightRollbackRegistry()

    @property
    def registry(self) -> InFlightRollbackRegistry:
        return self._registry

    async def prune_after_insert(
        self,
        executor_id: int,
        retention: int,
        exclude_ids: set[int] | None = None,
    ) -> list[int]:
        """Prune snapshots beyond the retention cap, newest kept, oldest dropped.

        Args:
            executor_id: Executor whose snapshot history to prune.
            retention: Maximum number of snapshots to retain.
            exclude_ids: Snapshot ids to exclude from deletion (typically
                ids currently being consumed by an in-flight rollback). If
                ``None``, the current registry snapshot is used.

        Returns:
            List of deleted snapshot ids (empty when nothing was pruned).
        """
        if retention < 1:
            logger.warning(
                "snapshot retention count %s is below minimum; skipping prune for executor %s",
                retention,
                executor_id,
            )
            return []

        excluded = set(exclude_ids) if exclude_ids is not None else await self._registry.snapshot_ids()

        snapshots = await self._storage.list_executor_snapshots(
            executor_id,
            limit=10_000,  # snapshot counts are small; one read is cheap
            offset=0,
        )
        if len(snapshots) <= retention:
            return []

        # Snapshots are returned newest-first. Candidates to prune are the
        # tail beyond the retention cap; excluded ids skip deletion this pass.
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

    async def list_snapshots(
        self,
        executor_id: int,
        page: int,
        page_size: int,
    ) -> Any:
        raise NotImplementedError("list_snapshots lands in Phase E")

    async def get_snapshot(self, executor_id: int, snapshot_id: int) -> Any:
        raise NotImplementedError("get_snapshot lands in Phase E")

    def redact_snapshot_data(self, data: dict, *, runtime_type: str) -> tuple[dict, bool]:
        raise NotImplementedError("redact_snapshot_data lands in Phase E")
