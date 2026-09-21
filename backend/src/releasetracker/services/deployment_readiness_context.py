"""Task-local readiness handoff; never changes legacy direct execution implicitly."""

from contextvars import ContextVar
from collections.abc import Awaitable, Callable

DEFER_READINESS: ContextVar[bool] = ContextVar("defer_readiness", default=False)
READINESS_FINALIZER: ContextVar[Callable[..., Awaitable[object]] | None] = ContextVar(
    "readiness_finalizer", default=None
)
