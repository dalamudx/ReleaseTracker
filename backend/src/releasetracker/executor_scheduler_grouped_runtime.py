from __future__ import annotations


from .executor_scheduler_grouped_runtime_helm import ExecutorSchedulerHelmRuntime
from .executor_scheduler_grouped_runtime_kubernetes import ExecutorSchedulerKubernetesRuntime
from .executor_scheduler_grouped_runtime_compose import ExecutorSchedulerComposeRuntime
from .executor_scheduler_grouped_runtime_portainer import ExecutorSchedulerPortainerRuntime
from .executor_scheduler_grouped_runtime_support import (
    ExecutorSchedulerGroupedRuntimeSupport,
)


class ExecutorSchedulerGroupedRuntime(
    ExecutorSchedulerHelmRuntime,
    ExecutorSchedulerKubernetesRuntime,
    ExecutorSchedulerComposeRuntime,
    ExecutorSchedulerPortainerRuntime,
    ExecutorSchedulerGroupedRuntimeSupport,
):
    """Execute grouped runtime updates and summarize their outcomes."""
