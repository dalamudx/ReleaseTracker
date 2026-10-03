"""Cache invalidation releases idle pools instead of losing closing ownership."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executor_scheduler import ExecutorScheduler
from releasetracker.executors.portainer import PortainerRuntimeAdapter
from releasetracker.executors.adapter_lifetime import (
    adapter_borrow_scope,
    runtime_adapter_scope,
    retained_native_read,
)
from releasetracker.services import deployment_readiness_probes as readiness_probes


def runtime(key="synthetic-key"):
    return RuntimeConnectionConfig(
        name="test-portainer",
        type="portainer",
        credential_id=1,
        config={"base_url": "https://portainer.test", "endpoint_id": 1},
        secrets={"api_key": key},
    )


@pytest.mark.parametrize("action", ["refresh", "remove", "credentials_changed"])
async def test_idle_invalidated_real_portainer_pool_is_closed(action):
    scheduler = ExecutorScheduler(SimpleNamespace(get_executor_config=AsyncMock(return_value=None)))
    scheduler.refresh_release_history_cleanup_schedule = AsyncMock()
    adapter = scheduler._get_adapter(1, runtime())
    assert isinstance(adapter, PortainerRuntimeAdapter)
    client = adapter._get_client()
    try:
        if action == "refresh":
            await scheduler.refresh_executor(1)
        elif action == "remove":
            await scheduler.remove_executor(1)
        else:
            replacement = scheduler._get_adapter(1, runtime("rotated-synthetic-key"))
            assert replacement is not adapter
        # The close task for an idle client must be scheduled at invalidation,
        # not left to garbage collection or an unrelated future shutdown.
        await asyncio.sleep(0)
        assert client.is_closed, "invalidated cache lost ownership of the HTTP pool"
    finally:
        await client.aclose()
        await scheduler.shutdown()


def scheduler_fixture():
    scheduler = ExecutorScheduler(SimpleNamespace(get_executor_config=AsyncMock(return_value=None)))
    scheduler.refresh_release_history_cleanup_schedule = AsyncMock()
    return scheduler


@pytest.mark.parametrize("action", ["refresh", "remove", "credentials_changed"])
async def test_retirement_keeps_whole_multi_step_borrow_alive(action):
    scheduler = scheduler_fixture()
    try:
        with adapter_borrow_scope():
            adapter = scheduler._get_adapter(1, runtime())
            lifetime = adapter._runtime_adapter_lifetime
            client = adapter._get_client()
            with adapter_borrow_scope():
                assert scheduler._get_adapter(1, runtime()) is adapter
            assert lifetime.borrowers == 1, "nested calls share the full consumer lease"
            if action == "refresh":
                await scheduler.refresh_executor(1)
            elif action == "remove":
                await scheduler.remove_executor(1)
            else:
                assert scheduler._get_adapter(1, runtime("rotated-key")) is not adapter
            await asyncio.sleep(0)
            assert lifetime.retired
            assert not client.is_closed
            assert adapter._get_client() is client, "later steps must keep their original session"
        await asyncio.wait_for(lifetime.wait_closed(), timeout=1)
        assert client.is_closed
        assert lifetime.key not in scheduler._adapter_lifetimes
    finally:
        await scheduler.shutdown()


async def test_child_task_inherited_context_has_its_own_lease():
    scheduler = scheduler_fixture()
    entered, release = asyncio.Event(), asyncio.Event()

    @runtime_adapter_scope
    async def child():
        adapter = scheduler._get_adapter(1, runtime())
        entered.set()
        await release.wait()
        assert not adapter._get_client().is_closed

    task = None
    try:
        with adapter_borrow_scope():
            adapter = scheduler._get_adapter(1, runtime())
            client = adapter._get_client()
            lifetime = adapter._runtime_adapter_lifetime
            task = asyncio.create_task(child())
            await asyncio.wait_for(entered.wait(), timeout=1)
            assert lifetime.borrowers == 2
            await scheduler.remove_executor(1)
        assert lifetime.borrowers == 1
        assert not client.is_closed
        release.set()
        await asyncio.wait_for(task, timeout=1)
        await asyncio.wait_for(lifetime.wait_closed(), timeout=1)
        assert client.is_closed
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await scheduler.shutdown()


@pytest.mark.parametrize("outcome", ["error", "cancel"])
async def test_exception_or_cancellation_releases_last_borrow(outcome):
    scheduler = scheduler_fixture()
    entered = asyncio.Event()
    state = {}

    @runtime_adapter_scope
    async def operation():
        adapter = scheduler._get_adapter(1, runtime())
        state.update(
            adapter=adapter,
            client=adapter._get_client(),
            lifetime=adapter._runtime_adapter_lifetime,
        )
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(operation())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        await scheduler.refresh_executor(1)
        assert not state["client"].is_closed
        if outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            # An async consumer failing after invalidation must release its scope too.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            with pytest.raises(RuntimeError, match="synthetic consumer failure"):
                with adapter_borrow_scope():
                    other = scheduler._get_adapter(2, runtime())
                    other_lifetime = other._runtime_adapter_lifetime
                    other_client = other._get_client()
                    await scheduler.remove_executor(2)
                    raise RuntimeError("synthetic consumer failure")
            await asyncio.wait_for(other_lifetime.wait_closed(), timeout=1)
            assert other_client.is_closed
        await asyncio.wait_for(state["lifetime"].wait_closed(), timeout=1)
        assert state["client"].is_closed
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await scheduler.shutdown()


async def test_shared_cache_alias_remains_owned_until_last_entry_invalidated():
    scheduler = scheduler_fixture()
    adapter = scheduler._get_adapter(1, runtime())
    client = adapter._get_client()
    lifetime = adapter._runtime_adapter_lifetime
    scheduler._adapters[2] = adapter
    try:
        await scheduler.remove_executor(1)
        assert not lifetime.retired
        assert not client.is_closed
        await scheduler.remove_executor(2)
        await asyncio.wait_for(lifetime.wait_closed(), timeout=1)
        assert client.is_closed
    finally:
        await scheduler.shutdown()


async def test_failed_replacement_constructor_keeps_original_client(monkeypatch):
    import releasetracker.executor_scheduler as module

    scheduler = scheduler_fixture()
    adapter = scheduler._get_adapter(1, runtime())
    client = adapter._get_client()

    def fail(config):
        raise RuntimeError("synthetic constructor failure")

    monkeypatch.setattr(module, "PortainerRuntimeAdapter", fail)
    try:
        with pytest.raises(RuntimeError, match="synthetic constructor failure"):
            scheduler._get_adapter(1, runtime("changed-key"))
        assert scheduler._adapters[1] is adapter
        assert not adapter._runtime_adapter_lifetime.retired
        assert not client.is_closed
    finally:
        await scheduler.shutdown()


async def test_retired_close_failure_is_logged_and_reported_at_shutdown(caplog):
    scheduler = scheduler_fixture()
    adapter = scheduler._get_adapter(1, runtime())
    client = adapter._get_client()
    close = adapter.close
    failure = RuntimeError("synthetic secret should not be logged")
    adapter.close = AsyncMock(side_effect=failure)
    lifetime = adapter._runtime_adapter_lifetime
    try:
        await scheduler.remove_executor(1)
        await asyncio.wait_for(lifetime.wait_closed(), timeout=1)
        assert not scheduler._adapter_lifetimes
        assert str(failure) not in caplog.text
        assert "runtime_adapter_close_failed" in caplog.text
        with pytest.raises(RuntimeError) as caught:
            await scheduler.shutdown()
        assert caught.value is failure
        await scheduler.shutdown()
        adapter.close.assert_awaited_once()
    finally:
        await close()
        assert client.is_closed


async def test_shutdown_forbids_new_clients():
    scheduler = scheduler_fixture()
    await scheduler.shutdown()
    with pytest.raises(RuntimeError, match="has shut down"):
        scheduler._get_adapter(1, runtime())
    assert not scheduler._adapters
    assert not scheduler._adapter_lifetimes


async def test_native_read_timeout_retains_old_client_until_real_thread_finishes(monkeypatch):
    scheduler = scheduler_fixture()
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    original_wait_for = asyncio.wait_for

    async def synthetic_timeout(awaitable, timeout):
        if timeout == 20:
            await started.wait()
            raise TimeoutError("synthetic observation budget reached")
        return await original_wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(readiness_probes.asyncio, "wait_for", synthetic_timeout)
    shutdown = None
    adapter = None
    try:
        with adapter_borrow_scope():
            adapter = scheduler._get_adapter(1, runtime())
            client = adapter._get_client()
            lifetime = adapter._runtime_adapter_lifetime

            def native_read():
                assert not client.is_closed
                loop.call_soon_threadsafe(started.set)
                assert release.wait(timeout=3), "test must release its native read"
                assert not client.is_closed
                return "read complete"

            with pytest.raises(TimeoutError, match="synthetic observation budget"):
                await readiness_probes._bounded_thread(adapter, "owned-native-test", native_read)
            await scheduler.remove_executor(1)
        assert lifetime.borrowers > 0
        assert not client.is_closed
        with pytest.raises(TimeoutError, match="previous native read"):
            await readiness_probes._bounded_thread(adapter, "owned-native-test", native_read)
        shutdown_started = asyncio.Event()

        async def stop():
            shutdown_started.set()
            await scheduler.shutdown()

        shutdown = asyncio.create_task(stop())
        await original_wait_for(shutdown_started.wait(), timeout=1)
        assert not shutdown.done()
        assert not client.is_closed
        release.set()
        await original_wait_for(shutdown, timeout=3)
        assert client.is_closed
        assert not scheduler._adapter_lifetimes
        assert (id(adapter), "owned-native-test") not in readiness_probes._READS
    finally:
        release.set()
        if shutdown is not None:
            await asyncio.gather(shutdown, return_exceptions=True)
        await scheduler.shutdown()


async def test_repeated_sdk_cancellation_waits_for_mutation_before_releasing_lease():
    from releasetracker.executors.base import offload_blocking_runtime_adapter_methods

    scheduler = scheduler_fixture()
    entered, finished = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    @offload_blocking_runtime_adapter_methods
    class BlockingSDK(SimpleNamespace):
        async def update_image(self, *args):
            loop.call_soon_threadsafe(entered.set)
            try:
                assert release.wait(timeout=3)
                self.close.assert_not_awaited()
            finally:
                loop.call_soon_threadsafe(finished.set)

    adapter = BlockingSDK(close=AsyncMock())
    scheduler._adapters[1] = adapter

    @runtime_adapter_scope
    async def deploy():
        owned = scheduler._get_adapter(1, runtime())
        await owned.update_image({}, "synthetic:2")

    task = asyncio.create_task(deploy())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        lifetime = adapter._runtime_adapter_lifetime
        await scheduler.remove_executor(1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "a second cancellation must not release a running SDK operation"
        assert lifetime.borrowers == 1
        adapter.close.assert_not_awaited()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
        assert finished.is_set()
        await asyncio.wait_for(lifetime.wait_closed(), timeout=1)
        adapter.close.assert_awaited_once()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.wait_for(finished.wait(), timeout=3)
        await scheduler.shutdown()


async def test_repeated_native_read_cancellation_does_not_close_working_client():
    scheduler = scheduler_fixture()
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    adapter = scheduler._get_adapter(1, runtime())
    client = adapter._get_client()
    lifetime = adapter._runtime_adapter_lifetime

    def native_read():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=3)
        assert not client.is_closed
        return True

    task = asyncio.create_task(retained_native_read(adapter, native_read))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        await scheduler.remove_executor(1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert not client.is_closed
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
        await asyncio.wait_for(lifetime.wait_closed(), timeout=1)
        assert client.is_closed
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await scheduler.shutdown()
