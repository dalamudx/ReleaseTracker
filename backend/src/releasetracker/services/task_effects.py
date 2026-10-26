"""Prepare deployment authorization, then persist the actual mutation boundary."""

import asyncio
from contextvars import ContextVar
from collections.abc import Awaitable, Callable

MUTATION_GUARD: ContextVar[Callable[[], Awaitable[None]] | None] = ContextVar(
    "task_mutation_guard", default=None
)
PREPARATION_GUARD: ContextVar[Callable[[], Awaitable[None]] | None] = ContextVar(
    "task_preparation_guard", default=None
)
_MUTATION_OWNER = ContextVar("task_mutation_owner", default=None)


async def prepare_deployment_mutation() -> None:
    # SDK threads share this receipt, but authorization runs on the owner's loop.
    _MUTATION_OWNER.set(
        {
            "loop": asyncio.get_running_loop(),
            "task": asyncio.current_task(),
            "lock": asyncio.Lock(),
            "marked": False,
        }
    )
    guard = PREPARATION_GUARD.get()
    if guard is not None:
        await guard()


async def mark_deployment_mutation() -> None:
    guard = MUTATION_GUARD.get()
    if guard is None:
        return
    owner = _MUTATION_OWNER.get()
    if owner is None:
        await guard()
        return

    def check_cancelled():
        if owner["task"].cancelling():
            raise asyncio.CancelledError("Deployment cancelled before mutation")

    async def authorize():
        async with owner["lock"]:
            if owner["marked"]:
                return
            check_cancelled()
            await guard()
            owner["marked"] = True
            check_cancelled()

    async def authorize_on_owner_loop():
        # run_coroutine_threadsafe copies the SDK worker context to the owner loop.
        from ..executors.base import _runtime_adapter_worker_active

        token = _runtime_adapter_worker_active.set(False)
        try:
            await authorize()
        finally:
            _runtime_adapter_worker_active.reset(token)

    if owner["loop"] is not asyncio.get_running_loop():
        # Storage and admission callbacks belong to the scheduler's event loop.
        receipt = asyncio.run_coroutine_threadsafe(authorize_on_owner_loop(), owner["loop"])
        await asyncio.wrap_future(receipt)
    else:
        await authorize()
