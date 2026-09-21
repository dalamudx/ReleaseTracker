"""Persist the mutation boundary before issuing any queued deployment writes."""

from contextvars import ContextVar
from collections.abc import Awaitable, Callable

MUTATION_GUARD: ContextVar[Callable[[], Awaitable[None]] | None] = ContextVar(
    "task_mutation_guard", default=None
)


async def mark_deployment_mutation() -> None:
    guard = MUTATION_GUARD.get()
    if guard is not None:
        await guard()
