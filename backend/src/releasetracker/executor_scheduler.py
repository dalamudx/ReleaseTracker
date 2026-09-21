from __future__ import annotations

from .services.task_effects import mark_deployment_mutation

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Coroutine
from zoneinfo import ZoneInfo

from .config import (
    ExecutorConfig,
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
from .executor_scheduler_grouped_runtime import ExecutorSchedulerGroupedRuntime
from .executor_scheduler_update_safety import ExecutorSchedulerUpdateSafety
from .executor_scheduler_run_lifecycle import (
    ExecutorRunOutcome,
    ExecutorSchedulerRunLifecycle,
)
from .executor_scheduler_maintenance import (
    _parse_time,
    ExecutorSchedulerMaintenance,
)
from .executor_scheduler_run_queue import (
    ExecutorSchedulerRunQueue,
)
from .executor_scheduler_target_resolution import (
    ExecutorSchedulerTargetResolution,
    _normalize_docker_digest,
)
from .executor_trigger import enqueue_executor_binding_targets
from .scheduler_host import SchedulerHost
from .services.runtime_credentials import materialize_runtime_connection_credentials
from .services.snapshot_service import SnapshotService
from .storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)

_DESIRED_STATE_CONSUMER_INTERVAL_SECONDS = 30


class ExecutorScheduler(
    ExecutorSchedulerRunQueue,
    ExecutorSchedulerMaintenance,
    ExecutorSchedulerTargetResolution,
    ExecutorSchedulerUpdateSafety,
    ExecutorSchedulerGroupedRuntime,
    ExecutorSchedulerRunLifecycle,
):
    def __init__(
        self,
        storage: SQLiteStorage,
        *,
        scheduler_host: SchedulerHost | None = None,
        now_provider: Callable[[], datetime] | None = None,
        snapshot_service: SnapshotService | None = None,
    ):
        self.storage = storage
        self.deploy_tasks = None
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
        self._snapshot_service = snapshot_service or SnapshotService(storage)

    @property
    def snapshot_service(self) -> SnapshotService:
        return self._snapshot_service

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
        # Configuration and credential materialization are part of adapter identity.
        # Do not let a saved executor keep an old runtime client.
        self._adapters.pop(executor_id, None)
        config = await self.storage.get_executor_config(executor_id)
        if config:
            await self._add_or_update_executor_job(config)
            projection_work_enqueued = await self._enqueue_current_projection_work_for_executor(
                config
            )
            await self.refresh_release_history_cleanup_schedule()
            if projection_work_enqueued:
                self._track_background_task(self.reconcile_pending_desired_states())

    async def _enqueue_current_projection_work_for_executor(
        self,
        executor_config: ExecutorConfig,
    ) -> bool:
        return await enqueue_executor_binding_targets(self.storage, executor_config)

    async def remove_executor(self, executor_id: int) -> None:
        self._adapters.pop(executor_id, None)
        self.scheduler_host.remove_job(self._job_namespace, executor_id)
        await self.refresh_release_history_cleanup_schedule()

    async def check_all(self) -> None:
        await self.reconcile_pending_desired_states()

    async def _add_or_update_executor_job(self, executor_config: ExecutorConfig) -> None:
        if executor_config.id is None:
            return
        self.scheduler_host.remove_job(self._job_namespace, executor_config.id)

    async def _execute_executor_by_id(self, executor_id: int) -> None:
        if self.deploy_tasks is not None:
            config = await self.storage.get_executor_config(executor_id)
            if config and config.enabled:
                await self._enqueue_current_projection_work_for_executor(config)
                await self.deploy_tasks.dispatch_pending()
            return
        config = await self.storage.get_executor_config(executor_id)
        if not config:
            return
        try:
            await self._run_executor_with_overlap_guard(config, manual=False)
        except ValueError as exc:
            if "already running" not in str(exc):
                raise

    async def _execute_executor(
        self, executor_config: ExecutorConfig, *, manual: bool, _run_id: int | None = None
    ) -> ExecutorRunOutcome:
        await self._refresh_system_timezone()
        target_mode = executor_config.target_ref.get("mode")
        if target_mode == "ssh_compose":
            return await self._execute_ssh_compose_executor(
                executor_config, manual=manual, _run_id=_run_id
            )
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
            if not adapter.supports_single_image_operations(executor_config.target_ref):
                return await self._record_failed(
                    executor_config,
                    (
                        f"{executor_config.runtime_type} target mode {target_mode!r} "
                        "requires grouped runtime handling, not generic single-image updates"
                    ),
                    run_id=_run_id,
                )
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

            runtime_digest_matches = False
            if target_digest is not None:
                try:
                    current_digest = await adapter.get_current_image_digest(
                        executor_config.target_ref
                    )
                    runtime_digest_matches = _normalize_docker_digest(
                        current_digest
                    ) == _normalize_docker_digest(target_digest)
                except Exception as exc:
                    logger.warning(
                        "Failed to resolve current image digest for executor %s: %s",
                        executor_config.id,
                        exc,
                    )

            if current_image == target_image or runtime_digest_matches:
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
                    message=(
                        "runtime already at target artifact"
                        if runtime_digest_matches and current_image != target_image
                        else "runtime already at target image"
                    ),
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
                snapshot_created = False
                if self._supports_persisted_full_config_snapshots(executor_config):
                    snapshot_created = await self._capture_pre_update_snapshot(
                        executor_config,
                        adapter,
                        run_id=run_id,
                        current_image=current_image,
                    )
                await mark_deployment_mutation()
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

                # --- Post-update health check --------------------------
                # Only runs for container-mode executors in this branch.
                # Grouped modes (compose / portainer stack / kubernetes
                # workload / helm_release) go through separate pipelines
                # and will get their own wiring in a follow-up slice.
                health_check_outcome = await self._run_post_update_health_check(
                    executor_config,
                    adapter,
                    run_id=run_id,
                    update_result=result,
                )
                if health_check_outcome is not None:
                    return await self._finalize_run(
                        executor_config,
                        run_id,
                        status=health_check_outcome.status,
                        to_version=result.new_image or target_image,
                        message=health_check_outcome.message,
                        last_error=health_check_outcome.last_error,
                        from_version=current_image,
                        diagnostics=health_check_outcome.diagnostics,
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
                message = str(exc) or exc.__class__.__name__
                diagnostics = self._manual_rollback_diagnostics(snapshot_created=snapshot_created)
                return await self._finalize_run(
                    executor_config,
                    run_id,
                    status="failed",
                    to_version=target_image,
                    message=message,
                    last_error=message,
                    from_version=current_image,
                    diagnostics=diagnostics,
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

    def _get_adapter(self, executor_id: int, runtime_connection) -> BaseRuntimeAdapter:
        cached_adapter = self._adapters.get(executor_id)
        if cached_adapter is not None:
            return cached_adapter

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
