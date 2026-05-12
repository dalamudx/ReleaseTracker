"""Unit tests for the Helm release status health probe."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

from releasetracker.config import (
    ExecutorConfig,
    HealthCheckProfile,
    RuntimeConnectionConfig,
)
from releasetracker.executors.health_check.helm_status_probe import HelmStatusProbe
from releasetracker.executors.health_check.types import HealthCheckContext, ProbeAttemptResult


class _FakeHelmAdapter:
    """Minimal stand-in for ``KubernetesRuntimeAdapter`` exposing only the
    method that ``HelmStatusProbe`` reads."""

    def __init__(self, *, output: str | None = None, error: Exception | None = None):
        self._output = output
        self._error = error
        self.calls: list[list[str]] = []

    def _run_helm_command(self, args: list[str]) -> str:
        self.calls.append(list(args))
        if self._error is not None:
            raise self._error
        assert self._output is not None
        return self._output


def _make_executor_config() -> ExecutorConfig:
    return ExecutorConfig(
        id=7,
        name="helm-executor",
        runtime_type="kubernetes",
        runtime_connection_id=1,
        tracker_name="chart-tracker",
        tracker_source_id=1,
        channel_name="stable",
        enabled=True,
        update_mode="manual",
        target_ref={
            "mode": "helm_release",
            "namespace": "prod",
            "release_name": "api",
        },
        health_check=HealthCheckProfile(
            strategy="helm_status",
            grace_period_seconds=1,
            attempt_timeout_seconds=5,
            interval_seconds=1,
            probe_window_seconds=60,
            failure_policy="mark_failed",
        ),
    )


def _make_context(adapter: Any) -> HealthCheckContext:
    return HealthCheckContext(
        executor_config=_make_executor_config(),
        adapter=adapter,
        run_id=1,
        update_phase_end_at=datetime(2026, 5, 8, 12, 0, 0),
        baseline={},
    )


def _status_payload(status: str) -> str:
    return json.dumps({"info": {"status": status}})


@pytest.mark.asyncio
async def test_helm_status_deployed_is_healthy():
    adapter = _FakeHelmAdapter(output=_status_payload("deployed"))
    result = await HelmStatusProbe().attempt(_make_context(adapter))

    assert isinstance(result, ProbeAttemptResult)
    assert result.healthy is True
    assert result.terminate_phase is False
    assert result.detail["helm_status"] == "deployed"
    assert adapter.calls == [
        ["status", "api", "--namespace", "prod", "--output", "json"]
    ]


@pytest.mark.parametrize(
    "status", ["pending-install", "pending-upgrade", "pending-rollback"]
)
@pytest.mark.asyncio
async def test_helm_pending_statuses_retry(status: str):
    adapter = _FakeHelmAdapter(output=_status_payload(status))
    result = await HelmStatusProbe().attempt(_make_context(adapter))

    assert result.healthy is False
    assert result.terminate_phase is False
    assert result.error_category == "helm_pending"


@pytest.mark.asyncio
async def test_helm_failed_status_short_circuits_phase():
    adapter = _FakeHelmAdapter(output=_status_payload("failed"))
    result = await HelmStatusProbe().attempt(_make_context(adapter))

    assert result.healthy is False
    assert result.error_category == "helm_failed"
    assert result.terminate_phase is True


@pytest.mark.parametrize(
    "status", ["superseded", "uninstalled", "uninstalling", "unknown"]
)
@pytest.mark.asyncio
async def test_helm_unknown_status_retries(status: str):
    adapter = _FakeHelmAdapter(output=_status_payload(status))
    result = await HelmStatusProbe().attempt(_make_context(adapter))

    assert result.healthy is False
    assert result.error_category == "helm_unknown_status"
    assert result.terminate_phase is False
    assert result.detail["helm_status"] == status


@pytest.mark.asyncio
async def test_helm_command_error_maps_to_runtime_api_error():
    adapter = _FakeHelmAdapter(error=RuntimeError("helm binary not found"))
    result = await HelmStatusProbe().attempt(_make_context(adapter))

    assert result.healthy is False
    assert result.error_category == "runtime_api_error"
    assert "helm binary not found" in (result.last_error or "")


@pytest.mark.asyncio
async def test_helm_malformed_json_maps_to_runtime_api_error():
    adapter = _FakeHelmAdapter(output="not-json")
    result = await HelmStatusProbe().attempt(_make_context(adapter))

    assert result.healthy is False
    assert result.error_category == "runtime_api_error"
    assert "not JSON" in (result.last_error or "")


@pytest.mark.asyncio
async def test_helm_probe_requires_helm_release_target_ref():
    adapter = _FakeHelmAdapter(output=_status_payload("deployed"))
    ctx = _make_context(adapter)
    # Swap in a malformed target_ref to simulate misconfiguration.
    ctx.executor_config.target_ref.pop("release_name", None)
    result = await HelmStatusProbe().attempt(ctx)

    assert result.healthy is False
    assert result.error_category == "runtime_api_error"
    assert "release_name" in (result.last_error or "")


@pytest.mark.asyncio
async def test_helm_probe_requires_adapter_with_run_helm_command():
    class _NoHelmAdapter:
        pass

    result = await HelmStatusProbe().attempt(_make_context(_NoHelmAdapter()))
    assert result.healthy is False
    assert "_run_helm_command" in (result.last_error or "")
