from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .config import ExecutorConfig
from .models import ExecutorRunHistory, ExecutorStatus

logger = logging.getLogger(__name__)


ACTIVE_EXECUTOR_RUN_STATUSES = frozenset({"queued", "running", "health_checking"})


@dataclass(frozen=True)
class ExecutorRunOutcome:
    status: str
    from_version: str | None
    to_version: str | None
    message: str | None


class ExecutorSchedulerRunLifecycle:
    """Persist executor run outcomes and deliver their notifications."""

    async def _create_run_record(
        self,
        executor_config: ExecutorConfig,
        *,
        from_version: str | None,
        to_version: str | None,
        diagnostics: dict[str, Any] | None = None,
    ) -> int:
        if executor_config.id is None:
            raise ValueError("Executor config must have id")
        run = ExecutorRunHistory(
            executor_id=executor_config.id,
            started_at=self._now_provider(),
            status="queued",
            from_version=from_version,
            to_version=to_version,
            message=None,
            diagnostics=diagnostics,
        )
        return await self.storage.create_executor_run(run)

    async def _record_skipped(
        self,
        executor_config: ExecutorConfig,
        *,
        message: str,
        from_version: str | None = None,
        to_version: str | None = None,
        run_id: int | None = None,
    ) -> ExecutorRunOutcome:
        final_run_id = run_id or await self._create_run_record(
            executor_config,
            from_version=from_version,
            to_version=to_version,
        )
        return await self._finalize_run(
            executor_config,
            final_run_id,
            status="skipped",
            to_version=to_version,
            message=message,
            last_error=None,
            from_version=from_version,
        )

    async def _record_failed(
        self,
        executor_config: ExecutorConfig,
        message: str,
        *,
        from_version: str | None = None,
        to_version: str | None = None,
        run_id: int | None = None,
    ) -> ExecutorRunOutcome:
        final_run_id = run_id or await self._create_run_record(
            executor_config,
            from_version=from_version,
            to_version=to_version,
        )
        return await self._finalize_run(
            executor_config,
            final_run_id,
            status="failed",
            to_version=to_version,
            message=message,
            last_error=message,
            from_version=from_version,
        )

    async def _finalize_run(
        self,
        executor_config: ExecutorConfig,
        run_id: int,
        *,
        status: str,
        to_version: str | None,
        message: str | None,
        last_error: str | None,
        from_version: str | None,
        diagnostics: dict[str, Any] | None = None,
    ) -> ExecutorRunOutcome:
        if executor_config.id is None:
            raise ValueError("Executor config must have id")
        from .services.deployment_readiness_context import READINESS_FINALIZER

        handoff = READINESS_FINALIZER.get()
        # Partial/failed mutations retain the conservative uncertainty lock.
        changed = status == "success"
        if handoff is not None and changed:
            return await handoff(
                executor_config,
                run_id,
                status=status,
                from_version=from_version,
                to_version=to_version,
                message=message,
                last_error=last_error,
                diagnostics=diagnostics,
            )
        finished_at = self._now_provider()
        payload = {
            "entity": "executor_run",
            "executor_id": executor_config.id,
            "executor_name": executor_config.name,
            "tracker_name": executor_config.tracker_name,
            "tracker_source_id": executor_config.tracker_source_id,
            "runtime_type": executor_config.runtime_type,
            "target_mode": executor_config.target_ref.get("mode"),
            "run_id": run_id,
            "status": status,
            "finished_at": _notification_timestamp(finished_at, self._system_timezone),
            "from_version": from_version,
            "to_version": to_version,
            "services": (diagnostics or {}).get("services", []),
        }
        health = (diagnostics or {}).get("health_check")
        if isinstance(health, dict):
            payload["health_check"] = health
        # No notifier lookup or network call on the deployment path. A committed
        # result always has durable notification intent, even without a live worker.
        await self.storage.finalize_executor_run(
            run_id,
            status=status,
            from_version=from_version,
            finished_at=finished_at,
            to_version=to_version,
            message=message,
            diagnostics=diagnostics,
            executor_status=ExecutorStatus(
                executor_id=executor_config.id,
                last_run_at=finished_at,
                last_result=status,
                last_error=last_error,
                last_version=to_version,
            ),
            notification_intent={
                "payload": payload,
                "notify_health_result": executor_config.health_check.notify_result,
                "timezone_name": self._system_timezone,
            },
        )
        return ExecutorRunOutcome(
            status=status,
            from_version=from_version,
            to_version=to_version,
            message=message,
        )


def _notification_timestamp(value: datetime, timezone_name: str) -> str:
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception:
        timezone = ZoneInfo("UTC")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone)
    return value.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
