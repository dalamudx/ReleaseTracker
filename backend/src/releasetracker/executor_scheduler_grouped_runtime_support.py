from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import ExecutorConfig, ExecutorServiceBinding


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


class ExecutorSchedulerGroupedRuntimeSupport:
    """Share binding construction and result summaries across grouped runtimes."""

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

    @staticmethod
    def _summarize_portainer_stack_image_versions(
        results: list[_ExecutorBindingRunResult],
    ) -> tuple[str | None, str | None]:
        return ExecutorSchedulerGroupedRuntimeSupport._summarize_grouped_image_versions(results)

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
