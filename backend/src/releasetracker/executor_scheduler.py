from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from uuid import uuid4
from typing import Any, Callable, Coroutine
from zoneinfo import ZoneInfo

from .config import (
    EXECUTOR_BINDABLE_SOURCE_TYPES,
    ExecutorConfig,
    ExecutorServiceBinding,
    MaintenanceWindowConfig,
)
from .executors import (
    BaseRuntimeAdapter,
    DockerRuntimeAdapter,
    KubernetesRuntimeAdapter,
    PodmanRuntimeAdapter,
    PortainerRuntimeAdapter,
)
from .executors.base import RuntimeMutationError
from .executors.portainer import PortainerRequestTimeoutError
from .models import (
    ExecutorDesiredState,
    ExecutorRunHistory,
    ExecutorSnapshot,
    ExecutorStatus,
    Release,
    TrackerSource,
)
from .notifiers import WebhookNotifier
from .notifiers.base import NotificationEvent
from .scheduler_host import SchedulerHost
from .services.runtime_credentials import materialize_runtime_connection_credentials
from .storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)

_DOCKER_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_DESIRED_STATE_CONSUMER_INTERVAL_SECONDS = 30
_DESIRED_STATE_CLAIM_BATCH_SIZE = 20
_DESIRED_STATE_CLAIM_LEASE_SECONDS = 300
_DESIRED_STATE_OVERLAP_RETRY_SECONDS = 30
_DESIRED_STATE_MANUAL_OR_DISABLED_RETRY_SECONDS = 300
_DESIRED_STATE_MAINTENANCE_WINDOW_RETRY_SECONDS = 300
_RELEASE_HISTORY_CLEANUP_RETRY_SECONDS = 60
_RELEASE_HISTORY_CLEANUP_SEGMENT_LOOKAHEAD_DAYS = 14
SYSTEM_TIMEZONE_SETTING_KEY = "system.timezone"


@dataclass(frozen=True)
class ExecutorRunOutcome:
    status: str
    from_version: str | None
    to_version: str | None
    message: str | None


@dataclass(frozen=True)
class _ExecutorBindingRunContext:
    service: str | None
    tracker_source_id: int
    channel_name: str


@dataclass(frozen=True)
class _ExecutorBindingRunResult:
    service: str
    status: str
    from_version: str | None
    to_version: str | None
    message: str


@dataclass(frozen=True)
class _CleanupSegment:
    start_at: datetime
    end_at: datetime
    executor_ids: frozenset[int]

    @property
    def key(self) -> str:
        return f"{self.start_at.isoformat()}__{self.end_at.isoformat()}"


async def _resolve_tracker_binding_by_source_id_from_storage(
    storage: SQLiteStorage, tracker_source_id: int
) -> tuple[str, TrackerSource] | None:
    binding = await storage.get_executor_binding(tracker_source_id)
    if binding is None:
        return None
    aggregate_tracker, tracker_source = binding
    if tracker_source.source_type in EXECUTOR_BINDABLE_SOURCE_TYPES and tracker_source.enabled:
        return aggregate_tracker.name, tracker_source
    return None


async def _resolve_tracker_binding_from_storage(
    storage: SQLiteStorage, executor_config: ExecutorConfig
) -> tuple[str, TrackerSource] | None:
    if executor_config.tracker_source_id is None:
        return None
    return await _resolve_tracker_binding_by_source_id_from_storage(
        storage,
        executor_config.tracker_source_id,
    )


async def _load_bound_releases_from_storage(
    storage: SQLiteStorage,
    tracker_name: str,
    *,
    tracker_source_id: int | None,
    tracker_source_type: str | None,
) -> list[Release]:
    if tracker_source_id is None:
        aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
        if aggregate_tracker is None or aggregate_tracker.id is None:
            return []

        releases = await storage.get_tracker_current_releases(aggregate_tracker.id)
        for release in releases:
            release.tracker_name = tracker_name
        return releases

    observations = await storage.get_source_release_observations_by_source(tracker_source_id)
    if observations:
        return [
            Release(
                tracker_name=tracker_name,
                tracker_type=tracker_source_type or "container",
                name=observation.name,
                tag_name=observation.tag_name,
                version=observation.version,
                app_version=observation.app_version,
                chart_version=observation.chart_version,
                published_at=observation.published_at,
                url=observation.url,
                prerelease=observation.prerelease,
                body=observation.body,
                commit_sha=observation.commit_sha,
            )
            for observation in observations
        ]

    return []


async def _resolve_tracker_latest_version_from_storage(
    storage: SQLiteStorage,
    tracker_name: str,
    channel_name: str | None,
    *,
    tracker_source_id: int | None = None,
    tracker_source_type: str | None = None,
) -> str | None:
    target = await _resolve_tracker_latest_target_from_storage(
        storage,
        tracker_name,
        channel_name,
        tracker_source_id=tracker_source_id,
        tracker_source_type=tracker_source_type,
    )
    if target is None:
        return None
    version, _ = target
    return version


async def _resolve_tracker_latest_target_from_storage(
    storage: SQLiteStorage,
    tracker_name: str,
    channel_name: str | None,
    *,
    tracker_source_id: int | None = None,
    tracker_source_type: str | None = None,
) -> tuple[str, str | None] | None:
    releases = await _load_bound_releases_from_storage(
        storage,
        tracker_name,
        tracker_source_id=tracker_source_id,
        tracker_source_type=tracker_source_type,
    )
    if not releases:
        return None

    tracker_config = await storage.get_tracker_config(tracker_name)
    sort_mode = tracker_config.version_sort_mode if tracker_config else "published_at"

    scoped_channels = tracker_config.channels if tracker_config else []
    if tracker_source_id is not None:
        bound_source = await storage.get_tracker_source(tracker_source_id)
        if bound_source is not None and bound_source.release_channels:
            scoped_channels = bound_source.release_channels

    bound_channels = scoped_channels
    if channel_name:
        bound_channels = [ch for ch in scoped_channels if ch.name == channel_name and ch.enabled]
        if not bound_channels:
            return None

    best_release = storage.select_best_release(releases, bound_channels, sort_mode=sort_mode)
    if best_release is None:
        return None

    if tracker_source_type == "container":
        same_version_releases = [
            release for release in releases if release.version == best_release.version
        ]
        if bound_channels:
            same_version_releases = [
                release
                for release in same_version_releases
                if any(
                    SQLiteStorage._release_matches_channel(release, channel)
                    for channel in bound_channels
                )
            ]

        digest_candidates = [
            release
            for release in same_version_releases
            if _normalize_docker_digest(release.commit_sha) is not None
        ]
        if digest_candidates:
            best_release = max(
                digest_candidates,
                key=lambda release: SQLiteStorage._release_order_key(release, sort_mode),
            )

    return best_release.version, _normalize_docker_digest(best_release.commit_sha)


