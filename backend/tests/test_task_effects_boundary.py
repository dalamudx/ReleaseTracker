"""Prepared write guards run once on their owner loop, including SDK callbacks."""

import asyncio
import threading

from releasetracker.executors.base import offload_blocking_runtime_adapter_methods
from releasetracker.services import task_effects


async def test_multistep_guard_is_once_on_owner_loop_and_nested_reads_stay_offloaded():
    loop = asyncio.get_running_loop()
    main_thread = threading.get_ident()
    events = []

    @offload_blocking_runtime_adapter_methods
    class Adapter:
        async def inspect(self):
            assert threading.get_ident() != main_thread
            events.append("inspect")

        async def update(self):
            assert threading.get_ident() != main_thread
            await task_effects.mark_deployment_mutation()
            events.append("first_write")
            await task_effects.mark_deployment_mutation()
            events.append("second_write")

    adapter = Adapter()

    async def guard():
        assert asyncio.get_running_loop() is loop
        assert threading.get_ident() == main_thread
        await adapter.inspect()
        events.append("marked")

    async def prepare():
        events.append("prepare")

    owner_token = task_effects._MUTATION_OWNER.set(None)
    prep_token = task_effects.PREPARATION_GUARD.set(prepare)
    marker_token = task_effects.MUTATION_GUARD.set(guard)
    try:
        await task_effects.prepare_deployment_mutation()
        assert events == ["prepare"]
        await adapter.update()
        assert events == ["prepare", "inspect", "marked", "first_write", "second_write"]
    finally:
        task_effects.MUTATION_GUARD.reset(marker_token)
        task_effects.PREPARATION_GUARD.reset(prep_token)
        task_effects._MUTATION_OWNER.reset(owner_token)


async def test_concurrent_boundary_requests_share_one_guard_receipt():
    calls = []

    async def guard():
        await asyncio.sleep(0)
        calls.append("marked")

    owner_token = task_effects._MUTATION_OWNER.set(None)
    marker_token = task_effects.MUTATION_GUARD.set(guard)
    try:
        await task_effects.prepare_deployment_mutation()
        await asyncio.gather(*(task_effects.mark_deployment_mutation() for _ in range(3)))
        assert calls == ["marked"]
    finally:
        task_effects.MUTATION_GUARD.reset(marker_token)
        task_effects._MUTATION_OWNER.reset(owner_token)
