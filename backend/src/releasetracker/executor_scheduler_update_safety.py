from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from .config import ExecutorConfig
from .executors import BaseRuntimeAdapter
from .executors.base import RuntimeUpdateResult
from .models import ExecutorSnapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _HealthCheckOutcome:
    """Structured outcome returned by ``_run_post_update_health_check``."""

    status: str
    message: str | None
    last_error: str | None
    diagnostics: dict[str, Any]


class ExecutorSchedulerUpdateSafety:
    """Protect executor updates with snapshots, health checks, and diagnostics."""

    async def _prune_snapshot_history(self, executor_id: int) -> None:
        """Apply retention pruning after a successful snapshot insert.

        Failures here never abort the run: snapshots exist purely for
        recovery, and losing a prune pass is fixable on the next run.
        """
        try:
            retention = await self.storage.get_executor_snapshot_retention_count()
            await self._snapshot_service.prune_after_insert(executor_id, retention)
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning(
                "snapshot retention prune failed for executor_id=%s: %s",
                executor_id,
                exc,
            )

    async def _run_post_update_health_check(
        self,
        executor_config: ExecutorConfig,
        adapter: BaseRuntimeAdapter,
        *,
        run_id: int,
        update_result: "RuntimeUpdateResult",
    ) -> "_HealthCheckOutcome | None":
        """Drive post-update health checks after a successful image update.

        Returns ``None`` when the phase is skipped (strategy=none), which
        is the caller's signal to finalize with pre-feature semantics.
        Otherwise returns a structured outcome the caller feeds into
        ``_finalize_run``.
        """
        profile = executor_config.health_check
        if profile is None or profile.strategy == "none":
            return None

        # Local imports keep the scheduler lazy-coupled to the health
        # check subsystem and avoid pulling httpx etc. until needed.
        from .executors.health_check.factory import ProbeFactory
        from .executors.health_check.runner import HealthCheckRunner
        from .executors.health_check.types import HealthCheckContext

        baseline = self._capture_update_phase_baseline(adapter, update_result)
        target_mode = executor_config.target_ref.get("mode", "container")
        probe = ProbeFactory().build(profile.strategy, target_mode)
        runner = HealthCheckRunner(probe)

        ctx = HealthCheckContext(
            executor_config=executor_config,
            adapter=adapter,
            run_id=run_id,
            update_phase_end_at=self._now_provider(),
            baseline=baseline,
        )

        try:
            hc_result = await runner.run(ctx)
        except asyncio.CancelledError:
            # Cancellation during post-update health checks finalizes the run
            # as failed; no rollback or snapshot recovery is invoked.
            return _HealthCheckOutcome(
                status="failed",
                message="health check cancelled",
                last_error="health check cancelled",
                diagnostics={
                    "health_check": {
                        "strategy": profile.strategy,
                        "outcome": "error",
                        "last_error": "cancelled",
                    }
                },
            )

        diagnostics = {"health_check": hc_result.to_dict()}
        health_last_error = hc_result.last_error

        if hc_result.outcome == "healthy":
            return _HealthCheckOutcome(
                status="success",
                message=update_result.message or "image updated; health check passed",
                last_error=None,
                diagnostics=diagnostics,
            )

        # Unhealthy path: health checks never trigger automatic rollback or
        # snapshot recovery. Operators can use the retained snapshots for a
        # manual rollback from the UI/API.
        failure_policy = profile.failure_policy
        message_prefix = "health_check_failed"
        if failure_policy == "mark_degraded":
            message_prefix = "degraded"

        message_body = (health_last_error or "health check failed")[:500]
        message = f"{message_prefix}: {message_body}"
        return _HealthCheckOutcome(
            status="failed",
            message=message,
            # Truncate last_error that flows into Executor_Status to 500
            # chars so the run's last_error stays bounded.
            last_error=(health_last_error or "health check failed")[:500],
            diagnostics=diagnostics,
        )

    def _capture_update_phase_baseline(
        self,
        adapter: BaseRuntimeAdapter,
        update_result: "RuntimeUpdateResult",
    ) -> dict[str, Any]:
        """Capture adapter-specific state at the end of the image update.

        The runtime-native probes (container restart count, Kubernetes
        ``metadata.generation``) need a reference point from the moment
        the update completed. Adapters opt into this by exposing a
        ``capture_health_baseline`` coroutine; otherwise we return an
        empty dict and the probe falls back to best-effort checks.
        """
        capture = getattr(adapter, "capture_health_baseline", None)
        if callable(capture):
            try:
                baseline = capture(update_result)
                if asyncio.iscoroutine(baseline):
                    # Keep this helper sync; adapters that return a
                    # coroutine are expected to be awaited by the caller
                    # in a future refactor. For now we simply drop the
                    # coroutine to avoid "coroutine was never awaited"
                    # warnings and fall back to an empty baseline.
                    baseline.close()
                    return {}
                if isinstance(baseline, dict):
                    return baseline
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.warning(
                    "capture_health_baseline raised on %s: %s",
                    adapter.__class__.__name__,
                    exc,
                )
        return {}

    async def _capture_pre_update_snapshot(
        self,
        executor_config: ExecutorConfig,
        adapter: BaseRuntimeAdapter,
        *,
        run_id: int,
        current_image: str,
    ) -> bool:
        snapshot_data = await adapter.capture_snapshot(
            executor_config.target_ref,
            current_image,
        )
        await adapter.validate_snapshot(executor_config.target_ref, snapshot_data)
        redacted_snapshot, unredacted_persisted = self._snapshot_service.redact_for_persist(
            snapshot_data,
            runtime_type=executor_config.runtime_type,
        )
        await self.storage.create_executor_snapshot(
            ExecutorSnapshot(
                executor_id=executor_config.id,
                snapshot_data=redacted_snapshot,
                trigger="pre_update",
                image_at_capture=current_image,
                executor_run_id=run_id,
                unredacted_persisted=unredacted_persisted,
            )
        )
        await self._prune_snapshot_history(executor_config.id)
        return True

    @staticmethod
    def _supports_persisted_full_config_snapshots(
        executor_config: ExecutorConfig,
    ) -> bool:
        """Persist full config snapshots for destructive recreate targets."""
        target_mode = executor_config.target_ref.get("mode", "container")
        if target_mode in {"container", "docker_compose"}:
            return executor_config.runtime_type in {"docker", "podman"}
        if target_mode == "portainer_stack":
            # Current Portainer stack updates use the declarative stack-file API,
            # so stack history is the source of truth rather than full runtime
            # recreate snapshots.
            return False
        return False

    @staticmethod
    def _compose_snapshot_image_summary(service_images: dict[str, str]) -> str:
        return "; ".join(
            f"{service}={image}"
            for service, image in sorted(service_images.items())
            if isinstance(image, str) and image.strip()
        )

    @staticmethod
    def _manual_rollback_diagnostics(*, snapshot_created: bool) -> dict[str, Any]:
        return {
            "manual_rollback_available": snapshot_created,
            "automatic_recovery": "disabled",
        }
