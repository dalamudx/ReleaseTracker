"""Unit tests for the runtime-native health probe (Req 3.1-3.7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest

from releasetracker.config import ExecutorConfig, HealthCheckProfile, RuntimeConnectionConfig
from releasetracker.executors.container_runtime import _ContainerRuntimeAdapter
from releasetracker.executors.health_check.probe import RuntimeNativeProbe
from releasetracker.executors.health_check.types import HealthCheckContext, ProbeAttemptResult
from releasetracker.executors.kubernetes import KubernetesRuntimeAdapter


# ---- Helpers --------------------------------------------------------------


def _make_profile(**overrides: Any) -> HealthCheckProfile:
    base = dict(
        strategy="runtime_native",
        use_default_strategy=False,
        grace_period_seconds=1,
        attempt_timeout_seconds=5,
        interval_seconds=1,
        probe_window_seconds=60,
        failure_policy="mark_failed",
    )
    base.update(overrides)
    return HealthCheckProfile(**base)


def _make_runtime_connection(runtime_type: str) -> RuntimeConnectionConfig:
    if runtime_type in {"docker", "podman"}:
        return RuntimeConnectionConfig(
            id=1,
            name=f"{runtime_type}-local",
            type=runtime_type,
            enabled=True,
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={"token": "x"},
        )
    if runtime_type == "kubernetes":
        return RuntimeConnectionConfig(
            id=1,
            name="k8s-in-cluster",
            type="kubernetes",
            enabled=True,
            config={"in_cluster": True},
            credential_id=None,
            secrets={},
        )
    raise NotImplementedError(runtime_type)


def _make_executor_config(runtime_type: str, target_ref: dict[str, Any]) -> ExecutorConfig:
    from releasetracker.config import ExecutorServiceBinding

    mode = target_ref.get("mode")
    service_bindings: list[ExecutorServiceBinding] = []
    if mode in {"docker_compose", "portainer_stack", "kubernetes_workload"}:
        service_bindings = [
            ExecutorServiceBinding(
                service=entry["service"],
                tracker_source_id=1,
                channel_name="stable",
            )
            for entry in target_ref.get("services", [])
            if isinstance(entry, dict) and entry.get("service")
        ]

    return ExecutorConfig(
        id=42,
        name=f"{runtime_type}-executor",
        runtime_type=runtime_type,
        runtime_connection_id=1,
        tracker_name="tracker",
        tracker_source_id=1 if not service_bindings else None,
        channel_name="stable" if not service_bindings else None,
        enabled=True,
        update_mode="manual",
        target_ref=target_ref,
        service_bindings=service_bindings,
        health_check=_make_profile(),
    )


@dataclass
class _FakeContainer:
    id: str = "c1"
    name: str = "api"
    attrs: dict[str, Any] = field(default_factory=dict)


class _FakeContainersCollection:
    def __init__(self, containers: dict[str, _FakeContainer]) -> None:
        self._containers = containers

    def get(self, identifier: str) -> _FakeContainer:
        container = self._containers.get(identifier)
        if container is None:
            raise KeyError(identifier)
        return container


class _FakeDockerClient:
    def __init__(self, containers: dict[str, _FakeContainer]) -> None:
        self.containers = _FakeContainersCollection(containers)


class _StubContainerAdapter(_ContainerRuntimeAdapter):
    """Concrete container adapter with the minimum plumbing to drive probes."""

    def _create_client(self):  # pragma: no cover - exercised indirectly
        raise RuntimeError("tests must inject a client explicitly")


# ---- Container probe -----------------------------------------------------


@pytest.mark.asyncio
async def test_auto_host_port_resolution_uses_runtime_socket_host_and_published_port():
    runtime = _make_runtime_connection("docker")
    runtime.config["socket"] = "tcp://10.10.20.53:2375"
    client = _FakeDockerClient(
        {
            "c1": _FakeContainer(
                attrs={"NetworkSettings": {"Ports": {"8080/tcp": [{"HostPort": "18080"}]}}}
            )
        }
    )
    adapter = _StubContainerAdapter(runtime, client=client)

    hosts = await adapter.resolve_auto_probe_hosts(
        {"mode": "container", "container_id": "c1"},
        default_port=8080,
    )

    assert hosts[0].host == "10.10.20.53"
    assert hosts[0].port == 18080


@pytest.mark.asyncio
async def test_auto_host_port_resolution_rejects_ambiguous_ports_without_container_port():
    client = _FakeDockerClient(
        {
            "c1": _FakeContainer(
                attrs={
                    "NetworkSettings": {
                        "Ports": {
                            "8080/tcp": [{"HostPort": "18080"}],
                            "9090/tcp": [{"HostPort": "19090"}],
                        }
                    }
                }
            )
        }
    )
    adapter = _StubContainerAdapter(_make_runtime_connection("docker"), client=client)

    with pytest.raises(ValueError, match="multiple published host ports"):
        await adapter.resolve_auto_probe_hosts({"mode": "container", "container_id": "c1"})


@pytest.mark.asyncio
async def test_container_healthy_when_healthcheck_reports_healthy():
    client = _FakeDockerClient(
        {
            "c1": _FakeContainer(
                attrs={
                    "State": {
                        "Status": "running",
                        "Running": True,
                        "Health": {"Status": "healthy"},
                    },
                    "RestartCount": 0,
                }
            )
        }
    )
    adapter = _StubContainerAdapter(_make_runtime_connection("docker"), client=client)
    executor = _make_executor_config(
        "docker", {"mode": "container", "container_id": "c1", "container_name": "api"}
    )
    ctx = HealthCheckContext(
        executor_config=executor,
        adapter=adapter,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 8, 12, 0, 0),
        baseline={"restart_count": 0},
    )

    result = await RuntimeNativeProbe().attempt(ctx)

    assert isinstance(result, ProbeAttemptResult)
    assert result.healthy is True
    assert result.detail["health"] == "healthy"


@pytest.mark.asyncio
async def test_container_unhealthy_when_state_is_not_running():
    client = _FakeDockerClient(
        {
            "c1": _FakeContainer(
                attrs={"State": {"Status": "restarting", "Running": False}, "RestartCount": 3}
            )
        }
    )
    adapter = _StubContainerAdapter(_make_runtime_connection("docker"), client=client)
    executor = _make_executor_config(
        "docker", {"mode": "container", "container_id": "c1", "container_name": "api"}
    )
    ctx = HealthCheckContext(
        executor_config=executor,
        adapter=adapter,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 8, 12, 0, 0),
        baseline={"restart_count": 2},
    )

    result = await RuntimeNativeProbe().attempt(ctx)
    assert result.healthy is False
    assert result.error_category == "runtime_api_error"
    assert "expected 'running'" in (result.last_error or "")


@pytest.mark.asyncio
async def test_container_unhealthy_when_restart_count_increased_without_healthcheck():
    client = _FakeDockerClient(
        {
            "c1": _FakeContainer(
                attrs={"State": {"Status": "running", "Running": True}, "RestartCount": 5}
            )
        }
    )
    adapter = _StubContainerAdapter(_make_runtime_connection("docker"), client=client)
    executor = _make_executor_config(
        "docker", {"mode": "container", "container_id": "c1", "container_name": "api"}
    )
    ctx = HealthCheckContext(
        executor_config=executor,
        adapter=adapter,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 8, 12, 0, 0),
        baseline={"restart_count": 3},
    )

    result = await RuntimeNativeProbe().attempt(ctx)
    assert result.healthy is False
    assert "restart_count" in (result.last_error or "")


@pytest.mark.asyncio
async def test_container_healthy_when_no_healthcheck_and_restart_stable():
    client = _FakeDockerClient(
        {
            "c1": _FakeContainer(
                attrs={"State": {"Status": "running", "Running": True}, "RestartCount": 2}
            )
        }
    )
    adapter = _StubContainerAdapter(_make_runtime_connection("docker"), client=client)
    executor = _make_executor_config(
        "docker", {"mode": "container", "container_id": "c1", "container_name": "api"}
    )
    ctx = HealthCheckContext(
        executor_config=executor,
        adapter=adapter,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 8, 12, 0, 0),
        baseline={"restart_count": 2},
    )

    result = await RuntimeNativeProbe().attempt(ctx)
    assert result.healthy is True


@pytest.mark.asyncio
async def test_container_probe_maps_adapter_error_to_runtime_api_error():
    # Client raises on lookup → adapter returns unhealthy with runtime_api_error.
    class _RaisingClient:
        @property
        def containers(self):
            raise RuntimeError("socket closed")

    adapter = _StubContainerAdapter(_make_runtime_connection("docker"), client=_RaisingClient())
    executor = _make_executor_config(
        "docker", {"mode": "container", "container_id": "c1", "container_name": "api"}
    )
    ctx = HealthCheckContext(
        executor_config=executor,
        adapter=adapter,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 8, 12, 0, 0),
        baseline={"restart_count": 0},
    )

    result = await RuntimeNativeProbe().attempt(ctx)
    assert result.healthy is False
    assert result.error_category == "runtime_api_error"


# ---- Kubernetes probe ----------------------------------------------------


class _FakeAppsApi:
    def __init__(self, deployments: dict[tuple[str, str], dict]):
        self._deployments = deployments

    def read_namespaced_deployment(self, name: str, namespace: str):
        key = (namespace, name)
        payload = self._deployments.get(key)
        if payload is None:
            raise KeyError(key)
        return _ObjectView(payload)

    def read_namespaced_stateful_set(self, name: str, namespace: str):
        return self.read_namespaced_deployment(name, namespace)

    def read_namespaced_daemon_set(self, name: str, namespace: str):
        return self.read_namespaced_deployment(name, namespace)


class _ObjectView:
    """Turn a nested dict into a getattr-friendly view matching the K8s client."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        for key, value in data.items():
            setattr(self, key, _ObjectView(value) if isinstance(value, dict) else value)

    def to_dict(self) -> dict[str, Any]:
        return self._data


