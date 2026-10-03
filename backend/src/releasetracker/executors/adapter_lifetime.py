"""Loop-owned runtime leases; cache retirement never closes an active borrower.

Scopes cover whole multi-step consumer operations, not individual HTTP requests.
Native reads retain a separate lease until their worker actually finishes.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Callable


class RuntimeAdapterLifetime:
    def __init__(self, adapter, on_closed: Callable[[RuntimeAdapterLifetime], None]):
        self.key = id(adapter)
        self.adapter = adapter
        self.on_closed = on_closed
        self.borrowers = 0
        self.retired = False
        self.error: BaseException | None = None
        self._close_task = None
        self._closed = asyncio.Event()

    def acquire(self):
        if self._close_task is not None or self._closed.is_set():
            raise RuntimeError("runtime adapter has been retired")
        self.borrowers += 1

    def release(self):
        if self.borrowers <= 0:
            raise RuntimeError("runtime adapter lease underflow")
        self.borrowers -= 1
        self._close_if_idle()

    def retire(self):
        self.retired = True
        self._close_if_idle()

    def _close_if_idle(self):
        if not self.retired or self.borrowers or self._close_task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # The synchronous getter can be used before an event loop starts.
            # Shutdown/wait_closed will schedule deferred retirement on its loop.
            return
        self._close_task = loop.create_task(self._close())

    async def _close(self):
        try:
            close = getattr(self.adapter, "close", None)
            if close is not None:
                await close()
        except (Exception, asyncio.CancelledError) as exc:
            self.error = exc.with_traceback(None)
        finally:
            self.adapter = None
            self._closed.set()
            self.on_closed(self)

    async def wait_closed(self):
        self._close_if_idle()
        await self._closed.wait()


class _BorrowScope:
    def __init__(self):
        self.task = asyncio.current_task()
        self.leases: dict[int, RuntimeAdapterLifetime] = {}
        self.closed = False

    def borrow(self, lifetime):
        if lifetime.key not in self.leases:
            lifetime.acquire()
            self.leases[lifetime.key] = lifetime

    def release(self):
        self.closed = True
        for lifetime in reversed(list(self.leases.values())):
            lifetime.release()
        self.leases.clear()


_SCOPE: ContextVar[_BorrowScope | None] = ContextVar("runtime_adapter_borrow_scope", default=None)


@contextmanager
def adapter_borrow_scope():
    current = _SCOPE.get()
    # Tasks inherit ContextVars, but must never share another task's lease stack.
    if current is not None and not current.closed and current.task is asyncio.current_task():
        yield
        return
    scope = _BorrowScope()
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        _SCOPE.reset(token)
        scope.release()


def runtime_adapter_scope(operation):
    @wraps(operation)
    async def scoped(*args, **kwargs):
        with adapter_borrow_scope():
            return await operation(*args, **kwargs)

    return scoped


def borrow_runtime_adapter(lifetime):
    scope = _SCOPE.get()
    if scope is not None and not scope.closed and scope.task is asyncio.current_task():
        scope.borrow(lifetime)


@contextmanager
def retain_runtime_adapter(adapter):
    lifetime = getattr(adapter, "_runtime_adapter_lifetime", None)
    if lifetime is None:
        # Standalone/injected adapters retain their caller's existing ownership.
        yield
        return
    lifetime.acquire()
    try:
        yield
    finally:
        lifetime.release()


async def wait_for_runtime_worker(worker):
    """Even repeated cancellation cannot abandon an already running SDK worker."""
    cancelled = None
    while True:
        try:
            result = await asyncio.shield(worker)
            break
        except asyncio.CancelledError as exc:
            if worker.cancelled():
                raise
            cancelled = exc
        except Exception:
            if cancelled is not None:
                raise cancelled
            raise
    if cancelled is not None:
        raise cancelled
    return result


async def retained_native_read(adapter, callback, *args):
    """Cancellation cannot release a lease while its blocking SDK read still runs."""
    with retain_runtime_adapter(adapter):
        worker = asyncio.create_task(asyncio.to_thread(callback, *args))
        return await wait_for_runtime_worker(worker)
