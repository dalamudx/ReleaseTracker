"""AutoProbe unit tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest

from releasetracker.config import (
    ExecutorConfig,
    ExecutorServiceBinding,
    HealthCheckProfile,
    RuntimeConnectionConfig,
)
from releasetracker.executors.base import BaseRuntimeAdapter
from releasetracker.executors.health_check.auto_probe import AutoProbe
from releasetracker.executors.health_check.host_resolver import ProbeHost
from releasetracker.executors.health_check.types import HealthCheckContext, ProbeAttemptResult


class _AutoAdapter(BaseRuntimeAdapter):
    def __init__(
        self,
        runtime_connection,
        *,
        has_healthcheck: bool = False,
        hosts: list[ProbeHost] | None = None,
        host_error: Exception | None = None,
    ) -> None:
        super().__init__(runtime_connection)
        self.has_healthcheck = has_healthcheck
        self.hosts = hosts or []
        self.host_error = host_error
        self.native_calls = 0
        self.host_calls = 0

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

    async def has_runtime_native_healthcheck(self, target_ref, *, services=None) -> bool:
        return self.has_healthcheck

    async def probe_runtime_native_health(self, target_ref, *, baseline, services=None):
        self.native_calls += 1
        return ProbeAttemptResult(healthy=True, detail={"runtime": "ok"})

    async def resolve_auto_probe_hosts(self, target_ref, *, services=None, default_port=None):
        self.host_calls += 1
        if self.host_error is not None:
            raise self.host_error
        return list(self.hosts)


class _KubernetesAutoAdapter(_AutoAdapter):
    async def resolve_auto_probe_hosts(self, target_ref, *, services=None, default_port=None):
        raise AssertionError("kubernetes auto must not resolve host ports")


async def _start_listener() -> tuple[asyncio.AbstractServer, int]:
    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    sock = server.sockets[0]
    return server, int(sock.getsockname()[1])


def _runtime(runtime_type: str = "docker") -> RuntimeConnectionConfig:
    return RuntimeConnectionConfig(
        id=1,
        name=f"{runtime_type}-runtime",
        type=runtime_type,
        enabled=True,
        config={"socket": "unix:///var/run/docker.sock"} if runtime_type in {"docker", "podman"} else {"in_cluster": True},
        secrets={"token": "x"} if runtime_type in {"docker", "podman"} else {},
    )


def _profile() -> HealthCheckProfile:
    return HealthCheckProfile(
        strategy="auto",
        grace_period_seconds=0,
        attempt_timeout_seconds=1,
        interval_seconds=1,
        probe_window_seconds=10,
        failure_policy="mark_failed",
    )


def _context(adapter: BaseRuntimeAdapter, *, runtime_type: str = "docker") -> HealthCheckContext:
    target_ref: dict[str, Any] = {"mode": "container", "container_id": "c1"}
    if runtime_type == "kubernetes":
        target_ref = {
            "mode": "kubernetes_workload",
            "namespace": "prod",
            "kind": "Deployment",
            "name": "api",
        }
    return HealthCheckContext(
        executor_config=ExecutorConfig(
            id=1,
            name="auto-executor",
            runtime_type=runtime_type,
            runtime_connection_id=1,
            tracker_name="tracker",
            tracker_source_id=1 if runtime_type != "kubernetes" else None,
            channel_name="stable" if runtime_type != "kubernetes" else None,
            enabled=True,
            update_mode="manual",
            target_ref=target_ref,
            service_bindings=(
                [
                    ExecutorServiceBinding(
                        service="api",
                        tracker_source_id=1,
                        channel_name="stable",
                    )
                ]
                if runtime_type == "kubernetes"
                else []
            ),
            health_check=_profile(),
        ),
        adapter=adapter,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 11, 12, 0, 0),
        baseline={},
    )


@pytest.mark.asyncio
async def test_auto_uses_runtime_native_when_healthcheck_exists():
    adapter = _AutoAdapter(_runtime(), has_healthcheck=True)
    result = await AutoProbe().attempt(_context(adapter))
    assert result.healthy is True
    assert adapter.native_calls == 1
    assert adapter.host_calls == 0
    assert result.detail["auto"]["selected"] == "runtime_native_healthcheck"


@pytest.mark.asyncio
async def test_auto_host_port_unresolvable_falls_back_to_runtime_native_when_no_ports():
    adapter = _AutoAdapter(_runtime(), host_error=ValueError("container has no published host ports"))
    result = await AutoProbe().attempt(_context(adapter))
    assert result.healthy is True
    assert adapter.native_calls == 1
    assert adapter.host_calls == 1
    assert result.detail["auto"]["selected"] == "runtime_native_fallback"


@pytest.mark.asyncio
async def test_auto_host_port_success_uses_resolved_tcp_target():
    server, port = await _start_listener()
    try:
        adapter = _AutoAdapter(_runtime(), hosts=[ProbeHost(service=None, host="127.0.0.1", port=port)])
        result = await AutoProbe().attempt(_context(adapter))
        assert result.healthy is True
        assert adapter.native_calls == 0
        assert adapter.host_calls == 1
        assert result.detail["auto"]["selected"] == "host_port_tcp"
        assert result.detail["tcp"][0]["host"] == "127.0.0.1"
        assert result.detail["tcp"][0]["port"] == port
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_auto_ambiguous_host_ports_return_host_unresolvable_without_native_fallback():
    adapter = _AutoAdapter(
        _runtime(),
        host_error=ValueError(
            "multiple published host ports found; configure a container port for auto probing"
        ),
    )
    result = await AutoProbe().attempt(_context(adapter))
    assert result.healthy is False
    assert result.error_category == "host_unresolvable"
    assert adapter.native_calls == 0
    assert adapter.host_calls == 1
    assert "multiple published host ports" in (result.last_error or "")


@pytest.mark.asyncio
async def test_kubernetes_auto_does_not_use_host_port_resolution():
    adapter = _KubernetesAutoAdapter(_runtime("kubernetes"))
    result = await AutoProbe().attempt(_context(adapter, runtime_type="kubernetes"))
    assert result.healthy is True
    assert adapter.native_calls == 1
