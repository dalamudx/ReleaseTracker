"""Full application closing chain with isolated service doubles; no DB/key access."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

from releasetracker import main
from releasetracker.services import deployment_readiness, executor_notification_outbox
from releasetracker.services import deployment_admission_notifications, release_notification_outbox
from releasetracker.services import runtime_health_watch

ORDER = [
    "repository_webhook",
    "runtime_health",
    "queue",
    "readiness",
    "executor_outbox",
    "release_outbox",
    "admission_outbox",
    "executor",
    "host",
    "storage",
]


@pytest.fixture
def isolated_app(monkeypatch):
    closed, services = [], {}
    for name in ORDER + ["scheduler", "keys", "auth", "backup", "fetch", "deploy", "recover"]:

        async def close(name=name):
            closed.append(name)

        services[name] = SimpleNamespace(
            initialize=AsyncMock(),
            start=AsyncMock(),
            shutdown=AsyncMock(side_effect=close),
            close=AsyncMock(side_effect=close),
            add_interval_job=Mock(),
            register=Mock(),
            ensure_admin_user=AsyncMock(),
        )
    storage = services["storage"]
    storage.tasks = SimpleNamespace()
    storage.get_system_log_level = AsyncMock(return_value="INFO")
    storage.get_setting = AsyncMock(return_value=None)
    storage.reconcile_interrupted_source_fetch_runs = AsyncMock(return_value=0)
    storage.reconcile_stale_executor_snapshot_claims = AsyncMock(return_value=0)
    services["backup"].status = AsyncMock(return_value={})
    services["backup"].load_configuration = AsyncMock()
    services["backup"].reschedule = AsyncMock()
    for symbol, name in [
        ("SystemKeyManager", "keys"),
        ("SQLiteStorage", "storage"),
        ("AuthService", "auth"),
        ("SchedulerHost", "host"),
        ("ReleaseScheduler", "scheduler"),
        ("ExecutorScheduler", "executor"),
        ("RepositoryWebhookScheduler", "repository_webhook"),
        ("InstanceBackup", "backup"),
        ("TaskQueue", "queue"),
        ("FetchTasks", "fetch"),
        ("DeployTasks", "deploy"),
        ("RecoveryTasks", "recover"),
    ]:
        monkeypatch.setattr(main, symbol, lambda *a, name=name, **kw: services[name])
    for module, symbol, name in [
        (deployment_readiness, "DeploymentReadiness", "readiness"),
        (executor_notification_outbox, "ExecutorNotificationOutbox", "executor_outbox"),
        (release_notification_outbox, "ReleaseNotificationOutbox", "release_outbox"),
        (
            deployment_admission_notifications,
            "DeploymentAdmissionNotificationOutbox",
            "admission_outbox",
        ),
        (runtime_health_watch, "RuntimeHealthWatch", "runtime_health"),
    ]:
        monkeypatch.setattr(module, symbol, lambda *a, name=name, **kw: services[name])
    monkeypatch.setattr(main, "recover_pending_encryption_key_rotation", AsyncMock())
    monkeypatch.setattr(main, "migrate_legacy_snapshots", AsyncMock(return_value=0))
    monkeypatch.setattr(main, "retention_tiers", lambda: None)
    monkeypatch.setattr(main.LogConfig, "setup_logging", lambda **kw: None)
    return FastAPI(), services, closed


STARTUP_STAGES = [
    ("keys", "initialize", []),
    ("storage", "initialize", ["storage"]),
    ("rotation", None, ["storage"]),
    ("migration", None, ["storage"]),
    ("auth", "ensure_admin_user", ["storage"]),
    ("backup", "status", ["host", "storage"]),
    *[
        (name, "initialize", ORDER)
        for name in (
            "queue",
            "readiness",
            "runtime_health",
            "executor_outbox",
            "release_outbox",
            "admission_outbox",
            "scheduler",
            "executor",
            "repository_webhook",
        )
    ],
    *[(name, "start", ORDER) for name in ("host", "scheduler", "executor")],
]


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("component,method,expected", STARTUP_STAGES)
async def test_partial_startup_closes_owned_resources_without_entering_app(
    isolated_app, monkeypatch, component, method, expected, cancelled
):
    app, services, closed = isolated_app
    failure = (asyncio.CancelledError if cancelled else RuntimeError)("synthetic startup failure")
    operation = AsyncMock(side_effect=failure)
    if component == "rotation":
        monkeypatch.setattr(main, "recover_pending_encryption_key_rotation", operation)
    elif component == "migration":
        monkeypatch.setattr(main, "migrate_legacy_snapshots", operation)
    else:
        setattr(services[component], method, operation)
    with pytest.raises(type(failure)) as caught:
        async with main.lifespan(app):
            pytest.fail("failed startup must never yield a running application")
    assert caught.value is failure
    assert closed == expected
    if "storage" in expected:
        services["storage"].close.assert_awaited_once()
    else:
        services["storage"].close.assert_not_awaited()


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("SchedulerHost", ["storage"]),
        ("InstanceBackup", ["host", "storage"]),
        ("ReleaseScheduler", ["host", "storage"]),
        ("ExecutorScheduler", ["host", "storage"]),
        ("RepositoryWebhookScheduler", ["executor", "host", "storage"]),
        ("FetchTasks", ORDER),
        ("DeployTasks", ORDER),
        ("RecoveryTasks", ORDER),
    ],
)
async def test_constructor_failure_closes_previously_constructed_owners(
    isolated_app, monkeypatch, symbol, expected
):
    app, services, closed = isolated_app
    failure = ValueError("synthetic constructor failure")
    monkeypatch.setattr(main, symbol, Mock(side_effect=failure))
    with pytest.raises(ValueError) as caught:
        async with main.lifespan(app):
            pytest.fail("constructor failure must not yield")
    assert caught.value is failure
    assert closed == expected


@pytest.mark.parametrize(
    "module,symbol,expected",
    [
        (
            executor_notification_outbox,
            "ExecutorNotificationOutbox",
            ["repository_webhook", "executor", "host", "storage"],
        ),
        (
            release_notification_outbox,
            "ReleaseNotificationOutbox",
            ["repository_webhook", "executor_outbox", "executor", "host", "storage"],
        ),
        (
            deployment_admission_notifications,
            "DeploymentAdmissionNotificationOutbox",
            [
                "repository_webhook",
                "executor_outbox",
                "release_outbox",
                "executor",
                "host",
                "storage",
            ],
        ),
        (
            deployment_readiness,
            "DeploymentReadiness",
            [
                "repository_webhook",
                "executor_outbox",
                "release_outbox",
                "admission_outbox",
                "executor",
                "host",
                "storage",
            ],
        ),
        (
            runtime_health_watch,
            "RuntimeHealthWatch",
            [
                "repository_webhook",
                "readiness",
                "executor_outbox",
                "release_outbox",
                "admission_outbox",
                "executor",
                "host",
                "storage",
            ],
        ),
        (main, "TaskQueue", [name for name in ORDER if name != "queue"]),
    ],
)
async def test_service_constructor_failure_only_closes_preexisting_owners(
    isolated_app, monkeypatch, module, symbol, expected
):
    app, services, closed = isolated_app
    failure = ValueError("synthetic service constructor failure")
    monkeypatch.setattr(module, symbol, Mock(side_effect=failure))
    with pytest.raises(ValueError) as caught:
        async with main.lifespan(app):
            pytest.fail("failed construction must not yield")
    assert caught.value is failure
    assert closed == expected


@pytest.mark.parametrize("cancelled", [False, True])
async def test_storage_partial_initialize_closes_real_connection_on_failure(
    isolated_app, monkeypatch, tmp_path, cancelled
):
    from cryptography.fernet import Fernet
    from releasetracker.storage.sqlite import SQLiteStorage

    app, services, closed = isolated_app
    storage = SQLiteStorage(
        str(tmp_path / "partial-startup.db"),
        system_key_manager=SimpleNamespace(encryption_key=Fernet.generate_key()),
    )
    failure = (asyncio.CancelledError if cancelled else RuntimeError)(
        "synthetic DB initialization failure"
    )
    state = {}

    async def partial_initialize():
        state["connection"] = await storage._get_connection()
        await state["connection"].execute("CREATE TABLE synthetic(value TEXT)")
        await state["connection"].commit()
        await state["connection"].execute("INSERT INTO synthetic VALUES ('uncommitted')")
        raise failure

    storage.initialize = partial_initialize
    monkeypatch.setattr(main, "SQLiteStorage", lambda *a, **kw: storage)
    try:
        with pytest.raises(type(failure)) as caught:
            async with main.lifespan(app):
                pytest.fail("failed DB initialization must not yield")
        assert caught.value is failure
        assert not closed, "no later service has been constructed"
        assert not storage._task_connections
        assert not storage._task_connection_cleanup_tasks
        with pytest.raises(ValueError, match="no active connection"):
            await state["connection"].execute("SELECT 1")
        import sqlite3

        with sqlite3.connect(storage.db_path) as probe:
            assert probe.execute("SELECT COUNT(*) FROM synthetic").fetchone()[0] == 0
    finally:
        await storage.close()


async def test_partial_initialize_failure_drains_started_worker_before_storage_close(isolated_app):
    app, services, closed = isolated_app
    worker_entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    task_holder = {}
    failure = RuntimeError("synthetic partial initialization failure")

    async def worker():
        worker_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    async def initialize():
        task_holder["worker"] = asyncio.create_task(worker())
        await worker_entered.wait()
        raise failure

    async def close():
        closed.append("queue")
        task_holder["worker"].cancel()
        await asyncio.gather(task_holder["worker"], return_exceptions=True)

    services["queue"].initialize = AsyncMock(side_effect=initialize)
    services["queue"].shutdown = AsyncMock(side_effect=close)

    async def startup():
        async with main.lifespan(app):
            pytest.fail("partial initializer must not yield")

    task = asyncio.create_task(startup())
    try:
        await asyncio.wait_for(cleaning.wait(), timeout=1)
        services["storage"].close.assert_not_awaited()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "startup cancellation must wait for cleanup drain"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert closed == ORDER
        assert task_holder["worker"].cancelled()
        services["storage"].close.assert_awaited_once()
    finally:
        release.set()
        if "worker" in task_holder:
            task_holder["worker"].cancel()
            await asyncio.gather(task_holder["worker"], return_exceptions=True)
        await asyncio.gather(task, return_exceptions=True)


async def test_body_cancellation_wins_over_cleanup_error_after_all_services_close(isolated_app):
    app, services, closed = isolated_app
    body_cancel = asyncio.CancelledError("synthetic body cancelled")

    async def fail():
        closed.append("executor")
        raise RuntimeError("synthetic close error")

    services["executor"].shutdown = AsyncMock(side_effect=fail)
    with pytest.raises(asyncio.CancelledError) as caught:
        async with main.lifespan(app):
            raise body_cancel
    assert caught.value is body_cancel
    assert closed == ORDER


@pytest.mark.parametrize("late_cancel", [False, True])
async def test_combined_close_failures_preserve_first_error_or_cancel_priority(
    isolated_app, late_cancel
):
    app, services, closed = isolated_app
    first = RuntimeError("synthetic first close error")
    late = (asyncio.CancelledError if late_cancel else ValueError)("synthetic later error")

    async def fail_early():
        closed.append("repository_webhook")
        raise first

    async def fail_late():
        closed.append("storage")
        raise late

    services["repository_webhook"].shutdown = AsyncMock(side_effect=fail_early)
    services["storage"].close = AsyncMock(side_effect=fail_late)
    expected = late if late_cancel else first
    with pytest.raises(type(expected)) as caught:
        async with main.lifespan(app):
            pass
    assert caught.value is expected
    assert closed == ORDER


async def test_exception_in_lifespan_body_still_closes_every_service(isolated_app):
    app, services, closed = isolated_app
    failure = ValueError("synthetic body failure")
    with pytest.raises(ValueError) as caught:
        async with main.lifespan(app):
            raise failure
    assert caught.value is failure
    assert closed == ORDER
    services["storage"].close.assert_awaited_once()


@pytest.mark.parametrize("component", ["repository_webhook", "executor", "storage"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_close_failure_does_not_skip_remaining_services(
    isolated_app, component, cancelled, caplog
):
    app, services, closed = isolated_app
    failure = (asyncio.CancelledError if cancelled else RuntimeError)(
        "synthetic private remote detail"
    )

    async def fail():
        closed.append(component)
        raise failure

    if component == "storage":
        services[component].close = AsyncMock(side_effect=fail)
    else:
        services[component].shutdown = AsyncMock(side_effect=fail)
    with pytest.raises(type(failure)) as caught:
        async with main.lifespan(app):
            pass
    assert caught.value is failure
    assert closed == ORDER
    assert str(failure) not in caplog.text
    assert "service_shutdown_failed" in caplog.text


async def test_repeated_body_and_cleanup_cancellation_waits_for_ordered_shutdown(isolated_app):
    app, services, closed = isolated_app
    entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def slow_close():
        closed.append("repository_webhook")
        closing.set()
        await release.wait()

    services["repository_webhook"].shutdown = AsyncMock(side_effect=slow_close)

    async def application():
        async with main.lifespan(app):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(application())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        await asyncio.wait_for(closing.wait(), timeout=1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "cancelled app must wait for shutdown before returning"
        assert closed == ["repository_webhook"]
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert closed == ORDER
        services["storage"].close.assert_awaited_once()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_protected_cleanup_closes_cross_task_sqlite_connections_and_rolls_back(tmp_path):
    import sqlite3
    from cryptography.fernet import Fernet
    from releasetracker.storage.sqlite import SQLiteStorage
    from releasetracker.services.shutdown import shutdown_services

    database = tmp_path / "isolated-shutdown.db"
    storage = SQLiteStorage(
        str(database), system_key_manager=SimpleNamespace(encryption_key=Fernet.generate_key())
    )
    startup = await storage._get_connection()
    await startup.execute("CREATE TABLE synthetic (value TEXT)")
    await startup.commit()
    acquired, release = asyncio.Event(), asyncio.Event()
    state = {}

    async def request():
        state["connection"] = await storage._get_connection()
        await state["connection"].execute("INSERT INTO synthetic VALUES (?)", ("uncommitted",))
        acquired.set()
        await release.wait()

    task = asyncio.create_task(request())
    try:
        await asyncio.wait_for(acquired.wait(), timeout=1)
        assert len(storage._task_connections) == 2
        assert state["connection"] is not startup
        assert state["connection"].in_transaction
        await shutdown_services([("storage", storage.close)])
        assert not storage._task_connections
        assert not storage._task_connection_cleanup_tasks
        for connection in (startup, state["connection"]):
            with pytest.raises(ValueError, match="no active connection"):
                await connection.execute("SELECT 1")
        with sqlite3.connect(database) as probe:
            assert probe.execute("SELECT COUNT(*) FROM synthetic").fetchone()[0] == 0
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await storage.close()