def _normalize_docker_digest(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if _DOCKER_DIGEST_PATTERN.fullmatch(normalized) is None:
        return None
    return normalized


def _target_identity_key(target_version: str, target_digest: str | None) -> str:
    return f"{target_version}@{_normalize_docker_digest(target_digest) or 'no_digest'}"


def _replace_image_tag_value(image: str, target_version: str) -> str:
    return _build_image_target_value(image, target_version=target_version, target_digest=None)


def _build_image_target_value(
    image: str,
    *,
    target_version: str,
    target_digest: str | None,
) -> str:
    if "@" in image:
        image = image.split("@", 1)[0]
    last_slash = image.rfind("/")
    last_colon = image.rfind(":")
    if last_colon > last_slash:
        image = image[:last_colon]

    if target_digest is not None:
        return f"{image}@{target_digest}"
    return f"{image}:{target_version}"


def _build_target_image_value(
    *,
    current_image: str,
    target_version: str,
    target_digest: str | None,
    executor_config: ExecutorConfig,
    tracker_source,
    tracker_source_type: str | None,
) -> str:
    resolved_digest = (
        _normalize_docker_digest(target_digest)
        if tracker_source_type == "container" and executor_config.image_reference_mode == "digest"
        else None
    )

    if executor_config.image_selection_mode == "use_tracker_image_and_tag":
        source_config = getattr(tracker_source, "source_config", {}) or {}
        base_image = source_config.get("image")
        if not isinstance(base_image, str) or not base_image.strip():
            raise ValueError("tracker source image is required for tracker image selection mode")
        return _build_image_target_value(
            base_image,
            target_version=target_version,
            target_digest=resolved_digest,
        )

    if executor_config.image_selection_mode == "replace_tag_on_current_image":
        return _build_image_target_value(
            current_image,
            target_version=target_version,
            target_digest=resolved_digest,
        )

    raise ValueError(f"unsupported image selection mode: {executor_config.image_selection_mode}")


class ExecutorScheduler:
    def __init__(
        self,
        storage: SQLiteStorage,
        *,
        scheduler_host: SchedulerHost | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.storage = storage
        self.scheduler_host = scheduler_host or SchedulerHost()
        self._job_namespace = "executor"
        self._now_provider = now_provider or datetime.now
        self._adapters: dict[int, BaseRuntimeAdapter] = {}
        self._running_executor_ids: set[int] = set()
        self._running_executor_ids_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._desired_state_consume_lock = asyncio.Lock()
        self._release_history_cleanup_lock = asyncio.Lock()
        self._desired_state_consumer_job_key = "desired_state_reconcile"
        self._release_history_cleanup_job_namespace = "release_history_cleanup"
        self._desired_state_worker_id = f"executor-scheduler:{id(self)}"
        self._system_timezone = "UTC"
        self._completed_cleanup_segment_keys: set[str] = set()

    def _track_background_task(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def shutdown(self) -> None:
        pending_tasks = [task for task in self._background_tasks if not task.done()]
        for task in pending_tasks:
            task.cancel()

        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

        self._background_tasks.clear()

    async def initialize(self) -> None:
        await self._refresh_system_timezone()
        executor_configs = await self.storage.get_all_executor_configs()
        for config in executor_configs:
            await self._add_or_update_executor_job(config)
        await self.refresh_release_history_cleanup_schedule(executor_configs)
        await self._refresh_notifiers()

    async def _refresh_notifiers(self) -> None:
        try:
            await self.storage.get_notifiers()
        except Exception as exc:
            logger.error(f"Failed to warm executor notifiers: {exc}")

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

    async def start(self) -> None:
        await self.scheduler_host.start()
        logger.info("Executor scheduler started")
        self.scheduler_host.add_interval_job(
            self._job_namespace,
            self._desired_state_consumer_job_key,
            self._reconcile_pending_desired_states_tick,
            seconds=_DESIRED_STATE_CONSUMER_INTERVAL_SECONDS,
        )
        self._track_background_task(self.reconcile_pending_desired_states())

    async def refresh_executor(self, executor_id: int) -> None:
        config = await self.storage.get_executor_config(executor_id)
        if config:
            await self._add_or_update_executor_job(config)
            await self._enqueue_current_projection_work_for_executor(config)
            await self.refresh_release_history_cleanup_schedule()
            self._track_background_task(self.reconcile_pending_desired_states())

    async def _enqueue_current_projection_work_for_executor(
        self,
        executor_config: ExecutorConfig,
    ) -> bool:
        if executor_config.id is None:
            return False
        if not executor_config.enabled or executor_config.update_mode == "manual":
            return False

        binding_contexts = self._build_executor_binding_contexts(executor_config)
        if not binding_contexts:
            return False

        for binding_context in binding_contexts:
            tracker_binding = await self._resolve_tracker_binding_by_source_id(
                binding_context.tracker_source_id,
            )
            if tracker_binding is None:
                continue

            aggregate_tracker_name, tracker_source = tracker_binding
            if tracker_source.id is None:
                continue

            target = await self._resolve_tracker_latest_target(
                aggregate_tracker_name,
                binding_context.channel_name,
                tracker_source_id=tracker_source.id,
                tracker_source_type=tracker_source.source_type,
            )
            if target is None:
                continue

            target_version, target_digest = target
            current_identity_key = _target_identity_key(target_version, target_digest)
            return await self.storage.enqueue_executor_projection_trigger_work(
                executor_id=executor_config.id,
                tracker_name=aggregate_tracker_name,
                previous_version=None,
                current_version=target_version,
                previous_identity_key=None,
                current_identity_key=current_identity_key,
            )

        return False

    async def remove_executor(self, executor_id: int) -> None:
        if executor_id in self._adapters:
            del self._adapters[executor_id]

        self.scheduler_host.remove_job(self._job_namespace, executor_id)
        await self.refresh_release_history_cleanup_schedule()

    async def check_all(self) -> None:
        await self.reconcile_pending_desired_states()

    async def run_executor_now(self, executor_id: int) -> ExecutorRunOutcome:
        config = await self.storage.get_executor_config(executor_id)
        if not config:
            raise ValueError(f"Executor {executor_id} not found")
        return await self._run_executor_with_overlap_guard(config, manual=True)

    async def run_executor_now_async(self, executor_id: int) -> int:
        config = await self.storage.get_executor_config(executor_id)
        if not config:
            raise ValueError(f"Executor {executor_id} not found")
        if not config.enabled:
            raise ValueError(f"Executor {executor_id} is disabled")
        if not await self._try_acquire_executor_run(executor_id):
            raise ValueError(f"Executor {executor_id} is already running")

        run = ExecutorRunHistory(
            executor_id=executor_id,
            started_at=self._now_provider(),
            status="queued",
        )
        run_id = await self.storage.create_executor_run(run)

        async def _background():
            try:
                await self.storage.set_executor_run_status(run_id, "running")
                outcome = await self._execute_executor(config, manual=True, _run_id=run_id)
                await self._complete_pending_desired_state_after_manual_run(executor_id, outcome)
            finally:
                await self._release_executor_run(executor_id)

        self._track_background_task(_background())
        return run_id

    async def _try_acquire_executor_run(self, executor_id: int) -> bool:
        async with self._running_executor_ids_lock:
            if executor_id in self._running_executor_ids:
                return False
            self._running_executor_ids.add(executor_id)
            return True

    async def _release_executor_run(self, executor_id: int) -> None:
        async with self._running_executor_ids_lock:
            self._running_executor_ids.discard(executor_id)

    async def _run_executor_with_overlap_guard(
        self,
        executor_config: ExecutorConfig,
        *,
        manual: bool,
        run_id: int | None = None,
    ) -> ExecutorRunOutcome:
        if executor_config.id is None:
            raise ValueError("Executor config must have id")
        executor_id = executor_config.id
        if not await self._try_acquire_executor_run(executor_id):
            raise ValueError(f"Executor {executor_id} is already running")
        try:
            outcome = await self._execute_executor(executor_config, manual=manual, _run_id=run_id)
            if manual:
                await self._complete_pending_desired_state_after_manual_run(executor_id, outcome)
            return outcome
        finally:
            await self._release_executor_run(executor_id)

    async def _complete_pending_desired_state_after_manual_run(
        self,
        executor_id: int,
        outcome: ExecutorRunOutcome,
    ) -> None:
        if outcome.status not in {"success", "skipped"}:
            return
        desired_state = await self.storage.get_executor_desired_state(executor_id)
        if desired_state is None or not desired_state.pending:
            return
        await self.storage.complete_executor_desired_state(executor_id)

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

        try:
            run_outcome = await self._execute_executor(executor_config, manual=False)
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
                claimed_by=claimed_by,
            )
        except Exception:
            logger.exception("Failed to consume desired-state work for executor %s", executor_id)
            await self.storage.release_executor_desired_state_claim(
                executor_id,
                claimed_by=claimed_by,
            )
        finally:
            await self._release_executor_run(executor_id)

    async def _add_or_update_executor_job(self, executor_config: ExecutorConfig) -> None:
        if executor_config.id is None:
            return

        self.scheduler_host.remove_job(self._job_namespace, executor_config.id)

    async def _execute_executor_by_id(self, executor_id: int) -> None:
        config = await self.storage.get_executor_config(executor_id)
        if not config:
            return
        if not await self._try_acquire_executor_run(executor_id):
            return
        try:
            await self._execute_executor(config, manual=False)
        finally:
            await self._release_executor_run(executor_id)

    async def _execute_executor(
        self, executor_config: ExecutorConfig, *, manual: bool, _run_id: int | None = None
    ) -> ExecutorRunOutcome:
        await self._refresh_system_timezone()
        target_mode = executor_config.target_ref.get("mode")
        if target_mode == "portainer_stack":
            return await self._execute_portainer_stack_executor(
                executor_config,
                manual=manual,
                _run_id=_run_id,
            )
        if target_mode == "docker_compose":
            return await self._execute_docker_compose_executor(
                executor_config,
                manual=manual,
                _run_id=_run_id,
            )
        if target_mode == "kubernetes_workload":
            return await self._execute_kubernetes_workload_executor(
                executor_config,
                manual=manual,
                _run_id=_run_id,
            )
        if target_mode == "helm_release":
            return await self._execute_helm_release_executor(
                executor_config,
                manual=manual,
                _run_id=_run_id,
            )

        if executor_config.id is None:
            raise ValueError("Executor config must have id")
        try:
            if not executor_config.enabled:
                return await self._record_skipped(
                    executor_config,
                    message="executor disabled",
                    run_id=_run_id,
                )

            if not manual:
                if executor_config.update_mode == "manual":
                    return await self._record_skipped(
                        executor_config, message="manual mode", run_id=_run_id
                    )

                if executor_config.update_mode == "maintenance_window":
                    if not self._within_maintenance_window(executor_config.maintenance_window):
                        return await self._record_skipped(
                            executor_config,
                            message="outside maintenance window",
                            run_id=_run_id,
                        )

            runtime_connection = await self.storage.get_runtime_connection(
                executor_config.runtime_connection_id
            )
            if not runtime_connection:
                return await self._record_failed(
                    executor_config, "runtime connection not found", run_id=_run_id
                )
            if not runtime_connection.enabled:
                return await self._record_failed(
                    executor_config, "runtime connection disabled", run_id=_run_id
                )

            try:
                runtime_connection = await materialize_runtime_connection_credentials(
                    self.storage,
                    runtime_connection,
                )
            except ValueError as exc:
                return await self._record_failed(
                    executor_config,
                    str(exc),
                    run_id=_run_id,
                )

            tracker_binding = await self._resolve_tracker_binding(executor_config)
            if tracker_binding is None:
                return await self._record_failed(
                    executor_config, "tracker source binding missing", run_id=_run_id
                )
            aggregate_tracker_name, tracker_source = tracker_binding

            tracker_config = await self.storage.get_tracker_config(aggregate_tracker_name)
            if not tracker_config:
                return await self._record_failed(
                    executor_config, "tracker config missing", run_id=_run_id
                )

            target = await self._resolve_tracker_latest_target(
                tracker_config.name,
                executor_config.channel_name,
                tracker_source_id=tracker_source.id,
                tracker_source_type=tracker_source.source_type,
            )
            if target is None:
                return await self._record_skipped(
                    executor_config, message="tracker has no versions", run_id=_run_id
                )
            target_version, target_digest = target

            adapter = self._get_adapter(executor_config.id, runtime_connection)
            try:
                await adapter.validate_target_ref(executor_config.target_ref)
            except Exception as exc:
                return await self._record_failed(
                    executor_config, f"invalid target ref: {exc}", run_id=_run_id
                )

            try:
                current_image = await adapter.get_current_image(executor_config.target_ref)
            except Exception as exc:
                return await self._record_failed(
                    executor_config,
                    f"failed to resolve current image: {exc}",
                    run_id=_run_id,
                )

            try:
                target_image = self._build_target_image(
                    current_image=current_image,
                    target_version=target_version,
                    target_digest=target_digest,
                    executor_config=executor_config,
                    tracker_source=tracker_source,
                    tracker_source_type=tracker_source.source_type,
                )
            except ValueError as exc:
                return await self._record_failed(
                    executor_config,
                    str(exc),
                    run_id=_run_id,
                )

            if current_image == target_image:
                run_id = (
                    _run_id
                    if _run_id is not None
                    else await self._create_run_record(
                        executor_config,
                        from_version=current_image,
                        to_version=target_image,
                    )
                )
                return await self._finalize_run(
                    executor_config,
                    run_id,
                    status="skipped",
                    to_version=target_image,
                    message="runtime already at target image",
                    last_error=None,
                    from_version=current_image,
                )

            run_id = (
                _run_id
                if _run_id is not None
                else await self._create_run_record(
                    executor_config,
                    from_version=current_image,
                    to_version=target_image,
                )
            )

            try:
                snapshot_data = await adapter.capture_snapshot(
                    executor_config.target_ref, current_image
                )
                await adapter.validate_snapshot(executor_config.target_ref, snapshot_data)
                await self.storage.save_executor_snapshot(
                    ExecutorSnapshot(
                        executor_id=executor_config.id,
                        snapshot_data=snapshot_data,
                    )
                )
                result = await adapter.update_image(executor_config.target_ref, target_image)
                if not result.updated:
                    return await self._finalize_run(
                        executor_config,
                        run_id,
                        status="skipped",
                        to_version=target_image,
                        message="runtime already at target image",
                        last_error=None,
                        from_version=current_image,
                    )

                target_mode = executor_config.target_ref.get("mode", "container")
                if (
                    result.new_container_id
                    and executor_config.id is not None
                    and target_mode == "container"
                ):
                    refreshed_ref = {
                        **executor_config.target_ref,
                        "container_id": result.new_container_id,
                    }
                    await self.storage.update_executor_target_ref(executor_config.id, refreshed_ref)
                    executor_config = executor_config.model_copy(
                        update={"target_ref": refreshed_ref}
                    )

                return await self._finalize_run(
                    executor_config,
                    run_id,
                    status="success",
                    to_version=result.new_image or target_image,
                    message=result.message or "image updated",
                    last_error=None,
                    from_version=current_image,
                )
            except RuntimeMutationError as exc:
                return await self._recover_after_mutation_failure(
                    executor_config,
                    adapter,
                    run_id=run_id,
                    target_image=target_image,
                    from_version=current_image,
                    update_error=exc,
                    target_ref=executor_config.target_ref,
                )
            except Exception as exc:
                return await self._finalize_run(
                    executor_config,
                    run_id,
                    status="failed",
                    to_version=target_image,
                    message=str(exc) or exc.__class__.__name__,
                    last_error=str(exc) or exc.__class__.__name__,
                    from_version=current_image,
                )
        finally:
            pass

    def _build_executor_binding_contexts(
        self,
        executor_config: ExecutorConfig,
    ) -> list[_ExecutorBindingRunContext]:
        target_mode = executor_config.target_ref.get("mode")
        if target_mode in {"portainer_stack", "docker_compose", "kubernetes_workload"}:
            bindings: list[ExecutorServiceBinding] = sorted(
                executor_config.service_bindings,
                key=lambda binding: binding.service,
            )
            return [
                _ExecutorBindingRunContext(
                    service=binding.service,
                    tracker_source_id=binding.tracker_source_id,
                    channel_name=binding.channel_name,
                )
                for binding in bindings
            ]

        if executor_config.tracker_source_id is None or executor_config.channel_name is None:
            return []

        return [
            _ExecutorBindingRunContext(
                service=None,
                tracker_source_id=executor_config.tracker_source_id,
                channel_name=executor_config.channel_name,
            )
        ]

    def _summarize_portainer_stack_run(
        self,
        results: list[_ExecutorBindingRunResult],
    ) -> tuple[str, str]:
        return self._summarize_grouped_run("portainer-stack", results)

    def _summarize_docker_compose_run(
        self,
        results: list[_ExecutorBindingRunResult],
        *,
        runtime_type: str,
        group_message: str | None = None,
    ) -> tuple[str, str]:
        label = "podman-compose" if runtime_type == "podman" else "docker-compose"
        return self._summarize_grouped_run(label, results, group_message=group_message)

    def _summarize_kubernetes_workload_run(
        self,
        results: list[_ExecutorBindingRunResult],
        *,
        group_message: str | None = None,
    ) -> tuple[str, str]:
        return self._summarize_grouped_run(
            "kubernetes-workload",
            results,
            group_message=group_message,
        )

    @staticmethod
    def _build_grouped_run_diagnostics(
        kind: str,
        results: list[_ExecutorBindingRunResult],
        *,
        group_message: str | None = None,
    ) -> dict[str, Any]:
        ordered_results = sorted(results, key=lambda result: result.service)
        failed_count = len([result for result in ordered_results if result.status == "failed"])
        success_count = len([result for result in ordered_results if result.status == "success"])
        skipped_count = len([result for result in ordered_results if result.status == "skipped"])
        return {
            "kind": kind,
            "summary": {
                "updated_count": success_count,
                "skipped_count": skipped_count,
                "failed_count": failed_count,
                "group_message": group_message,
            },
            "services": [
                {
                    "service": result.service,
                    "status": result.status,
                    "from_version": result.from_version,
                    "to_version": result.to_version,
                    "message": result.message,
                }
                for result in ordered_results
            ],
        }

    @staticmethod
    def _summarize_grouped_run(
        label: str,
        results: list[_ExecutorBindingRunResult],
        *,
        group_message: str | None = None,
    ) -> tuple[str, str]:
        ordered_results = sorted(results, key=lambda result: result.service)
        failed_count = len([result for result in ordered_results if result.status == "failed"])
        success_count = len([result for result in ordered_results if result.status == "success"])
        skipped_count = len([result for result in ordered_results if result.status == "skipped"])

        if failed_count > 0:
            final_status = "failed"
        elif success_count > 0:
            final_status = "success"
        else:
            final_status = "skipped"

        details = "; ".join(
            f"{result.service}: {result.status} ({result.message})" for result in ordered_results
        )
        summary = (
            f"{label} run finished: "
            f"{success_count} updated, {skipped_count} skipped, {failed_count} failed"
        )
        message = f"{summary}; details: {details}"
        if group_message:
            message = f"{message}; group: {group_message}"
        return final_status, message

    @staticmethod
    def _compose_runtime_display_name(runtime_type: str) -> str:
        return "Podman Compose" if runtime_type == "podman" else "Docker Compose"

    async def _execute_helm_release_executor(
        self,
        executor_config: ExecutorConfig,
        *,
        manual: bool,
        _run_id: int | None,
    ) -> ExecutorRunOutcome:
        if executor_config.id is None:
            raise ValueError("Executor config must have id")

        if not executor_config.enabled:
            return await self._record_skipped(
                executor_config,
                message="executor disabled",
                run_id=_run_id,
            )

        if not manual:
            if executor_config.update_mode == "manual":
                return await self._record_skipped(
                    executor_config,
                    message="manual mode",
                    run_id=_run_id,
                )
            if (
                executor_config.update_mode == "maintenance_window"
                and not self._within_maintenance_window(executor_config.maintenance_window)
            ):
                return await self._record_skipped(
                    executor_config,
                    message="outside maintenance window",
                    run_id=_run_id,
                )

        runtime_connection = await self.storage.get_runtime_connection(
            executor_config.runtime_connection_id
        )
        if not runtime_connection:
            return await self._record_failed(
                executor_config,
                "runtime connection not found",
                run_id=_run_id,
            )
        if not runtime_connection.enabled:
            return await self._record_failed(
                executor_config,
                "runtime connection disabled",
                run_id=_run_id,
            )
        try:
            runtime_connection = await materialize_runtime_connection_credentials(
                self.storage,
                runtime_connection,
            )
        except ValueError as exc:
            return await self._record_failed(
                executor_config,
                str(exc),
                run_id=_run_id,
            )

        adapter = self._get_adapter(executor_config.id, runtime_connection)
        if not isinstance(adapter, KubernetesRuntimeAdapter):
            return await self._record_failed(
                executor_config,
                "helm_release targets require a Kubernetes runtime adapter",
                run_id=_run_id,
            )

        try:
            await adapter.validate_target_ref(executor_config.target_ref)
        except Exception as exc:
            return await self._record_failed(
                executor_config,
                f"invalid target ref: {exc}",
                run_id=_run_id,
            )

        tracker_binding = await self._resolve_tracker_binding(executor_config)
        if tracker_binding is None:
            return await self._record_failed(
                executor_config,
                "tracker source binding missing",
                run_id=_run_id,
            )
        aggregate_tracker_name, tracker_source = tracker_binding
        if tracker_source.source_type != "helm":
            return await self._record_failed(
                executor_config,
                "helm_release executor requires a Helm tracker source",
                run_id=_run_id,
            )

        tracker_config = await self.storage.get_tracker_config(aggregate_tracker_name)
        if not tracker_config:
            return await self._record_failed(
                executor_config,
                "tracker config missing",
                run_id=_run_id,
            )

        target_chart_version = await self._resolve_tracker_latest_chart_version(
            tracker_config.name,
            executor_config.channel_name,
            tracker_source_id=tracker_source.id,
            tracker_source_type=tracker_source.source_type,
        )
        if target_chart_version is None:
            return await self._record_skipped(
                executor_config,
                message="tracker has no chart versions",
                run_id=_run_id,
            )

        source_config = tracker_source.source_config or {}
        repo_url = source_config.get("repo")
        chart_name = source_config.get("chart")
        if not isinstance(chart_name, str) or not chart_name.strip():
            return await self._record_failed(
                executor_config,
                "Helm tracker source chart is missing",
                to_version=target_chart_version,
                run_id=_run_id,
            )
        chart_ref = chart_name.strip()

        try:
            current_chart_version = await adapter.get_helm_release_version(
                executor_config.target_ref
            )
        except Exception as exc:
            return await self._record_failed(
                executor_config,
                f"failed to resolve current Helm release version: {exc}",
                to_version=target_chart_version,
                run_id=_run_id,
            )

        if current_chart_version == target_chart_version:
            return await self._record_skipped(
                executor_config,
                message="Helm release already at target chart version",
                from_version=current_chart_version,
                to_version=target_chart_version,
                run_id=_run_id,
            )

        run_id = (
            _run_id
            if _run_id is not None
            else await self._create_run_record(
                executor_config,
                from_version=current_chart_version,
                to_version=target_chart_version,
            )
        )

        try:
            snapshot_data = await adapter.capture_helm_release_snapshot(executor_config.target_ref)
            await adapter.validate_helm_release_snapshot(executor_config.target_ref, snapshot_data)
            await self.storage.save_executor_snapshot(
                ExecutorSnapshot(
                    executor_id=executor_config.id,
                    snapshot_data=snapshot_data,
                )
            )
            result = await adapter.upgrade_helm_release(
                executor_config.target_ref,
                chart_ref=chart_ref,
                chart_version=target_chart_version,
                repo_url=repo_url if isinstance(repo_url, str) else None,
            )
            if not result.updated:
                return await self._finalize_run(
                    executor_config,
                    run_id,
                    status="skipped",
                    to_version=target_chart_version,
                    message=result.message or "Helm release already at target chart version",
                    last_error=None,
                    from_version=current_chart_version,
                )
            if executor_config.id is not None:
                refreshed_ref = {
                    **executor_config.target_ref,
                    "chart_name": chart_ref,
                    "chart_version": result.new_image or target_chart_version,
                }
                await self.storage.update_executor_target_ref(executor_config.id, refreshed_ref)
                executor_config = executor_config.model_copy(update={"target_ref": refreshed_ref})
            return await self._finalize_run(
                executor_config,
                run_id,
                status="success",
                to_version=result.new_image or target_chart_version,
                message=result.message or "Helm release upgraded",
                last_error=None,
                from_version=current_chart_version,
            )
        except RuntimeMutationError as exc:
            return await self._recover_after_mutation_failure(
                executor_config,
                adapter,
                run_id=run_id,
                target_image=target_chart_version,
                from_version=current_chart_version,
                update_error=exc,
                target_ref=executor_config.target_ref,
            )
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            return await self._finalize_run(
                executor_config,
                run_id,
                status="failed",
                to_version=target_chart_version,
                message=message,
                last_error=message,
                from_version=current_chart_version,
            )

    async def _execute_kubernetes_workload_executor(
        self,
        executor_config: ExecutorConfig,
        *,
        manual: bool,
        _run_id: int | None,
    ) -> ExecutorRunOutcome:
        if executor_config.id is None:
            raise ValueError("Executor config must have id")

        if not executor_config.enabled:
            return await self._record_skipped(
                executor_config,
                message="executor disabled",
                run_id=_run_id,
            )

        if not manual:
            if executor_config.update_mode == "manual":
                return await self._record_skipped(
                    executor_config,
                    message="manual mode",
                    run_id=_run_id,
                )
            if (
                executor_config.update_mode == "maintenance_window"
                and not self._within_maintenance_window(executor_config.maintenance_window)
            ):
                return await self._record_skipped(
                    executor_config,
                    message="outside maintenance window",
                    run_id=_run_id,
                )

        runtime_connection = await self.storage.get_runtime_connection(
            executor_config.runtime_connection_id
        )
        if not runtime_connection:
            return await self._record_failed(
                executor_config,
                "runtime connection not found",
                run_id=_run_id,
            )
        if not runtime_connection.enabled:
            return await self._record_failed(
                executor_config,
                "runtime connection disabled",
                run_id=_run_id,
            )
        try:
            runtime_connection = await materialize_runtime_connection_credentials(
                self.storage,
                runtime_connection,
            )
        except ValueError as exc:
            return await self._record_failed(
                executor_config,
                str(exc),
                run_id=_run_id,
            )

        adapter = self._get_adapter(executor_config.id, runtime_connection)
        if not isinstance(adapter, KubernetesRuntimeAdapter):
            return await self._record_failed(
                executor_config,
                "kubernetes_workload targets require a Kubernetes runtime adapter",
                run_id=_run_id,
            )

        try:
            await adapter.validate_target_ref(executor_config.target_ref)
        except Exception as exc:
            return await self._record_failed(
                executor_config,
                f"invalid target ref: {exc}",
                run_id=_run_id,
            )

        run_id = _run_id
        if run_id is None:
            run_id = await self._create_run_record(
                executor_config,
                from_version=None,
                to_version=None,
            )

        binding_contexts = self._build_executor_binding_contexts(executor_config)
        if not binding_contexts:
            return await self._finalize_run(
                executor_config,
                run_id,
                status="failed",
                to_version=None,
                message="kubernetes_workload executor has no service bindings",
                last_error="kubernetes_workload executor has no service bindings",
                from_version=None,
            )

        try:
            current_images_by_service = await adapter.fetch_workload_service_images(
                executor_config.target_ref
            )
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            return await self._finalize_run(
                executor_config,
                run_id,
                status="failed",
                to_version=None,
                message=message,
                last_error=message,
                from_version=None,
            )

        binding_results: list[_ExecutorBindingRunResult] = []
        pending_updates: dict[str, tuple[str, str]] = {}

        for binding_context in binding_contexts:
            service_name = binding_context.service or "container"
            tracker_binding = await self._resolve_tracker_binding_by_source_id(
                binding_context.tracker_source_id,
            )
            if tracker_binding is None:
                binding_results.append(
                    _ExecutorBindingRunResult(
                        service=service_name,
                        status="failed",
                        from_version=None,
                        to_version=None,
                        message="tracker source binding missing",
                    )
                )
                continue

            aggregate_tracker_name, tracker_source = tracker_binding
            tracker_config = await self.storage.get_tracker_config(aggregate_tracker_name)
            if not tracker_config:
                binding_results.append(
                    _ExecutorBindingRunResult(
                        service=service_name,
                        status="failed",
                        from_version=None,
                        to_version=None,
                        message="tracker config missing",
                    )
                )
                continue

            target = await self._resolve_tracker_latest_target(
                tracker_config.name,
                binding_context.channel_name,
                tracker_source_id=tracker_source.id,
                tracker_source_type=tracker_source.source_type,
            )
            if target is None:
                binding_results.append(
                    _ExecutorBindingRunResult(
                        service=service_name,
                        status="skipped",
                        from_version=None,
                        to_version=None,
                        message="tracker has no versions",
                    )
                )
                continue
            target_version, target_digest = target

            current_image = current_images_by_service.get(service_name)
            if not current_image:
                binding_results.append(
                    _ExecutorBindingRunResult(
                        service=service_name,
                        status="failed",
                        from_version=None,
                        to_version=None,
                        message=f"Kubernetes workload container image missing: {service_name}",
                    )
                )
                continue

            try:
                target_image = self._build_target_image(
                    current_image=current_image,
                    target_version=target_version,
                    target_digest=target_digest,
                    executor_config=executor_config,
                    tracker_source=tracker_source,
                    tracker_source_type=tracker_source.source_type,
                )
            except ValueError as exc:
                binding_results.append(
                    _ExecutorBindingRunResult(
                        service=service_name,
                        status="failed",
                        from_version=current_image,
                        to_version=None,
                        message=str(exc),
                    )
                )
                continue

            if current_image == target_image:
                binding_results.append(
                    _ExecutorBindingRunResult(
                        service=service_name,
                        status="skipped",
                        from_version=current_image,
                        to_version=target_image,
                        message="runtime already at target image",
                    )
                )
                continue

            pending_updates[service_name] = (current_image, target_image)

        binding_failures = [result for result in binding_results if result.status == "failed"]
        if binding_failures and pending_updates:
            abort_message = "Kubernetes workload update aborted because one or more service bindings failed validation"
            for service_name in sorted(pending_updates):
                current_image, target_image = pending_updates[service_name]
                binding_results.append(
                    _ExecutorBindingRunResult(
                        service=service_name,
                        status="failed",
                        from_version=current_image,
                        to_version=target_image,
                        message=abort_message,
                    )
                )
            pending_updates.clear()

        group_update_message: str | None = None
        if pending_updates:
            service_target_images = {
                service: target_image for service, (_, target_image) in pending_updates.items()
            }
            try:
                update_result = await adapter.update_workload_services(
                    executor_config.target_ref,
                    service_target_images,
                )
                group_update_message = update_result.message or "Kubernetes workload updated"
                for service_name in sorted(pending_updates):
                    current_image, target_image = pending_updates[service_name]
                    binding_results.append(
                        _ExecutorBindingRunResult(
                            service=service_name,
                            status="success",
                            from_version=current_image,
                            to_version=target_image,
                            message="updated",
                        )
                    )
            except Exception as exc:
                error_message = str(exc) or exc.__class__.__name__
                for service_name in sorted(pending_updates):
                    current_image, target_image = pending_updates[service_name]
                    binding_results.append(
                        _ExecutorBindingRunResult(
                            service=service_name,
                            status="failed",
                            from_version=current_image,
                            to_version=target_image,
                            message=error_message,
                        )
                    )

        final_status, final_message = self._summarize_kubernetes_workload_run(
            binding_results,
            group_message=group_update_message,
        )
        diagnostics = self._build_grouped_run_diagnostics(
            "kubernetes_workload",
            binding_results,
            group_message=group_update_message,
        )
        from_version, to_version = self._summarize_grouped_image_versions(binding_results)
        return await self._finalize_run(
            executor_config,
            run_id,
            status=final_status,
            to_version=to_version,
            message=final_message,
            last_error=final_message if final_status == "failed" else None,
            from_version=from_version,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _summarize_portainer_stack_image_versions(
        results: list[_ExecutorBindingRunResult],
    ) -> tuple[str | None, str | None]:
        return ExecutorScheduler._summarize_grouped_image_versions(results)

    @staticmethod
    def _summarize_grouped_image_versions(
        results: list[_ExecutorBindingRunResult],
    ) -> tuple[str | None, str | None]:
        ordered_results = sorted(results, key=lambda result: result.service)

        def summarize(field: str) -> str | None:
            values = [
                (result.service, value)
                for result in ordered_results
                if (value := getattr(result, field))
            ]
            if not values:
                return None
            if len(values) == 1:
                return values[0][1]
            return "; ".join(f"{service}={value}" for service, value in values)

        return summarize("from_version"), summarize("to_version")

    async def _execute_docker_compose_executor(
        self,
        executor_config: ExecutorConfig,
        *,
        manual: bool,
        _run_id: int | None,
    ) -> ExecutorRunOutcome:
        if executor_config.id is None:
            raise ValueError("Executor config must have id")

        if not executor_config.enabled:
            return await self._record_skipped(
                executor_config,
                message="executor disabled",
                run_id=_run_id,
            )

        if not manual:
            if executor_config.update_mode == "manual":
                return await self._record_skipped(
                    executor_config,
                    message="manual mode",
                    run_id=_run_id,
                )

            if executor_config.update_mode == "maintenance_window":
                if not self._within_maintenance_window(executor_config.maintenance_window):
                    return await self._record_skipped(
                        executor_config,
                        message="outside maintenance window",
                        run_id=_run_id,
                    )

        runtime_connection = await self.storage.get_runtime_connection(
            executor_config.runtime_connection_id
        )
        if not runtime_connection:
            return await self._record_failed(
                executor_config,
                "runtime connection not found",
                run_id=_run_id,
            )
        if not runtime_connection.enabled:
            return await self._record_failed(
                executor_config,
                "runtime connection disabled",
                run_id=_run_id,
            )
        try:
            runtime_connection = await materialize_runtime_connection_credentials(
                self.storage,
                runtime_connection,
            )
        except ValueError as exc:
            return await self._record_failed(
                executor_config,
                str(exc),
                run_id=_run_id,
            )

        adapter = self._get_adapter(executor_config.id, runtime_connection)
        if not isinstance(adapter, (DockerRuntimeAdapter, PodmanRuntimeAdapter)):
            return await self._record_failed(
                executor_config,
                "docker_compose targets require a Docker or Podman runtime adapter",
                run_id=_run_id,
            )

        try:
            await adapter.validate_target_ref(executor_config.target_ref)
        except Exception as exc:
            return await self._record_failed(
                executor_config,
                f"invalid target ref: {exc}",
                run_id=_run_id,
            )

        run_id = _run_id
        if run_id is None:
            run_id = await self._create_run_record(
                executor_config,
                from_version=None,
                to_version=None,
            )

        binding_contexts = self._build_executor_binding_contexts(executor_config)
        if not binding_contexts:
            return await self._finalize_run(
                executor_config,
                run_id,
                status="failed",
                to_version=None,
                message="docker_compose executor has no service bindings",
                last_error="docker_compose executor has no service bindings",
                from_version=None,
            )

        try:
            current_images_by_service = await adapter.fetch_compose_service_images(
                executor_config.target_ref
            )
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            return await self._finalize_run(
                executor_config,
                run_id,
                status="failed",
                to_version=None,
                message=message,
                last_error=message,
                from_version=None,
            )

        binding_results: list[_ExecutorBindingRunResult] = []
        pending_updates: dict[str, tuple[str, str]] = {}

        for binding_context in binding_contexts:
            service_name = binding_context.service or "service"

            tracker_binding = await self._resolve_tracker_binding_by_source_id(
                binding_context.tracker_source_id
            )
            if tracker_binding is None:
                binding_results.append(
                    _ExecutorBindingRunResult(
                        service=service_name,
                        status="failed",
                        from_version=None,
                        to_version=None,
                        message="tracker source binding missing",
                    )
                )
                continue

            aggregate_tracker_name, tracker_source = tracker_binding
            tracker_config = await self.storage.get_tracker_config(aggregate_tracker_name)
            if not tracker_config:
                binding_results.append(
                    _ExecutorBindingRunResult(
                        service=service_name,
                        status="failed",
                        from_version=None,
                        to_version=None,
                        message="tracker config missing",
                    )
                )
                continue

            target = await self._resolve_tracker_latest_target(
                tracker_config.name,
                binding_context.channel_name,
                tracker_source_id=tracker_source.id,
                tracker_source_type=tracker_source.source_type,
            )
            if target is None:
                binding_results.append(
                    _ExecutorBindingRunResult(
                        service=service_name,
                        status="skipped",
                        from_version=None,
                        to_version=None,
                        message="tracker has no versions",
                    )
                )
                continue
            target_version, target_digest = target

            current_image = current_images_by_service.get(service_name)
            if not current_image:
                binding_results.append(
                    _ExecutorBindingRunResult(
                        service=service_name,
                        status="failed",
                        from_version=None,
                        to_version=None,
                        message=f"{self._compose_runtime_display_name(runtime_connection.type)} service image missing: {service_name}",
                    )
                )
                continue

            try:
                target_image = self._build_target_image(
                    current_image=current_image,
                    target_version=target_version,
                    target_digest=target_digest,
                    executor_config=executor_config,
                    tracker_source=tracker_source,
                    tracker_source_type=tracker_source.source_type,
                )
            except ValueError as exc:
                binding_results.append(
                    _ExecutorBindingRunResult(
                        service=service_name,
                        status="failed",
                        from_version=current_image,
                        to_version=None,
                        message=str(exc),
                    )
                )
                continue

            if current_image == target_image:
                binding_results.append(
                    _ExecutorBindingRunResult(
                        service=service_name,
                        status="skipped",
                        from_version=current_image,
                        to_version=target_image,
                        message="runtime already at target image",
                    )
                )
                continue

            pending_updates[service_name] = (current_image, target_image)

        binding_failures = [result for result in binding_results if result.status == "failed"]
        if binding_failures and pending_updates:
            abort_message = f"{self._compose_runtime_display_name(runtime_connection.type)} group update aborted because one or more service bindings failed validation"
            for service_name in sorted(pending_updates):
                current_image, target_image = pending_updates[service_name]
                binding_results.append(
                    _ExecutorBindingRunResult(
                        service=service_name,
                        status="failed",
                        from_version=current_image,
                        to_version=target_image,
                        message=abort_message,
                    )
                )
            pending_updates.clear()

        group_update_message: str | None = None
        if pending_updates:
            service_target_images = {
                service: target_image for service, (_, target_image) in pending_updates.items()
            }
            try:
                update_result = await adapter.update_compose_services(
                    executor_config.target_ref,
                    service_target_images,
                )
                group_update_message = (
                    update_result.message
                    or f"{self._compose_runtime_display_name(runtime_connection.type)} services updated"
                )
                for service_name in sorted(pending_updates):
                    current_image, target_image = pending_updates[service_name]
                    binding_results.append(
                        _ExecutorBindingRunResult(
                            service=service_name,
                            status="success",
                            from_version=current_image,
                            to_version=target_image,
                            message="updated",
                        )
                    )
            except Exception as exc:
                error_message = str(exc) or exc.__class__.__name__
                for service_name in sorted(pending_updates):
                    current_image, target_image = pending_updates[service_name]
                    binding_results.append(
                        _ExecutorBindingRunResult(
                            service=service_name,
                            status="failed",
                            from_version=current_image,
                            to_version=target_image,
                            message=error_message,
                        )
                    )

        final_status, final_message = self._summarize_docker_compose_run(
            binding_results,
            runtime_type=runtime_connection.type,
            group_message=group_update_message,
        )
        diagnostics = self._build_grouped_run_diagnostics(
            "podman_compose" if runtime_connection.type == "podman" else "docker_compose",
            binding_results,
            group_message=group_update_message,
        )
        from_version, to_version = self._summarize_grouped_image_versions(binding_results)
        return await self._finalize_run(
            executor_config,
            run_id,
            status=final_status,
            to_version=to_version,
            message=final_message,
            last_error=final_message if final_status == "failed" else None,
            from_version=from_version,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _classify_portainer_runtime_error(exc: Exception) -> str:
        message = str(exc) or exc.__class__.__name__
        if isinstance(exc, PortainerRequestTimeoutError):
            return f"portainer request timeout: {message}"
        return message

    async def _execute_portainer_stack_executor(
        self,
        executor_config: ExecutorConfig,
        *,
        manual: bool,
        _run_id: int | None,
    ) -> ExecutorRunOutcome:
        if executor_config.id is None:
            raise ValueError("Executor config must have id")

        try:
            if not executor_config.enabled:
                return await self._record_skipped(
                    executor_config,
                    message="executor disabled",
                    run_id=_run_id,
                )

            if not manual:
                if executor_config.update_mode == "manual":
                    return await self._record_skipped(
                        executor_config,
                        message="manual mode",
                        run_id=_run_id,
                    )

                if executor_config.update_mode == "maintenance_window":
                    if not self._within_maintenance_window(executor_config.maintenance_window):
                        return await self._record_skipped(
                            executor_config,
                            message="outside maintenance window",
                            run_id=_run_id,
                        )

            runtime_connection = await self.storage.get_runtime_connection(
                executor_config.runtime_connection_id
            )
            if not runtime_connection:
                return await self._record_failed(
                    executor_config,
                    "runtime connection not found",
                    run_id=_run_id,
                )
            if not runtime_connection.enabled:
                return await self._record_failed(
                    executor_config,
                    "runtime connection disabled",
                    run_id=_run_id,
                )
            try:
                runtime_connection = await materialize_runtime_connection_credentials(
                    self.storage,
                    runtime_connection,
                )
            except ValueError as exc:
                return await self._record_failed(
                    executor_config,
                    str(exc),
                    run_id=_run_id,
                )

            adapter = self._get_adapter(executor_config.id, runtime_connection)
            if not isinstance(adapter, PortainerRuntimeAdapter):
                return await self._record_failed(
                    executor_config,
                    "portainer_stack targets require a Portainer runtime adapter",
                    run_id=_run_id,
                )

            try:
                await adapter.validate_target_ref(executor_config.target_ref)
            except ValueError as exc:
                return await self._record_failed(
                    executor_config,
                    f"invalid target ref: {exc}",
                    run_id=_run_id,
                )
            except PortainerRequestTimeoutError as exc:
                return await self._record_failed(
                    executor_config,
                    f"portainer target validation timeout: {exc}",
                    run_id=_run_id,
                )
            except Exception as exc:
                return await self._record_failed(
                    executor_config,
                    f"portainer target validation access failed: {exc}",
                    run_id=_run_id,
                )

            run_id = _run_id
            if run_id is None:
                run_id = await self._create_run_record(
                    executor_config,
                    from_version=None,
                    to_version=None,
                )

            binding_contexts = self._build_executor_binding_contexts(executor_config)
            if not binding_contexts:
                return await self._finalize_run(
                    executor_config,
                    run_id,
                    status="failed",
                    to_version=None,
                    message="portainer_stack executor has no service bindings",
                    last_error="portainer_stack executor has no service bindings",
                    from_version=None,
                )

            try:
                current_images_by_service = await adapter.fetch_stack_service_images(
                    executor_config.target_ref
                )
            except Exception as exc:
                message = self._classify_portainer_runtime_error(exc)
                return await self._finalize_run(
                    executor_config,
                    run_id,
                    status="failed",
                    to_version=None,
                    message=message,
                    last_error=message,
                    from_version=None,
                )

            binding_results: list[_ExecutorBindingRunResult] = []
            pending_updates: dict[str, tuple[str, str]] = {}

            for binding_context in binding_contexts:
                service_name = binding_context.service or "service"

                tracker_binding = await self._resolve_tracker_binding_by_source_id(
                    binding_context.tracker_source_id
                )
                if tracker_binding is None:
                    binding_results.append(
                        _ExecutorBindingRunResult(
                            service=service_name,
                            status="failed",
                            from_version=None,
                            to_version=None,
                            message="tracker source binding missing",
                        )
                    )
                    continue

                aggregate_tracker_name, tracker_source = tracker_binding
                tracker_config = await self.storage.get_tracker_config(aggregate_tracker_name)
                if not tracker_config:
                    binding_results.append(
                        _ExecutorBindingRunResult(
                            service=service_name,
                            status="failed",
                            from_version=None,
                            to_version=None,
                            message="tracker config missing",
                        )
                    )
                    continue

                target = await self._resolve_tracker_latest_target(
                    tracker_config.name,
                    binding_context.channel_name,
                    tracker_source_id=tracker_source.id,
                    tracker_source_type=tracker_source.source_type,
                )
                if target is None:
                    binding_results.append(
                        _ExecutorBindingRunResult(
                            service=service_name,
                            status="skipped",
                            from_version=None,
                            to_version=None,
                            message="tracker has no versions",
                        )
                    )
                    continue
                target_version, target_digest = target

                current_image = current_images_by_service.get(service_name)
                if not current_image:
                    binding_results.append(
                        _ExecutorBindingRunResult(
                            service=service_name,
                            status="failed",
                            from_version=None,
                            to_version=None,
                            message=f"Portainer stack service image missing: {service_name}",
                        )
                    )
                    continue

                try:
                    target_image = self._build_target_image(
                        current_image=current_image,
                        target_version=target_version,
                        target_digest=target_digest,
                        executor_config=executor_config,
                        tracker_source=tracker_source,
                        tracker_source_type=tracker_source.source_type,
                    )
                except ValueError as exc:
                    binding_results.append(
                        _ExecutorBindingRunResult(
                            service=service_name,
                            status="failed",
                            from_version=current_image,
                            to_version=None,
                            message=str(exc),
                        )
                    )
                    continue

                if current_image == target_image:
                    binding_results.append(
                        _ExecutorBindingRunResult(
                            service=service_name,
                            status="skipped",
                            from_version=current_image,
                            to_version=target_image,
                            message="runtime already at target image",
                        )
                    )
                    continue

                pending_updates[service_name] = (current_image, target_image)

            group_update_message: str | None = None
            if pending_updates:
                service_target_images = {
                    service: target_image for service, (_, target_image) in pending_updates.items()
                }
                try:
                    update_result = await adapter.update_stack_services(
                        executor_config.target_ref,
                        service_target_images,
                    )
                    message = update_result.message or "Portainer stack updated via API"
                    group_update_message = message
                    for service_name in sorted(pending_updates):
                        current_image, target_image = pending_updates[service_name]
                        binding_results.append(
                            _ExecutorBindingRunResult(
                                service=service_name,
                                status="success",
                                from_version=current_image,
                                to_version=target_image,
                                message=message,
                            )
                        )
                except Exception as exc:
                    error_message = self._classify_portainer_runtime_error(exc)
                    for service_name in sorted(pending_updates):
                        current_image, target_image = pending_updates[service_name]
                        binding_results.append(
                            _ExecutorBindingRunResult(
                                service=service_name,
                                status="failed",
                                from_version=current_image,
                                to_version=target_image,
                                message=error_message,
                            )
                        )

            final_status, final_message = self._summarize_portainer_stack_run(binding_results)
            diagnostics = self._build_grouped_run_diagnostics(
                "portainer_stack",
                binding_results,
                group_message=group_update_message,
            )
            from_version, to_version = self._summarize_portainer_stack_image_versions(
                binding_results
            )
            return await self._finalize_run(
                executor_config,
                run_id,
                status=final_status,
                to_version=to_version,
                message=final_message,
                last_error=final_message if final_status == "failed" else None,
                from_version=from_version,
                diagnostics=diagnostics,
            )
        finally:
            pass

    async def _recover_after_mutation_failure(
        self,
        executor_config: ExecutorConfig,
        adapter: BaseRuntimeAdapter,
        *,
        run_id: int,
        target_image: str,
        from_version: str | None,
        update_error: Exception,
        target_ref: dict[str, Any] | None = None,
        finalize_run: bool = True,
    ) -> ExecutorRunOutcome:
        if executor_config.id is None:
            raise ValueError("Executor config must have id")

        snapshot = await self.storage.get_executor_snapshot(executor_config.id)
        update_message = str(update_error) or update_error.__class__.__name__
        effective_target_ref = target_ref or executor_config.target_ref

        async def _build_outcome(
            *,
            status: str,
            to_version: str | None,
            message: str,
            last_error: str | None,
        ) -> ExecutorRunOutcome:
            if finalize_run:
                return await self._finalize_run(
                    executor_config,
                    run_id,
                    status=status,
                    to_version=to_version,
                    message=message,
                    last_error=last_error,
                    from_version=from_version,
                )
            return ExecutorRunOutcome(
                status=status,
                from_version=from_version,
                to_version=to_version,
                message=message,
            )

        if snapshot is None:
            return await _build_outcome(
                status="failed",
                to_version=target_image,
                message=f"update failed after destructive steps and no snapshot was available for recovery: {update_message}",
                last_error=update_message,
            )

        try:
            recovery_result = await adapter.recover_from_snapshot(
                effective_target_ref,
                snapshot.snapshot_data,
            )
            recovered_version = recovery_result.new_image or from_version
            recovery_message = recovery_result.message or "runtime recovered from snapshot"

            target_mode = effective_target_ref.get("mode", "container")
            if (
                recovery_result.new_container_id
                and executor_config.id is not None
                and target_mode == "container"
            ):
                refreshed_ref = {
                    **effective_target_ref,
                    "container_id": recovery_result.new_container_id,
                }
                await self.storage.update_executor_target_ref(executor_config.id, refreshed_ref)
                executor_config = executor_config.model_copy(update={"target_ref": refreshed_ref})

            return await _build_outcome(
                status="failed",
                to_version=recovered_version,
                message=(
                    f"update failed after destructive steps: {update_message}; "
                    f"automatic recovery succeeded: {recovery_message}"
                ),
                last_error=update_message,
            )
        except Exception as recovery_exc:
            recovery_message = str(recovery_exc) or recovery_exc.__class__.__name__
            return await _build_outcome(
                status="failed",
                to_version=from_version,
                message=(
                    f"update failed after destructive steps: {update_message}; "
                    f"automatic recovery failed: {recovery_message}"
                ),
                last_error=f"{update_message}; recovery failed: {recovery_message}",
            )

    async def _resolve_tracker_latest_version(
        self,
        tracker_name: str,
        channel_name: str | None,
        *,
        tracker_source_id: int | None = None,
        tracker_source_type: str | None = None,
    ) -> str | None:
        return await _resolve_tracker_latest_version_from_storage(
            self.storage,
            tracker_name,
            channel_name,
            tracker_source_id=tracker_source_id,
            tracker_source_type=tracker_source_type,
        )

    async def _resolve_tracker_latest_target(
        self,
        tracker_name: str,
        channel_name: str | None,
        *,
        tracker_source_id: int | None = None,
        tracker_source_type: str | None = None,
    ) -> tuple[str, str | None] | None:
        return await _resolve_tracker_latest_target_from_storage(
            self.storage,
            tracker_name,
            channel_name,
            tracker_source_id=tracker_source_id,
            tracker_source_type=tracker_source_type,
        )

    async def _resolve_tracker_latest_chart_version(
        self,
        tracker_name: str,
        channel_name: str | None,
        *,
        tracker_source_id: int | None,
        tracker_source_type: str | None,
    ) -> str | None:
        releases = await self._load_bound_releases(
            tracker_name,
            tracker_source_id=tracker_source_id,
            tracker_source_type=tracker_source_type,
        )
        if not releases:
            return None

        tracker_config = await self.storage.get_tracker_config(tracker_name)
        sort_mode = tracker_config.version_sort_mode if tracker_config else "published_at"
        scoped_channels = tracker_config.channels if tracker_config else []
        if tracker_source_id is not None:
            bound_source = await self.storage.get_tracker_source(tracker_source_id)
            if bound_source is not None and bound_source.release_channels:
                scoped_channels = bound_source.release_channels

        bound_channels = scoped_channels
        if channel_name and scoped_channels:
            bound_channels = [
                ch for ch in scoped_channels if ch.name == channel_name and ch.enabled
            ]
            if not bound_channels:
                return None

        if bound_channels:
            channel_winners = self.storage.select_best_releases_by_channel(
                releases,
                bound_channels,
                sort_mode=sort_mode,
                channel_source_type=tracker_source_type,
            )
            if not channel_winners:
                return None
            best_release = max(
                channel_winners.values(),
                key=lambda release: self.storage._release_order_key(release, sort_mode),
            )
        else:
            best_release = self.storage.select_best_release(
                releases,
                bound_channels,
                sort_mode=sort_mode,
            )
        if best_release is None:
            return None
        chart_version = best_release.chart_version or best_release.tag_name
        return (
            chart_version.strip()
            if isinstance(chart_version, str) and chart_version.strip()
            else None
        )

    def _build_target_image(
        self,
        *,
        current_image: str,
        target_version: str,
        target_digest: str | None,
        executor_config: ExecutorConfig,
        tracker_source,
        tracker_source_type: str | None,
    ) -> str:
        return _build_target_image_value(
            current_image=current_image,
            target_version=target_version,
            target_digest=target_digest,
            executor_config=executor_config,
            tracker_source=tracker_source,
            tracker_source_type=tracker_source_type,
        )

    async def _resolve_tracker_binding(
        self, executor_config: ExecutorConfig
    ) -> tuple[str, TrackerSource] | None:
        return await _resolve_tracker_binding_from_storage(self.storage, executor_config)

    async def _resolve_tracker_binding_by_source_id(
        self, tracker_source_id: int
    ) -> tuple[str, TrackerSource] | None:
        return await _resolve_tracker_binding_by_source_id_from_storage(
            self.storage,
            tracker_source_id,
        )

    async def _load_bound_releases(
        self,
        tracker_name: str,
        *,
        tracker_source_id: int | None,
        tracker_source_type: str | None,
    ) -> list:
        return await _load_bound_releases_from_storage(
            self.storage,
            tracker_name,
            tracker_source_id=tracker_source_id,
            tracker_source_type=tracker_source_type,
        )

    @staticmethod
    def _replace_image_tag(image: str, target_version: str) -> str:
        return _replace_image_tag_value(image, target_version)

    def _get_adapter(self, executor_id: int, runtime_connection) -> BaseRuntimeAdapter:
        if executor_id in self._adapters:
            return self._adapters[executor_id]

        if runtime_connection.type == "docker":
            adapter = DockerRuntimeAdapter(runtime_connection)
        elif runtime_connection.type == "podman":
            adapter = PodmanRuntimeAdapter(runtime_connection)
        elif runtime_connection.type == "kubernetes":
            adapter = KubernetesRuntimeAdapter(runtime_connection)
        elif runtime_connection.type == "portainer":
            adapter = PortainerRuntimeAdapter(runtime_connection)
        else:
            raise ValueError(f"Unsupported runtime type: {runtime_connection.type}")

        self._adapters[executor_id] = adapter
        return adapter

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
            status="skipped",
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
            WebhookNotifier(
                name=item.name,
                url=item.url,
                events=item.events,
                language=item.language,
            )
            for item in db_notifiers
            if item.enabled and item.type == "webhook"
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

        await asyncio.gather(
            *(notifier.notify(event, payload) for notifier in active_notifiers),
            return_exceptions=True,
        )

    def _within_maintenance_window(self, window: MaintenanceWindowConfig | None) -> bool:
        if not window:
            return False
        now = self._now_provider()
        try:
            tz = ZoneInfo(self._system_timezone)
        except Exception:
            tz = ZoneInfo("UTC")
        localized = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)

        if window.days_of_week and localized.weekday() not in window.days_of_week:
            return False

        start_time = _parse_time(window.start_time)
        end_time = _parse_time(window.end_time)
        if start_time is None or end_time is None:
            return False

        current_time = localized.time()
        if start_time <= end_time:
            return start_time <= current_time <= end_time
        return current_time >= start_time or current_time <= end_time


def _notification_timestamp(value: datetime, timezone_name: str) -> str:
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception:
        timezone = ZoneInfo("UTC")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone)
    return value.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")


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
