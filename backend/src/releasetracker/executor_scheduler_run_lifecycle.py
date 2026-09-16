from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .config import ExecutorConfig
from .models import ExecutorRunHistory, ExecutorStatus
from .notifiers import SUPPORTED_NOTIFIER_TYPES, build_notifier
from .notifiers.base import NotificationEvent

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
        finished_at = self._now_provider()
        await self.storage.finalize_executor_run(
            run_id,
            status=status,
            from_version=from_version,
            finished_at=finished_at,
            to_version=to_version,
            message=message,
            diagnostics=diagnostics,
        )
        await self.storage.update_executor_status(
            ExecutorStatus(
                executor_id=executor_config.id,
                last_run_at=finished_at,
                last_result=status,
                last_error=last_error,
                last_version=to_version,
            )
        )
        await self._send_run_notifications(
            executor_config,
            run_id=run_id,
            status=status,
            from_version=from_version,
            to_version=to_version,
            message=message,
            finished_at=finished_at,
        )
        return ExecutorRunOutcome(
            status=status,
            from_version=from_version,
            to_version=to_version,
            message=message,
        )

    async def _send_run_notifications(
        self,
        executor_config: ExecutorConfig,
        *,
        run_id: int,
        status: str,
        from_version: str | None,
        to_version: str | None,
        message: str | None,
        finished_at: datetime,
    ) -> None:
        event_map = {
            "success": NotificationEvent.EXECUTOR_RUN_SUCCESS,
            "failed": NotificationEvent.EXECUTOR_RUN_FAILED,
            "skipped": NotificationEvent.EXECUTOR_RUN_SKIPPED,
        }
        event = event_map.get(status)
        if event is None:
            return

        try:
            db_notifiers = await self.storage.get_notifiers()
        except Exception as exc:
            logger.error(f"Failed to load executor notifiers from DB: {exc}")
            return

        active_notifiers = [
            build_notifier(
                notifier_type=item.type,
                name=item.name,
                url=item.url,
                events=item.events,
                language=item.language,
            )
            for item in db_notifiers
            if (item.enabled and item.type in SUPPORTED_NOTIFIER_TYPES and event in item.events)
        ]
        if not active_notifiers:
            return

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
            "started_at": None,
            "finished_at": _notification_timestamp(finished_at, self._system_timezone),
            "from_version": from_version,
            "to_version": to_version,
            "message": message,
        }

        run_record = await self.storage.get_executor_run(run_id)
        if run_record is not None:
            payload["started_at"] = _notification_timestamp(
                run_record.started_at, self._system_timezone
            )
            # Lift the persisted health_check and recovery_outcome
            # diagnostics into the notification payload so webhook
            # subscribers (Discord/Slack/plain HTTP) can show post-update
            # readiness without calling the API.
            diagnostics = run_record.diagnostics or {}
            health_check = diagnostics.get("health_check")
            if isinstance(health_check, dict):
                payload["health_check"] = health_check
            recovery_outcome = diagnostics.get("recovery_outcome")
            if isinstance(recovery_outcome, str):
                payload["recovery_outcome"] = recovery_outcome

        results = await asyncio.gather(
            *(notifier.notify(event, payload) for notifier in active_notifiers),
            return_exceptions=True,
        )
        for notifier, result in zip(active_notifiers, results, strict=True):
            if isinstance(result, BaseException):
                logger.error(
                    "Executor notifier delivery raised for %s: %s",
                    notifier.name,
                    result,
                )
            elif result is not True:
                logger.error("Executor notifier delivery failed: %s", notifier.name)


def _notification_timestamp(value: datetime, timezone_name: str) -> str:
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception:
        timezone = ZoneInfo("UTC")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone)
    return value.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
