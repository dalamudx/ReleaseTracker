"""Bounded durable dispatch; no long-running operation runs in an APScheduler tick."""

from __future__ import annotations

import asyncio
import logging
import random
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from .outbound_http import (
    OutboundConnectError,
    OutboundDNSFailure,
    OutboundTimeout,
    OutboundTLSFailure,
    OutboundURLRejected,
    OutboundRedirectRejected,
)

logger = logging.getLogger(__name__)


@dataclass
class TaskResult:
    state: str = "succeeded"
    code: str | None = None
    message: str | None = None
    result: dict = field(default_factory=dict)
    retryable: bool = False
    retry_after: float = 0


@dataclass
class Deferred:
    until: float
    code: str


def classify_fetch_error(error: BaseException) -> TaskResult:
    """Inspect preserved exception chains, never parse localized error messages.

    Diagnostics intentionally omit exception text: URLs can contain credentials.
    """
    chain = []
    while error is not None and all(error is not item for item in chain):
        chain.append(error)
        error = error.__cause__ or error.__context__
    if any(
        isinstance(
            item, (ssl.SSLError, OutboundTLSFailure, OutboundURLRejected, OutboundRedirectRejected)
        )
        for item in chain
    ):
        return TaskResult("failed", "security_validation_failed")
    for item in chain:
        if isinstance(item, httpx.HTTPStatusError):
            status = item.response.status_code
            retryable = status in {408, 425, 429, 500, 502, 503, 504}
            retry_after = 0
            if retryable:
                value = item.response.headers.get("Retry-After", "")
                try:
                    retry_after = max(0, float(value))
                except ValueError:
                    try:
                        retry_after = max(
                            0,
                            (
                                parsedate_to_datetime(value) - datetime.now(timezone.utc)
                            ).total_seconds(),
                        )
                    except (TypeError, ValueError, OverflowError):
                        pass
            return TaskResult(
                "failed", f"upstream_http_{status}", retryable=retryable, retry_after=retry_after
            )
    if any(
        isinstance(item, (TimeoutError, httpx.TimeoutException, OutboundTimeout)) for item in chain
    ):
        return TaskResult("failed", "upstream_timeout", retryable=True)
    if any(
        isinstance(
            item, (ConnectionError, httpx.NetworkError, OutboundConnectError, OutboundDNSFailure)
        )
        for item in chain
    ):
        return TaskResult("failed", "upstream_connection_failed", retryable=True)
    return TaskResult("failed", "fetch_failed")


def retry_delay(attempt: int, retry_after: float = 0) -> float:
    base = (30, 120, 600)[min(max(attempt - 1, 0), 2)]
    return max(base * random.uniform(1, 1.1), retry_after)


class TaskQueue:
    def __init__(self, store, scheduler_host):
        self.store = store
        self.scheduler_host = scheduler_host
        self.handlers = {}
        self.capacity = {"fetch": 3, "deploy": 1, "recover": 1}
        self.workers = {}
        self.stopping = False
        self.dispatch_lock = asyncio.Lock()

    def register(self, kind, handler):
        self.handlers[kind] = handler

    async def initialize(self):
        await self.store.recover(startup=True)
        self.scheduler_host.add_interval_job("tasks", "dispatch", self.tick, seconds=2)

    async def tick(self):
        if self.stopping or self.dispatch_lock.locked():
            return
        async with self.dispatch_lock:
            await self.store.recover()
            for kind, capacity in self.capacity.items():
                if kind not in self.handlers:
                    continue
                running = sum(1 for work_kind, _ in self.workers.values() if work_kind == kind)
                for _ in range(capacity - running):
                    task = await self.store.claim(kind)
                    if not task:
                        break
                    worker = asyncio.create_task(self._run(task), name=f"task-{task['id']}")
                    self.workers[task["id"]] = (kind, worker)
                    worker.add_done_callback(
                        lambda done, task_id=task["id"]: self._done(task_id, done)
                    )

    def _done(self, task_id, worker):
        self.workers.pop(task_id, None)
        if not worker.cancelled() and worker.exception():
            logger.error(
                "Task worker %s terminated: %s", task_id, type(worker.exception()).__name__
            )

    async def _heartbeat(self, task, worker):
        try:
            while True:
                await asyncio.sleep(15)
                if not await self.store.heartbeat(task):
                    worker.cancel()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # A worker which cannot maintain its lease must stop making progress.
            worker.cancel()

    async def _run(self, task):
        heartbeat = asyncio.create_task(self._heartbeat(task, asyncio.current_task()))
        try:
            handler = self.handlers[task["kind"]]
            prepared = (
                TaskResult("failed", "retry_budget_exhausted")
                if task["attempts"] > task["max_retries"]
                else await handler.prepare(task)
            )
            if isinstance(prepared, Deferred):
                await self.store.finish(task, "queued", code=prepared.code, due_at=prepared.until)
                return
            if isinstance(prepared, TaskResult):
                outcome = prepared
            else:
                if not await self.store.start_attempt(task):
                    await self._finish(task, TaskResult("failed", "retry_budget_exhausted"))
                    return
                outcome = await handler.execute(task)
            await self._finish(task, outcome)
        except asyncio.CancelledError:
            # Keep the lease/attempt durable. Startup or expiration performs recovery;
            # deployments are blocked for verification, never replayed blindly.
            raise
        except Exception as exc:
            outcome = (
                classify_fetch_error(exc)
                if task["kind"] == "fetch"
                else TaskResult("needs_attention", "deployment_interrupted")
            )
            await self._finish(task, outcome)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _finish(self, task, outcome):
        if outcome.state == "observing":
            # Handoff transaction transferred ownership to the durable observer.
            return
        state = outcome.state
        due_at = None
        if (
            outcome.retryable
            and task["kind"] == "fetch"
            and 0 < task["attempts"] <= task["max_retries"]
        ):
            state = "retry_wait"
            due_at = time.time() + retry_delay(task["attempts"], outcome.retry_after)
        current = await self.store.get(task["id"])
        outcome.result = (
            (current.get("result") or {}) | outcome.result if current else outcome.result
        )
        if (
            task["kind"] == "deploy"
            and state == "needs_attention"
            and outcome.result.get("mutation_started") is False
        ):
            state = "failed"
        finished = await self.store.finish(
            task,
            state,
            code=outcome.code,
            message=outcome.message,
            result=outcome.result,
            due_at=due_at,
        )
        handler = self.handlers.get(task["kind"])
        if finished and handler is not None and hasattr(handler, "finished"):
            await handler.finished(await self.store.get(task["id"]))

    async def shutdown(self):
        self.stopping = True
        self.scheduler_host.remove_job("tasks", "dispatch")
        await asyncio.gather(
            *(worker for _, worker in list(self.workers.values())), return_exceptions=True
        )
