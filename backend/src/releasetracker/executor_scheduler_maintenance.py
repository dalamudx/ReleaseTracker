from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from .config import ExecutorConfig

logger = logging.getLogger("releasetracker.executor_scheduler")

_RELEASE_HISTORY_CLEANUP_RETRY_SECONDS = 60
_RELEASE_HISTORY_CLEANUP_SEGMENT_LOOKAHEAD_DAYS = 14
SYSTEM_TIMEZONE_SETTING_KEY = "system.timezone"


@dataclass(frozen=True)
class _CleanupSegment:
    start_at: datetime
    end_at: datetime
    executor_ids: frozenset[int]

    @property
    def key(self) -> str:
        return f"{self.start_at.isoformat()}__{self.end_at.isoformat()}"


class ExecutorSchedulerMaintenance:
    """Coordinate release-history cleanup windows for executor maintenance."""

    async def _refresh_system_timezone(self) -> None:
        try:
            timezone_value = await self.storage.get_setting(SYSTEM_TIMEZONE_SETTING_KEY)
            if isinstance(timezone_value, str) and timezone_value.strip():
                ZoneInfo(timezone_value.strip())
                self._system_timezone = timezone_value.strip()
                return
        except Exception as exc:
            logger.warning("Failed to load system timezone, falling back to UTC: %s", exc)
        self._system_timezone = "UTC"

    def _cleanup_timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self._system_timezone)
        except Exception:
            return ZoneInfo("UTC")

    async def refresh_release_history_cleanup_schedule(
        self,
        executor_configs: list[ExecutorConfig] | None = None,
    ) -> None:
        await self._refresh_system_timezone()
        configs = executor_configs or await self.storage.get_all_executor_configs()
        self.scheduler_host.remove_jobs_by_namespace(self._release_history_cleanup_job_namespace)
        segments = self._collect_maintenance_cleanup_segments(configs)
        if not segments:
            self.scheduler_host.add_cron_job(
                self._release_history_cleanup_job_namespace,
                "default_0200",
                self._run_default_release_history_cleanup,
                hour=2,
                minute=0,
                timezone=self._cleanup_timezone(),
            )
            return

        now = self._localized_now()
        scheduled_count = 0
        for segment in segments:
            if segment.end_at <= now:
                continue
            self.scheduler_host.add_date_job(
                self._release_history_cleanup_job_namespace,
                f"segment_{scheduled_count}_{abs(hash(segment.key))}",
                self._run_release_history_cleanup_for_segment,
                run_date=segment.end_at,
                args=[segment.key, sorted(segment.executor_ids)],
            )
            scheduled_count += 1
            if scheduled_count >= 32:
                break

    def _localized_now(self) -> datetime:
        now = self._now_provider()
        tz = self._cleanup_timezone()
        return now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)

    def _collect_maintenance_cleanup_segments(
        self,
        executor_configs: list[ExecutorConfig],
    ) -> list[_CleanupSegment]:
        tz = self._cleanup_timezone()
        now = self._localized_now()
        intervals: list[tuple[datetime, datetime, set[int]]] = []
        for executor_config in executor_configs:
            if executor_config.id is None:
                continue
            if not executor_config.enabled or executor_config.update_mode != "maintenance_window":
                continue
            window = executor_config.maintenance_window
            if window is None:
                continue
            start_time = _parse_time(window.start_time)
            end_time = _parse_time(window.end_time)
            if start_time is None or end_time is None:
                continue

            allowed_days = set(window.days_of_week) if window.days_of_week else set(range(7))
            for day_offset in range(_RELEASE_HISTORY_CLEANUP_SEGMENT_LOOKAHEAD_DAYS):
                candidate_date = (now + timedelta(days=day_offset)).date()
                if candidate_date.weekday() not in allowed_days:
                    continue
                start_at = datetime.combine(candidate_date, start_time, tzinfo=tz)
                end_at = datetime.combine(candidate_date, end_time, tzinfo=tz)
                if end_at <= start_at:
                    end_at += timedelta(days=1)
                if end_at <= now:
                    continue
                intervals.append((start_at, end_at, {executor_config.id}))

        if not intervals:
            return []

        intervals.sort(key=lambda item: item[0])
        merged: list[tuple[datetime, datetime, set[int]]] = []
        for start_at, end_at, executor_ids in intervals:
            if not merged or start_at > merged[-1][1]:
                merged.append((start_at, end_at, set(executor_ids)))
                continue
            previous_start, previous_end, previous_executor_ids = merged[-1]
            merged[-1] = (
                previous_start,
                max(previous_end, end_at),
                previous_executor_ids | executor_ids,
            )

        return [
            _CleanupSegment(start_at=start_at, end_at=end_at, executor_ids=frozenset(executor_ids))
            for start_at, end_at, executor_ids in merged
        ]

    async def _run_default_release_history_cleanup(self) -> None:
        await self._run_release_history_cleanup("default_0200")

    async def _run_release_history_cleanup_for_segment(
        self,
        segment_key: str,
        executor_ids: list[int],
    ) -> None:
        if segment_key in self._completed_cleanup_segment_keys:
            return
        async with self._running_executor_ids_lock:
            active_executor_ids = set(executor_ids) & self._running_executor_ids
        if active_executor_ids:
            self.scheduler_host.add_date_job(
                self._release_history_cleanup_job_namespace,
                f"retry_{uuid4().hex}",
                self._run_release_history_cleanup_for_segment,
                run_date=self._localized_now()
                + timedelta(seconds=_RELEASE_HISTORY_CLEANUP_RETRY_SECONDS),
                args=[segment_key, executor_ids],
            )
            return
        await self._run_release_history_cleanup(segment_key)
        self._completed_cleanup_segment_keys.add(segment_key)
        await self.refresh_release_history_cleanup_schedule()

    async def _run_release_history_cleanup(self, cleanup_key: str) -> None:
        if self._release_history_cleanup_lock.locked():
            return
        async with self._release_history_cleanup_lock:
            try:
                result = await self.storage.cleanup_release_history()
                logger.info("Release history cleanup %s completed: %s", cleanup_key, result)
            except Exception:
                logger.exception("Release history cleanup %s failed", cleanup_key)


def _parse_time(value: str) -> time | None:
    try:
        parts = value.split(":")
        if len(parts) != 2:
            return None
        hour = int(parts[0])
        minute = int(parts[1])
        return time(hour=hour, minute=minute)
    except Exception:
        return None