class _StubKubernetesAdapter(KubernetesRuntimeAdapter):
    """Kubernetes adapter that short-circuits workload reads through an injected
    ``_get_workload`` override so probe tests never touch a real cluster."""

    def __init__(self, runtime_connection, workload: dict[str, Any]) -> None:
        super().__init__(runtime_connection)
        self._workload = workload

    def _get_workload(self, kind: str, name: str, namespace: str) -> dict[str, Any]:
        if self._workload is None:
            raise RuntimeError("workload not found")
        return self._workload


def _deployment_workload(
    *,
    generation: int,
    observed_generation: int,
    progressing_reason: str = "NewReplicaSetAvailable",
    desired: int = 3,
    ready: int = 3,
) -> dict[str, Any]:
    return {
        "kind": "Deployment",
        "metadata": {"name": "api", "namespace": "prod", "generation": generation},
        "spec": {"replicas": desired},
        "status": {
            "observedGeneration": observed_generation,
            "readyReplicas": ready,
            "conditions": [
                {"type": "Progressing", "status": "True", "reason": progressing_reason}
            ],
        },
    }


@pytest.mark.asyncio
async def test_kubernetes_deployment_healthy_when_rolled_out():
    adapter = _StubKubernetesAdapter(
        _make_runtime_connection("kubernetes"),
        _deployment_workload(generation=5, observed_generation=5),
    )
    executor = _make_executor_config(
        "kubernetes",
        {
            "mode": "kubernetes_workload",
            "namespace": "prod",
            "kind": "Deployment",
            "name": "api",
            "services": [{"service": "api", "image": "acme/api:1"}],
            "service_count": 1,
        },
    )
    ctx = HealthCheckContext(
        executor_config=executor,
        adapter=adapter,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 8, 12, 0, 0),
        baseline={"generation": 5},
    )

    result = await RuntimeNativeProbe().attempt(ctx)
    assert result.healthy is True


