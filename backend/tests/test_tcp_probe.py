"""TCPProbe unit tests (Req 5.*)."""

from __future__ import annotations

import asyncio
import socket
from datetime import datetime
from typing import Any

import pytest

from releasetracker.config import (
    ExecutorConfig,
    ExecutorServiceBinding,
    HealthCheckProfile,
    HealthCheckTcpConfig,
    RuntimeConnectionConfig,
)
from releasetracker.executors.base import BaseRuntimeAdapter
from releasetracker.executors.health_check.host_resolver import ProbeHost
from releasetracker.executors.health_check.tcp_probe import TCPProbe
from releasetracker.executors.health_check.types import HealthCheckContext


class _FakeResolverAdapter(BaseRuntimeAdapter):
    def __init__(self, runtime_connection, hosts: list[ProbeHost]):
        super().__init__(runtime_connection)
        self._hosts = hosts

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

    async def resolve_probe_hosts(self, target_ref, *, services=None, default_port=None):
        return list(self._hosts)


def _make_profile(port: int = 6379, **overrides) -> HealthCheckProfile:
    import releasetracker.config as config_module

    config_module._PHASE_D_ENABLED = True
    return HealthCheckProfile(
        strategy="tcp",
        grace_period_seconds=0,
        attempt_timeout_seconds=overrides.pop("attempt_timeout_seconds", 5),
        interval_seconds=1,
        probe_window_seconds=overrides.pop("probe_window_seconds", 60),
        failure_policy="mark_failed",
        tcp=HealthCheckTcpConfig(port=port),
        services=overrides.pop("services", None),
    )


def _make_manual_profile(host: str, port: int, **overrides) -> HealthCheckProfile:
    return HealthCheckProfile(
        strategy="manual_tcp",
        grace_period_seconds=0,
        attempt_timeout_seconds=overrides.pop("attempt_timeout_seconds", 5),
        interval_seconds=1,
        probe_window_seconds=overrides.pop("probe_window_seconds", 60),
        failure_policy="mark_failed",
        tcp=HealthCheckTcpConfig(host=host, port=port),
        services=overrides.pop("services", None),
    )


def _context(
    profile: HealthCheckProfile,
    hosts: list[ProbeHost],
    *,
    target_ref: dict[str, Any] | None = None,
    service_bindings: list[ExecutorServiceBinding] | None = None,
) -> HealthCheckContext:
    runtime = RuntimeConnectionConfig(
        id=1,
        name="docker-local",
        type="docker",
        enabled=True,
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={"token": "x"},
    )
    adapter = _FakeResolverAdapter(runtime, hosts)
    executor = ExecutorConfig(
        id=1,
        name="tcp-executor",
        runtime_type="docker",
        runtime_connection_id=1,
        tracker_name="tracker",
        tracker_source_id=1 if not service_bindings else None,
        channel_name="stable" if not service_bindings else None,
        enabled=True,
        update_mode="manual",
        target_ref=target_ref or {"mode": "container", "container_id": "c1"},
        service_bindings=service_bindings or [],
        health_check=profile,
    )
    return HealthCheckContext(
        executor_config=executor,
        adapter=adapter,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 8, 12, 0, 0),
        baseline={},
    )


# ---- Helpers: tiny in-process TCP listener ------------------------------


async def _start_listener() -> tuple[asyncio.AbstractServer, int]:
    """Start a TCP listener bound to an ephemeral port and return ``(server, port)``."""

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    server = await asyncio.start_server(_handle, host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]
    return server, port


