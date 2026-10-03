"""Guard whole-operation leases on every production cached-adapter consumer."""

import ast
import inspect

import pytest

from releasetracker import executor_scheduler as scheduler_module
from releasetracker import executor_scheduler_grouped_runtime_compose as compose_module
from releasetracker import executor_scheduler_grouped_runtime_helm as helm_module
from releasetracker import executor_scheduler_grouped_runtime_kubernetes as kubernetes_module
from releasetracker import executor_scheduler_grouped_runtime_portainer as portainer_module
from releasetracker.executor_scheduler import ExecutorScheduler
from releasetracker.executors.adapter_lifetime import runtime_adapter_scope
from releasetracker.services import deploy_tasks, recovery_tasks, deployment_readiness
from releasetracker.services import deployment_readiness_probes as probes


@pytest.mark.parametrize(
    "consumer",
    [
        ExecutorScheduler._execute_executor,
        ExecutorScheduler._execute_docker_compose_executor,
        ExecutorScheduler._execute_helm_release_executor,
        ExecutorScheduler._execute_kubernetes_workload_executor,
        ExecutorScheduler._execute_portainer_stack_executor,
        deploy_tasks.DeployTasks._collect_admission_evidence,
        recovery_tasks.RecoveryTasks.execute,
        deployment_readiness.DeploymentReadiness._supplement,
        probes._capture,
        probes.capture_deployment_target,
        probes._probe_deployment,
    ],
)
def test_cached_runtime_consumer_has_full_operation_scope(consumer):
    # All wrappers share the decorator's code object, not merely a generic wraps
    # attribute. Verify actual lifetime protection without adding production flags.
    assert consumer.__code__ is runtime_adapter_scope(consumer).__code__
    assert inspect.iscoroutinefunction(consumer)


def test_cached_getters_and_native_adapter_helper_cannot_escape_lease_scope():
    modules = [
        scheduler_module,
        compose_module,
        helm_module,
        kubernetes_module,
        portainer_module,
        deploy_tasks,
        recovery_tasks,
        deployment_readiness,
        probes,
    ]
    found = []
    violations = []

    class Check(ast.NodeVisitor):
        def __init__(self, module):
            self.module = module
            self.functions = []

        def visit_AsyncFunctionDef(self, node):
            self.functions.append(node)
            self.generic_visit(node)
            self.functions.pop()

        visit_FunctionDef = visit_AsyncFunctionDef

        def visit_Call(self, node):
            fn = node.func
            getter = isinstance(fn, ast.Attribute) and fn.attr == "_get_adapter"
            helper = self.module is probes and isinstance(fn, ast.Name) and fn.id == "_adapter"
            if getter or helper:
                function = self.functions[-1] if self.functions else None
                scoped = function and any(
                    isinstance(d, ast.Name) and d.id == "runtime_adapter_scope"
                    for d in function.decorator_list
                )
                # _adapter is an async factory, not an ownership boundary. Its
                # callers must keep the lease alive across their subsequent calls.
                factory = (
                    getter and self.module is probes and function and function.name == "_adapter"
                )
                found.append((self.module.__name__, function.name if function else None))
                if not scoped and not factory:
                    violations.append((self.module.__name__, node.lineno))
            self.generic_visit(node)

    for module in modules:
        Check(module).visit(ast.parse(inspect.getsource(module)))
    assert found, "guard must inspect actual cached-adapter calls"
    assert not violations, f"cached adapter consumer lost its lease: {violations}"
