"""Manual rollback orchestration (Req 18.*).

Phase E wiring: accepts a snapshot id (or defaults to the most recent
snapshot), rejects runs while the executor is active, captures a fresh
``pre_rollback`` snapshot so the rollback itself is reversible, then
calls ``adapter.recover_from_snapshot``. The resulting recovery_outcome
is persisted into the rollback run's diagnostics so the UI can surface
it consistently with Recovery Hook runs (Req 10.5, Req 18.4).

Rollback runs do not execute the Health Check Phase in the initial
version (Req 18.7); outcome is derived directly from the adapter's
``RuntimeUpdateResult``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from fastapi import HTTPException

from ..executors.health_check.recovery_hook import (
    RecoveryHookCoordinator,
    RecoveryOutcome,
)
from ..models import ExecutorRunHistory, ExecutorSnapshot

if TYPE_CHECKING:
    from ..config import ExecutorConfig
    from ..executors.base import BaseRuntimeAdapter
    from ..storage.sqlite import SQLiteStorage
    from .snapshot_service import SnapshotService


logger = logging.getLogger(__name__)


_ROLLBACK_ACTIVE_STATES = frozenset({"queued", "running", "health_checking"})


@dataclass(frozen=True)
class RollbackOutcome:
    run: ExecutorRunHistory
    recovery_outcome: RecoveryOutcome


class RollbackService:
    def __init__(
        self,
        storage: "SQLiteStorage",
        snapshot_service: "SnapshotService",
    ) -> None:
        self._storage = storage
        self._snapshot_service = snapshot_service

    async def rollback(
        self,
        *,
        executor_config: "ExecutorConfig",
        adapter: "BaseRuntimeAdapter",
        snapshot_id: int | None,
        actor: str | None,
    ) -> RollbackOutcome:
        if executor_config.id is None:
            raise HTTPException(status_code=400, detail="Executor id is required")

        executor_id = executor_config.id

        # 1. Resolve the snapshot (explicit id or most-recent fallback).
        snapshot = await self._resolve_snapshot(executor_id, snapshot_id)

        # 2. Refuse to start when the executor already has an active run.
        await self._reject_when_active(executor_id)

        # 3. Create the rollback run row.
        run = ExecutorRunHistory(
            executor_id=executor_id,
            started_at=datetime.now(),
            status="queued",
            from_version=None,
            to_version=snapshot.image_at_capture,
            message=f"manual rollback to snapshot {snapshot.id}",
            diagnostics={
                "run_trigger": "manual_rollback",
                "snapshot_id": snapshot.id,
                "actor": actor,
            },
        )
        run_id = await self._storage.create_executor_run(run)
        run.id = run_id

        await self._storage.set_executor_run_status(run_id, "running")

        # 4. Register the chosen snapshot so retention pruning cannot
        #    delete it while this rollback is in flight (Req 16.3).
        registry = self._snapshot_service.registry
        if snapshot.id is not None:
            await registry.register(snapshot.id)

        diagnostics: dict = dict(run.diagnostics or {})
        recovery_outcome: RecoveryOutcome = "failed"
        try:
            # 5. Capture a fresh ``pre_rollback`` snapshot so the
            #    rollback itself is reversible (Req 18.5).
            pre_rollback_captured = await self._capture_pre_rollback_snapshot(
                executor_config=executor_config,
                adapter=adapter,
                run_id=run_id,
                diagnostics=diagnostics,
            )
            if not pre_rollback_captured:
                recovery_outcome = "failed"
                return await self._finalize_failed(
                    run_id=run_id,
                    diagnostics=diagnostics,
                    message="pre-rollback snapshot capture failed",
                )

            # 6. Delegate the actual restore to the shared Recovery
            #    Hook so manual and automatic rollbacks share the same
            #    error taxonomy and timeout semantics.
            coordinator = RecoveryHookCoordinator(self._storage)
            recovery_outcome = await coordinator.recover(
                executor_id=executor_id,
                adapter=adapter,
                target_ref=executor_config.target_ref,
                budget_seconds=self._rollback_budget_seconds(executor_config),
            )
            diagnostics["recovery_outcome"] = recovery_outcome

            status = "success" if recovery_outcome == "succeeded" else "failed"
            message = (
                f"rollback to snapshot {snapshot.id} {recovery_outcome}"
            )
            finalized_run = await self._finalize_run(
                run_id=run_id,
                status=status,
                diagnostics=diagnostics,
                message=message,
                to_version=snapshot.image_at_capture,
            )
            return RollbackOutcome(run=finalized_run, recovery_outcome=recovery_outcome)
        finally:
            if snapshot.id is not None:
                await registry.unregister(snapshot.id)

    # ---- Helpers ---------------------------------------------------------

    async def _resolve_snapshot(
        self, executor_id: int, snapshot_id: int | None
    ) -> ExecutorSnapshot:
        if snapshot_id is None:
            snapshot = await self._storage.get_executor_snapshot(executor_id)
            if snapshot is None:
                raise HTTPException(
                    status_code=404,
                    detail="No snapshot available for this executor",
                )
            return snapshot

        snapshot = await self._storage.get_executor_snapshot_by_id(
            executor_id, snapshot_id
        )
        if snapshot is None:
            raise HTTPException(
                status_code=404,
                detail="Snapshot not found for this executor",
            )
        return snapshot

    async def _reject_when_active(self, executor_id: int) -> None:
        latest = await self._storage.get_latest_executor_run(executor_id)
        if latest is not None and latest.status in _ROLLBACK_ACTIVE_STATES:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Executor has an active run",
                    "active_run_id": latest.id,
                    "active_run_status": latest.status,
                },
            )

    async def _capture_pre_rollback_snapshot(
        self,
        *,
        executor_config: "ExecutorConfig",
        adapter: "BaseRuntimeAdapter",
        run_id: int,
        diagnostics: dict,
    ) -> bool:
        target_ref = executor_config.target_ref
        try:
            current_image = await adapter.get_current_image(target_ref)
        except Exception as exc:
            # Some runtimes (K8s workloads, Helm) don't support the
            # single-container ``get_current_image`` API. That is fine
            # — we still capture a snapshot with an empty image tag so
            # the rollback is reversible.
            logger.debug(
                "rollback could not resolve current image for executor_id=%s: %s",
                executor_config.id,
                exc,
            )
            current_image = ""

        try:
            snapshot_data = await adapter.capture_snapshot(target_ref, current_image)
            await adapter.validate_snapshot(target_ref, snapshot_data)
        except NotImplementedError as exc:
            diagnostics["pre_rollback_capture_error"] = (
                f"adapter does not support snapshot capture: {exc}"
            )
            return False
        except Exception as exc:
            diagnostics["pre_rollback_capture_error"] = str(exc)
            return False

        redacted, unredacted = self._snapshot_service.redact_for_persist(
            snapshot_data, runtime_type=executor_config.runtime_type
        )
        await self._storage.create_executor_snapshot(
            ExecutorSnapshot(
                executor_id=executor_config.id,
                snapshot_data=redacted,
                trigger="pre_rollback",
                image_at_capture=current_image or None,
                executor_run_id=run_id,
                unredacted_persisted=unredacted,
            )
        )

        try:
            retention = await self._storage.get_executor_snapshot_retention_count()
            # Exclude every currently-registered snapshot id (the one
            # we're rolling back to, plus anything another rollback
            # might be consuming in parallel).
            exclude = await self._snapshot_service.registry.snapshot_ids()
            await self._snapshot_service.prune_after_insert(
                executor_config.id, retention, exclude_ids=exclude
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "pre-rollback prune failed for executor_id=%s: %s",
                executor_config.id,
                exc,
            )
        return True

    async def _finalize_failed(
        self,
        *,
        run_id: int,
        diagnostics: dict,
        message: str,
    ) -> RollbackOutcome:
        run = await self._finalize_run(
            run_id=run_id,
            status="failed",
            diagnostics=diagnostics,
            message=message,
            to_version=None,
        )
        return RollbackOutcome(run=run, recovery_outcome="failed")

    async def _finalize_run(
        self,
        *,
        run_id: int,
        status: Literal["success", "failed"],
        diagnostics: dict,
        message: str,
        to_version: str | None,
    ) -> ExecutorRunHistory:
        finished_at = datetime.now()
        await self._storage.finalize_executor_run(
            run_id,
            status=status,
            from_version=None,
            finished_at=finished_at,
            to_version=to_version,
            message=message,
            diagnostics=diagnostics,
        )
        updated = await self._storage.get_executor_run(run_id)
        assert updated is not None
        return updated

    @staticmethod
    def _rollback_budget_seconds(executor_config: "ExecutorConfig") -> int:
        profile = executor_config.health_check
        if profile is not None and profile.probe_window_seconds > 0:
            return profile.probe_window_seconds
        # Sensible default when the executor has no health check
        # profile: matches the adapter's internal Portainer poll cap
        # so operators see consistent behaviour.
        return 120


__all__ = ["RollbackOutcome", "RollbackService"]
