"""Caller cancellation must not interrupt draining or client cleanup."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.executor_scheduler import ExecutorScheduler


@pytest.mark.parametrize("phase", ["background_drain", "client_close"])
async def test_repeated_shutdown_cancellation_finishes_cleanup_before_propagating(phase):
    scheduler = ExecutorScheduler(SimpleNamespace())
    entered, drained, close_started, cleanup_started = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    allow_drain, allow_close = asyncio.Event(), asyncio.Event()

    async def worker():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await allow_drain.wait()
            drained.set()

    async def close():
        assert drained.is_set()
        close_started.set()
        await allow_close.wait()

    adapter = SimpleNamespace(close=AsyncMock(side_effect=close))
    scheduler._adapters[1] = adapter
    task = scheduler._track_background_task(worker())
    await asyncio.wait_for(entered.wait(), timeout=1)
    shutdown = asyncio.create_task(scheduler.shutdown())
    try:
        if phase == "client_close":
            allow_drain.set()
            await asyncio.wait_for(close_started.wait(), timeout=1)
        else:
            await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        shutdown.cancel()
        await asyncio.sleep(0)
        shutdown.cancel()
        await asyncio.sleep(0)
        assert not shutdown.done(), "external cancellation must wait for resource cleanup"
        allow_drain.set()
        allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(shutdown, timeout=1)
        assert drained.is_set()
        adapter.close.assert_awaited_once()
        assert not scheduler._adapters
        assert not scheduler._adapter_lifetimes
        assert not scheduler._background_tasks
    finally:
        allow_drain.set()
        allow_close.set()
        await asyncio.gather(task, shutdown, return_exceptions=True)
        await scheduler.shutdown()


async def test_concurrent_shutdown_waiters_share_one_drain_and_failure():
    scheduler = ExecutorScheduler(SimpleNamespace())
    started, release = asyncio.Event(), asyncio.Event()
    failure = RuntimeError("synthetic shared close failure")

    async def close():
        started.set()
        await release.wait()
        raise failure

    adapter = SimpleNamespace(close=AsyncMock(side_effect=close))
    scheduler._adapters[1] = adapter
    first = asyncio.create_task(scheduler.shutdown())
    second = asyncio.create_task(scheduler.shutdown())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        adapter.close.assert_awaited_once()
        release.set()
        outcomes = await asyncio.gather(first, second, return_exceptions=True)
        assert outcomes == [failure, failure]
        assert not scheduler._adapter_lifetimes
        await scheduler.shutdown()
        adapter.close.assert_awaited_once()
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