@pytest.mark.asyncio
async def test_kubernetes_deployment_unhealthy_when_observed_generation_lags():
    adapter = _StubKubernetesAdapter(
        _make_runtime_connection("kubernetes"),
        _deployment_workload(generation=6, observed_generation=5),
    )
    executor = _make_executor_config(
        "kubernetes",
        {
            "mode": "kubernetes_workload",
            "namespace": "prod",
            "kind": "Deployment",
            "name": "api",
            "services": [{"service": "api", "image": "acme/api:1"}],
            "service_count": 1,
        },
    )
    ctx = HealthCheckContext(
        executor_config=executor,
        adapter=adapter,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 8, 12, 0, 0),
        baseline={"generation": 6},
    )

    result = await RuntimeNativeProbe().attempt(ctx)
    assert result.healthy is False
    assert "observed_generation" in (result.last_error or "")


@pytest.mark.asyncio
async def test_kubernetes_deployment_unhealthy_when_ready_below_desired():
    adapter = _StubKubernetesAdapter(
        _make_runtime_connection("kubernetes"),
        _deployment_workload(
            generation=5, observed_generation=5, desired=3, ready=1
        ),
    )
    executor = _make_executor_config(
        "kubernetes",
        {
            "mode": "kubernetes_workload",
            "namespace": "prod",
            "kind": "Deployment",
            "name": "api",
            "services": [{"service": "api", "image": "acme/api:1"}],
            "service_count": 1,
        },
    )
    ctx = HealthCheckContext(
        executor_config=executor,
        adapter=adapter,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 8, 12, 0, 0),
        baseline={"generation": 5},
    )

    result = await RuntimeNativeProbe().attempt(ctx)
    assert result.healthy is False
    assert "ready_replicas" in (result.last_error or "")


