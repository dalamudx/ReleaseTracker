from __future__ import annotations

from .services.task_effects import mark_deployment_mutation

from .config import ExecutorConfig
from .executor_scheduler_run_lifecycle import ExecutorRunOutcome
from .executors import KubernetesRuntimeAdapter
from .executors.base import RuntimeMutationError
from .services.runtime_credentials import materialize_runtime_connection_credentials


class ExecutorSchedulerHelmRuntime:
    """Execute Helm release updates through Kubernetes runtime connections."""

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

        chart_target = await self._resolve_tracker_latest_chart_target(
            tracker_config.name,
            executor_config.channel_name,
            tracker_source_id=tracker_source.id,
            tracker_source_type=tracker_source.source_type,
        )
        if chart_target is None:
            return await self._record_skipped(
                executor_config,
                message="tracker has no chart versions",
                run_id=_run_id,
            )
        target_chart_version = chart_target["version"]
        target_chart_digest = chart_target.get("digest")

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

        target_chart_digest = (
            target_chart_digest.strip()
            if isinstance(target_chart_digest, str) and target_chart_digest.strip()
            else None
        )
        recorded_chart_digest = executor_config.target_ref.get("chart_digest")
        same_chart_artifact = current_chart_version == target_chart_version and (
            target_chart_digest is None or recorded_chart_digest == target_chart_digest
        )
        if same_chart_artifact:
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

        snapshot_created = False
        try:
            if self._supports_persisted_full_config_snapshots(executor_config):
                snapshot_created = await self._capture_pre_update_snapshot(
                    executor_config,
                    adapter,
                    run_id=run_id,
                    current_image=current_chart_version,
                )
            await mark_deployment_mutation()
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
                    "chart_digest": target_chart_digest,
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
            message = str(exc) or exc.__class__.__name__
            return await self._finalize_run(
                executor_config,
                run_id,
                status="failed",
                to_version=target_chart_version,
                message=message,
                last_error=message,
                from_version=current_chart_version,
                diagnostics=self._manual_rollback_diagnostics(snapshot_created=snapshot_created),
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
                diagnostics=self._manual_rollback_diagnostics(snapshot_created=snapshot_created),
            )
