from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import ExecutorConfig, MaintenanceWindowConfig
from .executor_scheduler_maintenance import _parse_time
from .executor_scheduler_run_lifecycle import (
    ACTIVE_EXECUTOR_RUN_STATUSES,
    ExecutorRunOutcome,
)
from .models import ExecutorDesiredState, ExecutorRunHistory

logger = logging.getLogger("releasetracker.executor_scheduler")

_DESIRED_STATE_CLAIM_BATCH_SIZE = 20
_DESIRED_STATE_CLAIM_LEASE_SECONDS = 300
_DESIRED_STATE_OVERLAP_RETRY_SECONDS = 30
_DESIRED_STATE_MANUAL_OR_DISABLED_RETRY_SECONDS = 300
_DESIRED_STATE_MAINTENANCE_WINDOW_RETRY_SECONDS = 300


class ExecutorSchedulerRunQueue:
    """Coordinate manual executor runs and desired-state queue consumption."""

    async def run_executor_now(self, executor_id: int) -> ExecutorRunOutcome:
        config = await self.storage.get_executor_config(executor_id)
        if not config:
            raise ValueError(f"Executor {executor_id} not found")
        expected_revision = await self._pending_desired_state_revision(executor_id)
        return await self._run_executor_with_overlap_guard(
            config,
            manual=True,
            desired_state_revision=expected_revision,
        )

    async def run_executor_now_async(self, executor_id: int) -> int:
        config = await self.storage.get_executor_config(executor_id)
        if not config:
            raise ValueError(f"Executor {executor_id} not found")
        expected_revision = await self._pending_desired_state_revision(executor_id)
        if not config.enabled:
            raise ValueError(f"Executor {executor_id} is disabled")
        if not await self._try_acquire_executor_run(executor_id):
            raise ValueError(f"Executor {executor_id} is already running")

        run_id: int | None = None
        try:
            run_id = await self._claim_executor_run(executor_id, trigger="manual")
            if run_id is None:
                raise ValueError(f"Executor {executor_id} is already running")

            async def _background():
                try:
                    await self.storage.set_executor_run_status(run_id, "running")
                    outcome = await self._execute_executor(config, manual=True, _run_id=run_id)
                    await self._complete_pending_desired_state_after_manual_run(
                        executor_id,
                        outcome,
                        expected_revision=expected_revision,
                    )
                except BaseException:
                    await self._finalize_interrupted_run(
                        config, run_id, "manual executor run interrupted"
                    )
                    raise
                finally:
                    await self._release_executor_run(executor_id)

            self._track_background_task(_background())
            return run_id
        except BaseException:
            if run_id is not None:
                await self._finalize_interrupted_run(
                    config, run_id, "manual executor run interrupted"
                )
            await self._release_executor_run(executor_id)
            raise

    async def _try_acquire_executor_run(self, executor_id: int) -> bool:
        async with self._running_executor_ids_lock:
            if executor_id in self._running_executor_ids:
                return False
            self._running_executor_ids.add(executor_id)
            return True

    async def _release_executor_run(self, executor_id: int) -> None:
        async with self._running_executor_ids_lock:
            self._running_executor_ids.discard(executor_id)

    async def _claim_executor_run(self, executor_id: int, *, trigger: str) -> int | None:
        return await self.storage.create_executor_run_if_no_active(
            ExecutorRunHistory(
                executor_id=executor_id,
                started_at=self._now_provider(),
                status="queued",
                diagnostics={"run_trigger": trigger},
            ),
            active_statuses=ACTIVE_EXECUTOR_RUN_STATUSES,
        )

    async def _finalize_interrupted_run(
        self,
        executor_config: ExecutorConfig,
        run_id: int,
        message: str,
    ) -> None:
        run = await self.storage.get_executor_run(run_id)
        if run is None or run.status not in ACTIVE_EXECUTOR_RUN_STATUSES:
            return
        diagnostics = dict(run.diagnostics or {})
        diagnostics["interrupted"] = True
        try:
            await self._finalize_run(
                executor_config,
                run_id,
                status="failed",
                to_version=run.to_version,
                message=message,
                last_error=message,
                from_version=run.from_version,
                diagnostics=diagnostics,
            )
        except Exception:
            logger.exception("Failed to finalize interrupted executor run %s", run_id)

    async def _run_executor_with_overlap_guard(
        self,
        executor_config: ExecutorConfig,
        *,
        manual: bool,
        run_id: int | None = None,
        desired_state_revision: str | None = None,
    ) -> ExecutorRunOutcome:
        if executor_config.id is None:
            raise ValueError("Executor config must have id")
        executor_id = executor_config.id
        if not await self._try_acquire_executor_run(executor_id):
            raise ValueError(f"Executor {executor_id} is already running")
        claimed_run_id = run_id
        try:
            if claimed_run_id is None:
                claimed_run_id = await self._claim_executor_run(
                    executor_id, trigger="manual" if manual else "automatic"
                )
                if claimed_run_id is None:
                    raise ValueError(f"Executor {executor_id} is already running")
            await self.storage.set_executor_run_status(claimed_run_id, "running")
            outcome = await self._execute_executor(
                executor_config, manual=manual, _run_id=claimed_run_id
            )
            if manual:
                await self._complete_pending_desired_state_after_manual_run(
                    executor_id,
                    outcome,
                    expected_revision=desired_state_revision,
                )
            return outcome
        except BaseException:
            if claimed_run_id is not None:
                await self._finalize_interrupted_run(
                    executor_config, claimed_run_id, "executor run interrupted"
                )
            raise
        finally:
            await self._release_executor_run(executor_id)

    async def _pending_desired_state_revision(self, executor_id: int) -> str | None:
        desired_state = await self.storage.get_executor_desired_state(executor_id)
        if desired_state is None or not desired_state.pending:
            return None
        return desired_state.desired_state_revision

    async def _complete_pending_desired_state_after_manual_run(
        self,
        executor_id: int,
        outcome: ExecutorRunOutcome,
        *,
        expected_revision: str | None,
    ) -> None:
        if outcome.status not in {"success", "skipped"} or expected_revision is None:
            return
        await self.storage.complete_executor_desired_state(
            executor_id,
            expected_revision=expected_revision,
        )

    async def _reconcile_pending_desired_states_tick(self) -> None:
        if self._desired_state_consume_lock.locked():
            return
        self._track_background_task(self.reconcile_pending_desired_states())

    async def reconcile_pending_desired_states(self) -> int:
        if self._desired_state_consume_lock.locked():
            return 0

        processed_count = 0
        async with self._desired_state_consume_lock:
            while True:
                claimed_states = await self.storage.claim_pending_executor_desired_states(
                    claimed_by=self._desired_state_worker_id,
                    now=self._now_provider(),
                    limit=_DESIRED_STATE_CLAIM_BATCH_SIZE,
                    lease_seconds=_DESIRED_STATE_CLAIM_LEASE_SECONDS,
                )
                if not claimed_states:
                    break

                for desired_state in claimed_states:
                    await self._consume_claimed_desired_state(desired_state)
                    processed_count += 1

        return processed_count

    async def _defer_claimed_desired_state(
        self,
        *,
        executor_id: int,
        claimed_by: str,
        seconds: int,
    ) -> None:
        next_eligible_at = self._now_provider() + timedelta(seconds=max(1, seconds))
        deferred = await self.storage.defer_executor_desired_state(
            executor_id,
            next_eligible_at=next_eligible_at,
            claimed_by=claimed_by,
        )
        if not deferred:
            await self.storage.release_executor_desired_state_claim(
                executor_id,
                claimed_by=claimed_by,
            )

    def _seconds_until_next_maintenance_window(
        self,
        window: MaintenanceWindowConfig | None,
    ) -> int:
        if window is None:
            return _DESIRED_STATE_MAINTENANCE_WINDOW_RETRY_SECONDS

        start_time = _parse_time(window.start_time)
        if start_time is None or _parse_time(window.end_time) is None:
            return _DESIRED_STATE_MAINTENANCE_WINDOW_RETRY_SECONDS

        now = self._now_provider()
        try:
            tz = ZoneInfo(self._system_timezone)
        except Exception:
            tz = ZoneInfo("UTC")

        localized_now = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
        allowed_days = set(window.days_of_week) if window.days_of_week else set(range(7))

        for day_offset in range(15):
            candidate_date = (localized_now + timedelta(days=day_offset)).date()
            candidate_start = datetime.combine(candidate_date, start_time, tzinfo=tz)
            if candidate_start.weekday() not in allowed_days:
                continue
            if candidate_start <= localized_now:
                continue

            seconds = int((candidate_start - localized_now).total_seconds())
            if seconds > 0:
                return seconds

        return _DESIRED_STATE_MAINTENANCE_WINDOW_RETRY_SECONDS

    async def _consume_claimed_desired_state(self, desired_state: ExecutorDesiredState) -> None:
        claimed_by = desired_state.claimed_by
        if claimed_by is None:
            return

        executor_id = desired_state.executor_id
        executor_config = await self.storage.get_executor_config(executor_id)
        if executor_config is None:
            await self.storage.complete_executor_desired_state(
                executor_id,
                expected_revision=desired_state.desired_state_revision,
                claimed_by=claimed_by,
            )
            return
        if (not executor_config.enabled) or executor_config.update_mode == "manual":
            await self._defer_claimed_desired_state(
                executor_id=executor_id,
                claimed_by=claimed_by,
                seconds=_DESIRED_STATE_MANUAL_OR_DISABLED_RETRY_SECONDS,
            )
            return
        if executor_config.update_mode == "maintenance_window":
            await self._refresh_system_timezone()
        if (
            executor_config.update_mode == "maintenance_window"
            and not self._within_maintenance_window(executor_config.maintenance_window)
        ):
            await self._defer_claimed_desired_state(
                executor_id=executor_id,
                claimed_by=claimed_by,
                seconds=self._seconds_until_next_maintenance_window(
                    executor_config.maintenance_window,
                ),
            )
            return
        if not await self._try_acquire_executor_run(executor_id):
            await self._defer_claimed_desired_state(
                executor_id=executor_id,
                claimed_by=claimed_by,
                seconds=_DESIRED_STATE_OVERLAP_RETRY_SECONDS,
            )
            return

        run_id: int | None = None
        try:
            run_id = await self._claim_executor_run(executor_id, trigger="automatic")
            if run_id is None:
                await self._defer_claimed_desired_state(
                    executor_id=executor_id,
                    claimed_by=claimed_by,
                    seconds=_DESIRED_STATE_OVERLAP_RETRY_SECONDS,
                )
                return
            await self.storage.set_executor_run_status(run_id, "running")
            run_outcome = await self._execute_executor(
                executor_config, manual=False, _run_id=run_id
            )
            if (
                run_outcome.status == "skipped"
                and run_outcome.message == "outside maintenance window"
            ):
                await self._defer_claimed_desired_state(
                    executor_id=executor_id,
                    claimed_by=claimed_by,
                    seconds=self._seconds_until_next_maintenance_window(
                        executor_config.maintenance_window,
                    ),
                )
                return
            await self.storage.complete_executor_desired_state(
                executor_id,
                expected_revision=desired_state.desired_state_revision,
                claimed_by=claimed_by,
            )
        except BaseException as exc:
            if run_id is not None:
                await self._finalize_interrupted_run(
                    executor_config, run_id, "automatic executor run interrupted"
                )
            await self.storage.release_executor_desired_state_claim(
                executor_id,
                claimed_by=claimed_by,
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
            logger.exception("Failed to consume desired-state work for executor %s", executor_id)
        finally:
            await self._release_executor_run(executor_id)
