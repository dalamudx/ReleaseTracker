"""Startup cleanup works with real component instances, not only lifespan doubles."""

import asyncio
from types import SimpleNamespace

import aiosqlite
import pytest

from releasetracker.executor_scheduler import ExecutorScheduler
from releasetracker.scheduler_host import SchedulerHost
from releasetracker.webhook_scheduler import RepositoryWebhookScheduler
from releasetracker.services.deployment_readiness import DeploymentReadiness
from releasetracker.services.runtime_health_watch import RuntimeHealthWatch
from releasetracker.services.task_queue import TaskQueue
from releasetracker.services.executor_notification_outbox import ExecutorNotificationOutbox
from releasetracker.services.release_notification_outbox import ReleaseNotificationOutbox
from releasetracker.services.deployment_admission_notifications import (
    DeploymentAdmissionNotificationOutbox,
)
from releasetracker.services.shutdown import shutdown_services


@pytest.mark.parametrize(
    "kind",
    [
        "executor",
        "webhook",
        "readiness",
        "runtime_health",
        "queue",
        "executor_outbox",
        "release_outbox",
        "admission_outbox",
    ],
)
async def test_constructed_component_can_close_before_initialize(kind, monkeypatch):
    monkeypatch.setenv("RELEASETRACKER_RUNTIME_HEALTH_INTERVAL_SECONDS", "0")
    host, storage = SchedulerHost(), SimpleNamespace()
    scheduler = ExecutorScheduler(storage, scheduler_host=host)
    constructors = {
        "executor": lambda: scheduler,
        "webhook": lambda: RepositoryWebhookScheduler(storage, scheduler, host),
        "readiness": lambda: DeploymentReadiness(storage, scheduler, host),
        "runtime_health": lambda: RuntimeHealthWatch(storage, scheduler, SimpleNamespace(), host),
        "queue": lambda: TaskQueue(SimpleNamespace(), host),
        "executor_outbox": lambda: ExecutorNotificationOutbox(storage, host),
        "release_outbox": lambda: ReleaseNotificationOutbox(storage, host),
        "admission_outbox": lambda: DeploymentAdmissionNotificationOutbox(storage, host),
    }
    service = constructors[kind]()
    await shutdown_services([(kind, service.shutdown), ("host", host.shutdown)])
    await service.shutdown()
    assert not host.scheduler.running
    assert not host.scheduler.get_jobs()


@pytest.mark.parametrize(
    "service_type",
    [
        ExecutorNotificationOutbox,
        ReleaseNotificationOutbox,
        DeploymentAdmissionNotificationOutbox,
    ],
)
@pytest.mark.parametrize("cancelled", [False, True])
async def test_outbox_partial_initialize_closes_real_independent_sqlite_pool(
    service_type, cancelled, tmp_path
):
    failure = (asyncio.CancelledError if cancelled else RuntimeError)(
        "synthetic host registration failed"
    )
    connections = []
    database = tmp_path / "outbox-test.db"
    async with aiosqlite.connect(database) as setup:
        await setup.execute(
            "CREATE TABLE executor_notification_outbox "
            "(status TEXT, attempts INTEGER, available_at REAL)"
        )
        await setup.commit()

    async def open_connection():
        connection = await aiosqlite.connect(database)
        connections.append(connection)
        return connection

    def add_job(*args, **kwargs):
        if service._db not in connections:
            connections.append(service._db)
        raise failure

    host = SimpleNamespace(add_interval_job=add_job, remove_job=lambda *args: None)
    storage = SimpleNamespace(_open_connection=open_connection, db_path=str(database))
    service = service_type(storage, host)
    try:
        with pytest.raises(type(failure)) as caught:
            await service.initialize()
        assert caught.value is failure
        assert len(connections) == 1
        assert service._db is connections[0]
        await shutdown_services([("outbox", service.shutdown)])
        assert service._db is None
        with pytest.raises(ValueError, match="no active connection"):
            await connections[0].execute("SELECT 1")
        await service.shutdown()
    finally:
        for connection in connections:
            await connection.close()
