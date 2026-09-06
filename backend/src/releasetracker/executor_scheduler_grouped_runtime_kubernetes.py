from __future__ import annotations

from .config import ExecutorConfig
from .executor_scheduler_grouped_runtime_support import _ExecutorBindingRunResult
from .executor_scheduler_run_lifecycle import ExecutorRunOutcome
from .executors import KubernetesRuntimeAdapter
from .services.runtime_credentials import materialize_runtime_connection_credentials


class ExecutorSchedulerKubernetesRuntime:
    """Execute grouped Kubernetes workload updates."""

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