def _closed_port() -> int:
    """Return a port that is almost certainly refused: bind and release."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return port


# ---- Tests --------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_tcp_probe_uses_configured_host_without_runtime_resolution():
    server, port = await _start_listener()
    try:
        profile = _make_manual_profile("127.0.0.1", port)
        ctx = _context(profile, [ProbeHost(service=None, host="unreachable.invalid", port=1)])
        result = await TCPProbe(manual=True).attempt(ctx)
        assert result.healthy is True
        assert result.detail["tcp"][0]["host"] == "127.0.0.1"
        assert result.detail["tcp"][0]["port"] == port
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_tcp_probe_healthy_against_live_listener():
    server, port = await _start_listener()
    try:
        profile = _make_profile(port=port)
        ctx = _context(profile, [ProbeHost(service=None, host="127.0.0.1", port=port)])
        result = await TCPProbe().attempt(ctx)
        assert result.healthy is True
        assert result.detail["tcp"][0]["host"] == "127.0.0.1"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_tcp_probe_connection_refused():
    profile = _make_profile(port=_closed_port())
    ctx = _context(
        profile, [ProbeHost(service=None, host="127.0.0.1", port=profile.tcp.port)]
    )
    result = await TCPProbe().attempt(ctx)
    assert result.healthy is False
    assert result.error_category == "connection_refused"


@pytest.mark.asyncio
async def test_tcp_probe_timeout_classified():
    # TEST-NET-1 (RFC 5737) — should be unroutable so connect will hang until timeout.
    profile = _make_profile(port=80, attempt_timeout_seconds=1)
    ctx = _context(profile, [ProbeHost(service=None, host="192.0.2.1", port=80)])
    result = await TCPProbe().attempt(ctx)
    assert result.healthy is False
    assert result.error_category in {"timeout", "network_unreachable"}


@pytest.mark.asyncio
async def test_tcp_probe_dns_failure():
    profile = _make_profile(port=80, attempt_timeout_seconds=2)
    ctx = _context(
        profile,
        [ProbeHost(service=None, host="nonexistent-host-abcdefg.invalid", port=80)],
    )
    result = await TCPProbe().attempt(ctx)
    assert result.healthy is False
    # Either gaierror (dns_failure) or, on some resolvers, OSError wrapped
    # as network_unreachable. Both are acceptable non-OK transport
    # classifications per Req 5.5.
    assert result.error_category in {"dns_failure", "network_unreachable"}


@pytest.mark.asyncio
async def test_tcp_probe_host_unresolvable_when_resolver_fails():
    class _FailingResolver(_FakeResolverAdapter):
        async def resolve_probe_hosts(self, target_ref, *, services=None, default_port=None):
            raise ValueError("no host")

    runtime = RuntimeConnectionConfig(
        id=1,
        name="docker-local",
        type="docker",
        enabled=True,
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={"token": "x"},
    )
    adapter = _FailingResolver(runtime, [])
    profile = _make_profile(port=6379)
    executor = ExecutorConfig(
        id=1,
        name="tcp-failing",
        runtime_type="docker",
        runtime_connection_id=1,
        tracker_name="tracker",
        tracker_source_id=1,
        channel_name="stable",
        enabled=True,
        update_mode="manual",
        target_ref={"mode": "container", "container_id": "c1"},
        health_check=profile,
    )
    ctx = HealthCheckContext(
        executor_config=executor,
        adapter=adapter,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 8, 12, 0, 0),
        baseline={},
    )

    result = await TCPProbe().attempt(ctx)
    assert result.healthy is False
    assert result.error_category == "host_unresolvable"


@pytest.mark.asyncio
async def test_tcp_probe_grouped_mode_all_services_must_pass():
    # api listener is live; worker points at a refused port. Aggregate
    # unhealthy with per-service results.
    server, api_port = await _start_listener()
    closed_port = _closed_port()
    try:
        profile = _make_profile(port=api_port)
        service_bindings = [
            ExecutorServiceBinding(service="api", tracker_source_id=1, channel_name="stable"),
            ExecutorServiceBinding(service="worker", tracker_source_id=1, channel_name="stable"),
        ]
        ctx = _context(
            profile,
            [
                ProbeHost(service="api", host="127.0.0.1", port=api_port),
                ProbeHost(service="worker", host="127.0.0.1", port=closed_port),
            ],
            target_ref={
                "mode": "docker_compose",
                "project": "acme",
                "services": [
                    {"service": "api", "image": "acme/api:1"},
                    {"service": "worker", "image": "acme/worker:1"},
                ],
                "service_count": 2,
            },
            service_bindings=service_bindings,
        )

        result = await TCPProbe().attempt(ctx)
        assert result.healthy is False
        assert result.per_service["api"].healthy is True
        assert result.per_service["worker"].healthy is False
        assert result.per_service["worker"].error_category == "connection_refused"
    finally:
        server.close()
        await server.wait_closed()
