"""Manual rollback orchestration."""

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
    recovery_error: str | None = None


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

        snapshot = await self._resolve_snapshot(executor_id, snapshot_id)

        await self._reject_when_active(executor_id)

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

        registry = self._snapshot_service.registry
        if snapshot.id is not None:
            await registry.register(snapshot.id)

        diagnostics: dict = dict(run.diagnostics or {})
        recovery_outcome: RecoveryOutcome = "failed"
        try:
            pre_rollback_captured, from_version = await self._capture_pre_rollback_snapshot(
                executor_config=executor_config,
                adapter=adapter,
                run_id=run_id,
                diagnostics=diagnostics,
            )
            if not pre_rollback_captured:
                recovery_outcome = "failed"
                capture_error = diagnostics.get("pre_rollback_capture_error")
                capture_detail = (
                    capture_error if isinstance(capture_error, str) else None
                )
                base_message = "pre-rollback snapshot capture failed"
                if capture_detail:
                    base_message = f"{base_message}: {capture_detail}"
                return await self._finalize_failed(
                    run_id=run_id,
                    diagnostics=diagnostics,
                    message=base_message,
                    recovery_error=capture_detail,
                    from_version=from_version,
                )

            coordinator = RecoveryHookCoordinator(self._storage)
            recovery_result = await coordinator.recover_detailed(
                executor_id=executor_id,
                adapter=adapter,
                target_ref=executor_config.target_ref,
                budget_seconds=self._rollback_budget_seconds(executor_config),
                snapshot=snapshot,
            )
            recovery_outcome = recovery_result.outcome
            recovery_error = recovery_result.error
            diagnostics["recovery_outcome"] = recovery_outcome
            if recovery_error:
                diagnostics["recovery_error"] = recovery_error
            await self._refresh_container_target_ref(
                executor_config=executor_config,
                new_container_id=recovery_result.new_container_id,
            )

            status = "success" if recovery_outcome == "succeeded" else "failed"
            message = f"rollback to snapshot {snapshot.id} {recovery_outcome}"
            if status == "failed" and recovery_error:
                message = f"{message}: {recovery_error}"
            finalized_run = await self._finalize_run(
                run_id=run_id,
                status=status,
                diagnostics=diagnostics,
                message=message,
                from_version=from_version,
                to_version=snapshot.image_at_capture,
            )
            return RollbackOutcome(
                run=finalized_run,
                recovery_outcome=recovery_outcome,
                recovery_error=recovery_error,
            )
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
    ) -> tuple[bool, str | None]:
        target_ref = executor_config.target_ref
        try:
            current_image = await adapter.get_current_image(target_ref)
        except Exception as exc:
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
            return False, current_image or None
        except Exception as exc:
            diagnostics["pre_rollback_capture_error"] = str(exc)
            return False, current_image or None

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
        return True, current_image or None

    async def _refresh_container_target_ref(
        self,
        *,
        executor_config: "ExecutorConfig",
        new_container_id: str | None,
    ) -> None:
        if (
            not new_container_id
            or executor_config.id is None
            or executor_config.target_ref.get("mode", "container") != "container"
        ):
            return
        refreshed_ref = {
            **executor_config.target_ref,
            "container_id": new_container_id,
        }
        await self._storage.update_executor_target_ref(executor_config.id, refreshed_ref)

    async def _finalize_failed(
        self,
        *,
        run_id: int,
        diagnostics: dict,
        message: str,
        recovery_error: str | None = None,
        from_version: str | None = None,
    ) -> RollbackOutcome:
        run = await self._finalize_run(
            run_id=run_id,
            status="failed",
            diagnostics=diagnostics,
            message=message,
            from_version=from_version,
            to_version=None,
        )
        return RollbackOutcome(
            run=run,
            recovery_outcome="failed",
            recovery_error=recovery_error,
        )

    async def _finalize_run(
        self,
        *,
        run_id: int,
        status: Literal["success", "failed"],
        diagnostics: dict,
        message: str,
        from_version: str | None,
        to_version: str | None,
    ) -> ExecutorRunHistory:
        finished_at = datetime.now()
        await self._storage.finalize_executor_run(
            run_id,
            status=status,
            from_version=from_version,
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
        return 120


__all__ = ["RollbackOutcome", "RollbackService"]
