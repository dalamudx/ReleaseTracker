"""Runtime control-plane timeout and retry policy tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from releasetracker.services.runtime_policy import (
    DEFAULT_RUNTIME_READ_TIMEOUT_SECONDS,
    DEFAULT_RUNTIME_WRITE_TIMEOUT_SECONDS,
    RuntimeOperationPolicy,
    run_read_operation,
    runtime_operation_policy,
)


def test_runtime_operation_policy_falls_back_for_unsafe_internal_values():
    connection = SimpleNamespace(
        config={
            "operation_policy": {
                "read_timeout_seconds": 0,
                "write_timeout_seconds": True,
                "read_retries": True,
            }
        }
    )

    policy = runtime_operation_policy(connection)

    assert policy.read_timeout_seconds == DEFAULT_RUNTIME_READ_TIMEOUT_SECONDS
    assert policy.write_timeout_seconds == DEFAULT_RUNTIME_WRITE_TIMEOUT_SECONDS
    assert policy.read_retries == 1


@pytest.mark.asyncio
async def test_read_operation_retries_only_transient_failures():
    attempts = 0

    async def eventually_available():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("temporary control-plane outage")
        return "ok"

    result = await run_read_operation(
        eventually_available,
        policy=RuntimeOperationPolicy(
            read_timeout_seconds=1, write_timeout_seconds=1, read_retries=2
        ),
        operation_name="test-read",
    )

    assert result == "ok"
    assert attempts == 3


@pytest.mark.asyncio
async def test_read_operation_never_retries_validation_errors():
    attempts = 0

    async def invalid_request():
        nonlocal attempts
        attempts += 1
        raise ValueError("invalid target")

    with pytest.raises(ValueError, match="invalid target"):
        await run_read_operation(
            invalid_request,
            policy=RuntimeOperationPolicy(
                read_timeout_seconds=1, write_timeout_seconds=1, read_retries=3
            ),
            operation_name="test-read",
        )
    assert attempts == 1