@pytest.mark.asyncio
async def test_kubernetes_statefulset_healthy_when_updated():
    workload = {
        "kind": "StatefulSet",
        "metadata": {"name": "db", "namespace": "prod", "generation": 3},
        "spec": {"replicas": 2},
        "status": {
            "observedGeneration": 3,
            "currentRevision": "rev-2",
            "updateRevision": "rev-2",
            "updatedReplicas": 2,
            "readyReplicas": 2,
        },
    }
    adapter = _StubKubernetesAdapter(_make_runtime_connection("kubernetes"), workload)
    executor = _make_executor_config(
        "kubernetes",
        {
            "mode": "kubernetes_workload",
            "namespace": "prod",
            "kind": "StatefulSet",
            "name": "db",
            "services": [{"service": "db", "image": "acme/db:1"}],
            "service_count": 1,
        },
    )
    ctx = HealthCheckContext(
        executor_config=executor,
        adapter=adapter,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 8, 12, 0, 0),
        baseline={"generation": 3},
    )

    result = await RuntimeNativeProbe().attempt(ctx)
    assert result.healthy is True


@pytest.mark.asyncio
async def test_kubernetes_daemonset_unhealthy_when_not_all_nodes_updated():
    workload = {
        "kind": "DaemonSet",
        "metadata": {"name": "agent", "namespace": "prod", "generation": 4},
        "spec": {},
        "status": {
            "observedGeneration": 4,
            "desiredNumberScheduled": 5,
            "updatedNumberScheduled": 3,
            "numberReady": 3,
        },
    }
    adapter = _StubKubernetesAdapter(_make_runtime_connection("kubernetes"), workload)
    executor = _make_executor_config(
        "kubernetes",
        {
            "mode": "kubernetes_workload",
            "namespace": "prod",
            "kind": "DaemonSet",
            "name": "agent",
            "services": [{"service": "agent", "image": "acme/agent:1"}],
            "service_count": 1,
        },
    )
    ctx = HealthCheckContext(
        executor_config=executor,
        adapter=adapter,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 8, 12, 0, 0),
        baseline={"generation": 4},
    )

    result = await RuntimeNativeProbe().attempt(ctx)
    assert result.healthy is False
    assert "DaemonSet" in (result.last_error or "")


@pytest.mark.asyncio
async def test_probe_maps_not_implemented_error_to_runtime_api_error():
    from releasetracker.executors.base import BaseRuntimeAdapter

    class _UnimplementedAdapter(BaseRuntimeAdapter):
        async def discover_targets(self):
            return []

        async def validate_target_ref(self, target_ref):
            return None

        async def get_current_image(self, target_ref):
            return ""

        async def capture_snapshot(self, target_ref, current_image):
            return {}

        async def validate_snapshot(self, target_ref, snapshot):
            return None

        async def update_image(self, target_ref, new_image):
            raise NotImplementedError

    adapter = _UnimplementedAdapter(_make_runtime_connection("docker"))
    executor = _make_executor_config(
        "docker", {"mode": "container", "container_id": "c1", "container_name": "api"}
    )
    ctx = HealthCheckContext(
        executor_config=executor,
        adapter=adapter,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 8, 12, 0, 0),
        baseline={"restart_count": 0},
    )

    result = await RuntimeNativeProbe().attempt(ctx)
    assert result.healthy is False
    assert result.error_category == "runtime_api_error"
