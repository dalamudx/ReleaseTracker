"""Shared scheduler host abstraction."""

from __future__ import annotations

from collections.abc import Sequence
import os
import asyncio
import inspect
from functools import wraps
from typing import Any, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import SchedulerNotRunningError


class SchedulerHost:
    """Owns a shared AsyncIOScheduler and namespaced job operations."""

    def __init__(self, scheduler: AsyncIOScheduler | None = None):
        self._scheduler = scheduler or AsyncIOScheduler()
        self._accepting = True
        self._active: set[asyncio.Task] = set()
        try:
            self.worker_poll_seconds = int(
                os.environ.get("RELEASETRACKER_WORKER_POLL_SECONDS", "2")
            )
        except ValueError as exc:
            raise ValueError(
                "RELEASETRACKER_WORKER_POLL_SECONDS must be an integer from 1 to 60"
            ) from exc
        if not 1 <= self.worker_poll_seconds <= 60:
            raise ValueError("RELEASETRACKER_WORKER_POLL_SECONDS must be an integer from 1 to 60")

    @property
    def scheduler(self) -> AsyncIOScheduler:
        return self._scheduler

    @staticmethod
    def namespaced_job_id(namespace: str, key: str | int) -> str:
        normalized_namespace = namespace.strip()
        normalized_key = str(key).strip()
        return f"{normalized_namespace}_{normalized_key}"

    def get_job(self, namespace: str, key: str | int):
        return self._scheduler.get_job(self.namespaced_job_id(namespace, key))

    def remove_job(self, namespace: str, key: str | int) -> None:
        job_id = self.namespaced_job_id(namespace, key)
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)

    def remove_jobs_by_namespace(self, namespace: str) -> None:
        prefix = f"{namespace.strip()}_"
        for job in self._scheduler.get_jobs():
            if job.id.startswith(prefix):
                self._scheduler.remove_job(job.id)

    def add_interval_job(
        self,
        namespace: str,
        key: str | int,
        func: Callable[..., Any],
        *,
        seconds: int,
        args: Sequence[Any] | None = None,
        next_run_time=None,
        jitter: int | None = None,
    ) -> str:
        """``next_run_time`` lets callers resume from persisted history instead of
        restarting a full interval every time the process starts."""
        job_id = self.namespaced_job_id(namespace, key)
        if seconds == 2 and namespace.strip() in {
            "tasks",
            "readiness",
            "executor_notifications",
            "deployment_admission_notifications",
            "repository_webhooks",
        }:
            seconds = self.worker_poll_seconds
        self._scheduler.add_job(
            self._tracked(func),
            "interval",
            seconds=seconds,
            args=list(args or []),
            id=job_id,
            replace_existing=True,
            **({"next_run_time": next_run_time} if next_run_time is not None else {}),
            **({"jitter": jitter} if jitter else {}),
        )
        return job_id

    def add_date_job(
        self,
        namespace: str,
        key: str | int,
        func: Callable[..., Any],
        *,
        run_date,
        args: Sequence[Any] | None = None,
    ) -> str:
        job_id = self.namespaced_job_id(namespace, key)
        self._scheduler.add_job(
            self._tracked(func),
            "date",
            run_date=run_date,
            args=list(args or []),
            id=job_id,
            replace_existing=True,
        )
        return job_id

    def add_cron_job(
        self,
        namespace: str,
        key: str | int,
        func: Callable[..., Any],
        *,
        hour: int,
        minute: int,
        timezone=None,
        args: Sequence[Any] | None = None,
    ) -> str:
        job_id = self.namespaced_job_id(namespace, key)
        self._scheduler.add_job(
            self._tracked(func),
            "cron",
            hour=hour,
            minute=minute,
            timezone=timezone,
            args=list(args or []),
            id=job_id,
            replace_existing=True,
        )
        return job_id

    def _tracked(self, function):
        @wraps(function)
        async def run(*args, **kwargs):
            if not self._accepting:
                return
            task = asyncio.current_task()
            self._active.add(task)
            try:
                if inspect.iscoroutinefunction(function):
                    return await function(*args, **kwargs)
                from .executors.adapter_lifetime import wait_for_runtime_worker

                return await wait_for_runtime_worker(
                    asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
                )
            finally:
                self._active.discard(task)

        return run

    def pause(self):
        self._accepting = False
        if self._scheduler.running:
            self._scheduler.pause()

    def resume(self):
        self._accepting = True
        if self._scheduler.running:
            self._scheduler.resume()

    async def drain(self, timeout=30):
        pending = {task for task in self._active if not task.done()}
        if pending:
            _, unfinished = await asyncio.wait(pending, timeout=timeout)
            if unfinished:
                raise TimeoutError("Background jobs are still active")

    async def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start(paused=not self._accepting)

    async def shutdown(self) -> None:
        try:
            self._scheduler.shutdown(wait=False)
        except SchedulerNotRunningError:
            pass
