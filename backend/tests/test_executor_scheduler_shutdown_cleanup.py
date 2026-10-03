"""Scheduler shutdown drains borrowers and closes every owned cached adapter."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.executor_scheduler import ExecutorScheduler


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("synthetic close failure"), asyncio.CancelledError("synthetic close cancelled")],
)
async def test_shutdown_closes_remaining_adapters_and_clears_cache_after_failure(failure, caplog):
    scheduler = ExecutorScheduler(SimpleNamespace())
    first = SimpleNamespace(close=AsyncMock(side_effect=failure))
    second = SimpleNamespace(close=AsyncMock())
    scheduler._adapters = {1: first, 2: second}
    try:
        with pytest.raises(type(failure)) as caught:
            await scheduler.shutdown()
        assert caught.value is failure, "shutdown must preserve the failure after finishing cleanup"
        first.close.assert_awaited_once()
        second.close.assert_awaited_once()
        assert scheduler._adapters == {}
        assert "runtime_adapter_close_failed" in caplog.text
        # Remote error text may contain credentials; log type information only.
        assert str(failure) not in caplog.text
    finally:
        scheduler._adapters.clear()


async def test_shutdown_closes_shared_adapter_once_and_is_idempotent():
    scheduler = ExecutorScheduler(SimpleNamespace())
    shared = SimpleNamespace(close=AsyncMock())
    scheduler._adapters = {1: shared, 2: shared}
    await scheduler.shutdown()
    await scheduler.shutdown()
    shared.close.assert_awaited_once()
    assert scheduler._adapters == {}


async def test_shutdown_accepts_test_or_legacy_adapter_without_close():
    scheduler = ExecutorScheduler(SimpleNamespace())
    scheduler._adapters = {1: SimpleNamespace()}
    await scheduler.shutdown()
    assert scheduler._adapters == {}


async def test_shutdown_waits_for_background_finally_before_closing_connections():
    scheduler = ExecutorScheduler(SimpleNamespace())
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()
    drained = asyncio.Event()

    async def operation():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await allow_cleanup.wait()
            drained.set()

    async def close():
        assert drained.is_set(), "cannot close a runtime still owned by an in-flight operation"

    adapter = SimpleNamespace(close=AsyncMock(side_effect=close))
    scheduler._adapters[1] = adapter
    worker = scheduler._track_background_task(operation())
    await asyncio.wait_for(started.wait(), timeout=1)
    shutdown = asyncio.create_task(scheduler.shutdown())
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        adapter.close.assert_not_awaited()
        assert not shutdown.done()
        allow_cleanup.set()
        await asyncio.wait_for(shutdown, timeout=1)
        assert worker.cancelled()
        assert drained.is_set()
        adapter.close.assert_awaited_once()
        assert scheduler._background_tasks == set()
        assert scheduler._adapters == {}
    finally:
        allow_cleanup.set()
        await asyncio.gather(worker, shutdown, return_exceptions=True)


async def test_cancellation_takes_priority_after_other_close_failure():
    scheduler = ExecutorScheduler(SimpleNamespace())
    cancelled = asyncio.CancelledError("synthetic cancellation")
    first = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("synthetic error")))
    second = SimpleNamespace(close=AsyncMock(side_effect=cancelled))
    third = SimpleNamespace(close=AsyncMock())
    scheduler._adapters = {1: first, 2: second, 3: third}
    with pytest.raises(asyncio.CancelledError) as caught:
        await scheduler.shutdown()
    assert caught.value is cancelled
    for adapter in (first, second, third):
        adapter.close.assert_awaited_once()
    assert scheduler._adapters == {}


async def test_close_failure_does_not_leave_real_portainer_http_pool_open():
    from releasetracker.config import RuntimeConnectionConfig
    from releasetracker.executors.portainer import PortainerRuntimeAdapter

    scheduler = ExecutorScheduler(SimpleNamespace())
    adapter = PortainerRuntimeAdapter(
        RuntimeConnectionConfig(
            name="owned-test",
            type="portainer",
            credential_id=1,
            config={"base_url": "https://portainer.test", "endpoint_id": 1},
            secrets={"api_key": "synthetic-key"},
        )
    )
    client = adapter._get_client()
    scheduler._adapters = {
        1: SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("synthetic error"))),
        2: adapter,
    }
    try:
        with pytest.raises(RuntimeError, match="synthetic error"):
            await scheduler.shutdown()
        assert client.is_closed
        assert adapter._client is None
        assert scheduler._adapters == {}
    finally:
        await client.aclose()
