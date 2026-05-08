"""HTTPProbe unit tests (Req 4.*)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import pytest

from releasetracker.config import (
    ExecutorConfig,
    ExecutorServiceBinding,
    HealthCheckHttpConfig,
    HealthCheckProfile,
    RuntimeConnectionConfig,
)
from releasetracker.executors.base import BaseRuntimeAdapter
from releasetracker.executors.health_check.host_resolver import ProbeHost
from releasetracker.executors.health_check.http_probe import HTTPProbe
from releasetracker.executors.health_check.types import HealthCheckContext


class _FakeHostResolverAdapter(BaseRuntimeAdapter):
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


def _patched_client_factory(handler):
    """Return an httpx.AsyncClient factory that injects a MockTransport.

    The factory swallows ``verify`` / ``timeout`` passed by the probe and
    forwards everything else to the ORIGINAL ``httpx.AsyncClient`` backed
    by a ``MockTransport``. We must capture the original class here,
    before monkeypatch replaces it with this factory, otherwise the
    inner call recurses into this factory forever.
    """
    original_client_cls = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs.pop("verify", None)
        kwargs.pop("timeout", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client_cls(*args, **kwargs)

    return _factory


def _make_profile(**overrides) -> HealthCheckProfile:
    http_defaults = dict(path="/healthz")
    http_overrides = overrides.pop("http", {})
    http_config = HealthCheckHttpConfig(**{**http_defaults, **http_overrides})
    base = dict(
        strategy="http",
        grace_period_seconds=0,
        attempt_timeout_seconds=5,
        interval_seconds=1,
        probe_window_seconds=60,
        failure_policy="mark_failed",
        http=http_config,
    )
    base.update(overrides)
    # Enable Phase D so the validator accepts strategy=http.
    import releasetracker.config as config_module

    config_module._PHASE_D_ENABLED = True
    return HealthCheckProfile(**base)


def _make_context(
    profile: HealthCheckProfile,
    hosts: list[ProbeHost],
    *,
    target_ref: dict[str, Any] | None = None,
    runtime_type: str = "docker",
    service_bindings: list[ExecutorServiceBinding] | None = None,
) -> HealthCheckContext:
    runtime_conn = RuntimeConnectionConfig(
        id=1,
        name=f"{runtime_type}-local",
        type=runtime_type,
        enabled=True,
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={"token": "x"},
    )
    adapter = _FakeHostResolverAdapter(runtime_conn, hosts)
    executor = ExecutorConfig(
        id=1,
        name="http-executor",
        runtime_type=runtime_type,
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


# ---- Tests --------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_probe_healthy_200(monkeypatch):
    def handler(request: httpx.Request):
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(
        "releasetracker.executors.health_check.http_probe.httpx.AsyncClient",
        _patched_client_factory(handler),
    )

    profile = _make_profile(http={"path": "/healthz", "port": 8080})
    ctx = _make_context(profile, [ProbeHost(service=None, host="10.0.0.5")])

    result = await HTTPProbe().attempt(ctx)

    assert result.healthy is True
    assert result.detail["http"][0]["http_last_status"] == 200
    assert result.detail["http"][0]["matched"] is True


@pytest.mark.asyncio
async def test_http_probe_rejects_unexpected_status(monkeypatch):
    def handler(request: httpx.Request):
        return httpx.Response(503, text="nope")

    monkeypatch.setattr(
        "releasetracker.executors.health_check.http_probe.httpx.AsyncClient",
        _patched_client_factory(handler),
    )

    profile = _make_profile(http={"path": "/healthz", "port": 8080})
    ctx = _make_context(profile, [ProbeHost(service=None, host="10.0.0.5")])

    result = await HTTPProbe().attempt(ctx)

    assert result.healthy is False
    assert result.error_category == "status_mismatch"
    assert "503" in (result.last_error or "")


@pytest.mark.asyncio
async def test_http_probe_expected_status_codes_pass(monkeypatch):
    def handler(request: httpx.Request):
        return httpx.Response(418, text="teapot")

    monkeypatch.setattr(
        "releasetracker.executors.health_check.http_probe.httpx.AsyncClient",
        _patched_client_factory(handler),
    )

    profile = _make_profile(
        http={"path": "/healthz", "port": 8080, "expected_status_codes": [418]}
    )
    ctx = _make_context(profile, [ProbeHost(service=None, host="10.0.0.5")])

    result = await HTTPProbe().attempt(ctx)
    assert result.healthy is True


@pytest.mark.asyncio
async def test_http_probe_expected_body_regex(monkeypatch):
    def handler(request: httpx.Request):
        return httpx.Response(200, text="service-version=9.9.9\n")

    monkeypatch.setattr(
        "releasetracker.executors.health_check.http_probe.httpx.AsyncClient",
        _patched_client_factory(handler),
    )

    profile = _make_profile(
        http={"path": "/version", "port": 8080, "expected_body_regex": r"9\.9\.\d+"}
    )
    ctx = _make_context(profile, [ProbeHost(service=None, host="10.0.0.5")])

    result = await HTTPProbe().attempt(ctx)
    assert result.healthy is True


@pytest.mark.asyncio
async def test_http_probe_body_regex_mismatch(monkeypatch):
    def handler(request: httpx.Request):
        return httpx.Response(200, text="different")

    monkeypatch.setattr(
        "releasetracker.executors.health_check.http_probe.httpx.AsyncClient",
        _patched_client_factory(handler),
    )

    profile = _make_profile(
        http={"path": "/version", "port": 8080, "expected_body_regex": r"\d+\.\d+\.\d+"}
    )
    ctx = _make_context(profile, [ProbeHost(service=None, host="10.0.0.5")])

    result = await HTTPProbe().attempt(ctx)
    assert result.healthy is False
    assert result.error_category == "body_mismatch"


@pytest.mark.asyncio
async def test_http_probe_classifies_connection_refused(monkeypatch):
    def handler(request: httpx.Request):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(
        "releasetracker.executors.health_check.http_probe.httpx.AsyncClient",
        _patched_client_factory(handler),
    )

    profile = _make_profile(http={"path": "/", "port": 8080})
    ctx = _make_context(profile, [ProbeHost(service=None, host="10.0.0.5")])

    result = await HTTPProbe().attempt(ctx)
    assert result.healthy is False
    assert result.error_category == "connection_refused"


@pytest.mark.asyncio
async def test_http_probe_classifies_dns_failure(monkeypatch):
    def handler(request: httpx.Request):
        raise httpx.ConnectError("name or service not known")

    monkeypatch.setattr(
        "releasetracker.executors.health_check.http_probe.httpx.AsyncClient",
        _patched_client_factory(handler),
    )

    profile = _make_profile(http={"path": "/", "port": 8080})
    ctx = _make_context(profile, [ProbeHost(service=None, host="bad-host")])

    result = await HTTPProbe().attempt(ctx)
    assert result.error_category == "dns_failure"


@pytest.mark.asyncio
async def test_http_probe_timeout(monkeypatch):
    def handler(request: httpx.Request):
        raise httpx.TimeoutException("too slow", request=request)

    monkeypatch.setattr(
        "releasetracker.executors.health_check.http_probe.httpx.AsyncClient",
        _patched_client_factory(handler),
    )

    profile = _make_profile(
        http={"path": "/slow", "port": 8080}, attempt_timeout_seconds=1
    )
    ctx = _make_context(profile, [ProbeHost(service=None, host="10.0.0.5")])

    result = await HTTPProbe().attempt(ctx)
    assert result.healthy is False
    assert result.error_category == "timeout"


@pytest.mark.asyncio
async def test_http_probe_body_truncation_flag(monkeypatch):
    large_body = b"a" * 70_000

    def handler(request: httpx.Request):
        return httpx.Response(200, content=large_body)

    monkeypatch.setattr(
        "releasetracker.executors.health_check.http_probe.httpx.AsyncClient",
        _patched_client_factory(handler),
    )

    profile = _make_profile(
        http={
            "path": "/",
            "port": 8080,
            # Regex that would not match the literal body so we verify
            # truncation happens even for status-only checks.
            "expected_body_regex": r"zzz",
        }
    )
    ctx = _make_context(profile, [ProbeHost(service=None, host="10.0.0.5")])

    result = await HTTPProbe().attempt(ctx)
    assert result.detail["http"][0]["body_truncated"] is True


@pytest.mark.asyncio
async def test_http_probe_host_unresolvable(monkeypatch):
    class _EmptyAdapter(_FakeHostResolverAdapter):
        async def resolve_probe_hosts(self, target_ref, *, services=None, default_port=None):
            raise ValueError("no host available")

    runtime_conn = RuntimeConnectionConfig(
        id=1,
        name="docker-local",
        type="docker",
        enabled=True,
        config={"socket": "unix:///var/run/docker.sock"},
        secrets={"token": "x"},
    )
    adapter = _EmptyAdapter(runtime_conn, [])
    profile = _make_profile(http={"path": "/", "port": 8080})
    executor = ExecutorConfig(
        id=1,
        name="empty",
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

    result = await HTTPProbe().attempt(ctx)
    assert result.healthy is False
    assert result.error_category == "host_unresolvable"


@pytest.mark.asyncio
async def test_http_probe_grouped_mode_all_services_must_pass(monkeypatch):
    # api returns 200, worker returns 503 → aggregate unhealthy with per-service results.
    def handler(request: httpx.Request):
        if "worker" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(200)

    monkeypatch.setattr(
        "releasetracker.executors.health_check.http_probe.httpx.AsyncClient",
        _patched_client_factory(handler),
    )

    profile = _make_profile(http={"path": "/healthz", "port": 8080})
    service_bindings = [
        ExecutorServiceBinding(service="api", tracker_source_id=1, channel_name="stable"),
        ExecutorServiceBinding(service="worker", tracker_source_id=1, channel_name="stable"),
    ]
    ctx = _make_context(
        profile,
        [
            ProbeHost(service="api", host="10.0.0.5"),
            ProbeHost(service="worker", host="worker.internal"),
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

    result = await HTTPProbe().attempt(ctx)
    assert result.healthy is False
    assert result.per_service is not None
    assert result.per_service["api"].healthy is True
    assert result.per_service["worker"].healthy is False
    assert result.per_service["worker"].error_category == "status_mismatch"
    assert result.error_category == "status_mismatch"
