"""Shared scheduler host abstraction."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import SchedulerNotRunningError


class SchedulerHost:
    """Owns a shared AsyncIOScheduler and namespaced job operations."""

    def __init__(self, scheduler: AsyncIOScheduler | None = None):
        self._scheduler = scheduler or AsyncIOScheduler()

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
    ) -> str:
        job_id = self.namespaced_job_id(namespace, key)
        self._scheduler.add_job(
            func,
            "interval",
            seconds=seconds,
            args=list(args or []),
            id=job_id,
            replace_existing=True,
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
            func,
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
            func,
            "cron",
            hour=hour,
            minute=minute,
            timezone=timezone,
            args=list(args or []),
            id=job_id,
            replace_existing=True,
        )
        return job_id

    async def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    async def shutdown(self) -> None:
        try:
            self._scheduler.shutdown(wait=False)
        except SchedulerNotRunningError:
            pass
