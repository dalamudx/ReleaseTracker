"""Manual rollback orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from fastapi import HTTPException

from ..executor_scheduler_run_lifecycle import ACTIVE_EXECUTOR_RUN_STATUSES
from ..executors.health_check.recovery_hook import (
    RecoveryHookCoordinator,
    RecoveryOutcome,
)
from ..models import ExecutorRunHistory, ExecutorSnapshot
from .snapshot_integrity import SnapshotIntegrityError, verify_snapshot_integrity

if TYPE_CHECKING:
    from ..config import ExecutorConfig
    from ..executors.base import BaseRuntimeAdapter
    from ..storage.sqlite import SQLiteStorage
    from .snapshot_service import SnapshotService


logger = logging.getLogger(__name__)


_ROLLBACK_ACTIVE_STATES = ACTIVE_EXECUTOR_RUN_STATUSES


@dataclass(frozen=True)
class RollbackOutcome:
    run: ExecutorRunHistory
    recovery_outcome: RecoveryOutcome
    recovery_error: str | None = None


@dataclass(frozen=True)
class RollbackPreview:
    snapshot_id: int
    image_at_capture: str | None
    integrity_status: str
    snapshot_valid: bool
    validation_error: str | None = None


class RollbackService:
    def __init__(
        self,
        storage: "SQLiteStorage",
        snapshot_service: "SnapshotService",
    ) -> None:
        self._storage = storage
        self._snapshot_service = snapshot_service

    async def preview(
        self,
        *,
        executor_config: "ExecutorConfig",
        adapter: "BaseRuntimeAdapter",
        snapshot_id: int | None,
    ) -> RollbackPreview:
        """Validate a rollback candidate without a claim, run record, or runtime mutation."""
        if executor_config.id is None:
            raise HTTPException(status_code=400, detail="Executor id is required")
        snapshot = (
            await self._storage.get_executor_snapshot_by_id(executor_config.id, snapshot_id)
            if snapshot_id is not None
            else await self._storage.get_executor_snapshot(executor_config.id)
        )
        if snapshot is None or snapshot.id is None:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        try:
            integrity_status = verify_snapshot_integrity(snapshot)
            await adapter.validate_snapshot(executor_config.target_ref, snapshot.snapshot_data)
        except Exception as exc:
            return RollbackPreview(
                snapshot_id=snapshot.id,
                image_at_capture=snapshot.image_at_capture,
                integrity_status="invalid",
                snapshot_valid=False,
                validation_error=str(exc),
            )
        return RollbackPreview(
            snapshot_id=snapshot.id,
            image_at_capture=snapshot.image_at_capture,
            integrity_status=integrity_status,
            snapshot_valid=True,
        )

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

        run = ExecutorRunHistory(
            executor_id=executor_id,
            started_at=datetime.now(),
            status="queued",
            from_version=None,
            to_version=None,
            message="manual rollback queued",
            diagnostics={
                "run_trigger": "manual_rollback",
                "actor": actor,
            },
        )
        try:
            claim = await self._storage.claim_executor_snapshot_for_rollback(
                executor_id=executor_id,
                snapshot_id=snapshot_id,
                run=run,
                active_statuses=_ROLLBACK_ACTIVE_STATES,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if claim is None:
            await self._reject_when_active(executor_id)
            raise HTTPException(status_code=409, detail="Executor has an active run")

        snapshot, run_id = claim
        assert snapshot.id is not None
        run.id = run_id
        run.to_version = snapshot.image_at_capture
        run.message = f"manual rollback to snapshot {snapshot.id}"
        run.diagnostics = {**(run.diagnostics or {}), "snapshot_id": snapshot.id}

        diagnostics: dict = dict(run.diagnostics or {})
        recovery_outcome: RecoveryOutcome = "failed"
        from_version: str | None = None
        try:
            # Validate the requested historical snapshot before doing any work.
            # A missing live target is recoverable; malformed or tampered history is not.
            try:
                diagnostics["snapshot_integrity"] = verify_snapshot_integrity(snapshot)
                await adapter.validate_snapshot(executor_config.target_ref, snapshot.snapshot_data)
            except (SnapshotIntegrityError, Exception) as exc:
                diagnostics["snapshot_validation_error"] = str(exc)
                return await self._finalize_failed(
                    run_id=run_id,
                    diagnostics=diagnostics,
                    message=f"rollback snapshot validation failed: {exc}",
                    recovery_error=str(exc),
                    from_version=None,
                )

            await self._storage.set_executor_run_status(run_id, "running")
            (
                pre_rollback_captured,
                from_version,
                target_missing,
            ) = await self._capture_pre_rollback_snapshot(
                executor_config=executor_config,
                adapter=adapter,
                run_id=run_id,
                diagnostics=diagnostics,
            )
            if not pre_rollback_captured:
                recovery_outcome = "failed"
                capture_error = diagnostics.get("pre_rollback_capture_error")
                capture_detail = capture_error if isinstance(capture_error, str) else None
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
            if target_missing:
                diagnostics["pre_rollback_snapshot"] = "skipped: target is absent"

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
        except Exception as exc:
            diagnostics["rollback_error"] = str(exc)
            try:
                await self._finalize_run(
                    run_id=run_id,
                    status="failed",
                    diagnostics=diagnostics,
                    message=f"rollback to snapshot {snapshot.id} failed: {exc}",
                    from_version=from_version,
                    to_version=snapshot.image_at_capture,
                )
            except Exception:
                logger.exception("failed to finalize rollback run_id=%s after error", run_id)
            raise
        finally:
            try:
                released = await self._storage.release_executor_snapshot_claim(
                    snapshot_id=snapshot.id,
                    run_id=run_id,
                )
                if not released:
                    logger.warning(
                        "rollback snapshot claim was already absent for run_id=%s snapshot_id=%s",
                        run_id,
                        snapshot.id,
                    )
            except Exception:
                logger.exception(
                    "failed to release rollback snapshot claim for run_id=%s snapshot_id=%s",
                    run_id,
                    snapshot.id,
                )

    # ---- Helpers ---------------------------------------------------------

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
    ) -> tuple[bool, str | None, bool]:
        """Capture current state, except when the runtime confirms it is absent."""
        target_ref = executor_config.target_ref
        current_image = ""
        if adapter.supports_single_image_operations(target_ref):
            try:
                current_image = await adapter.get_current_image(target_ref)
            except Exception as exc:
                if adapter.is_target_missing_error(exc):
                    diagnostics["pre_rollback_snapshot_skipped"] = "target is absent"
                    return True, None, True
                diagnostics["pre_rollback_capture_error"] = str(exc)
                return False, None, False

        try:
            snapshot_data = await adapter.capture_snapshot(target_ref, current_image)
            await adapter.validate_snapshot(target_ref, snapshot_data)
        except NotImplementedError as exc:
            diagnostics["pre_rollback_capture_error"] = (
                f"adapter does not support snapshot capture: {exc}"
            )
            return False, current_image or None, False
        except Exception as exc:
            diagnostics["pre_rollback_capture_error"] = str(exc)
            return False, current_image or None, False

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
            await self._snapshot_service.prune_after_insert(executor_config.id, retention)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "pre-rollback prune failed for executor_id=%s: %s",
                executor_config.id,
                exc,
            )
        return True, current_image or None, False

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
