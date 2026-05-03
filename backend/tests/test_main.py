import pytest

from releasetracker import main as main_module


class FakeStorage:
    def __init__(self, _db_path: str):
        self.closed = False
        self.events: list[str] = []

    async def initialize(self):
        self.events.append("storage.initialize")
        return None

    async def get_system_log_level(self):
        return "INFO"

    async def close(self):
        self.closed = True


class FakeAuthService:
    def __init__(self, storage, system_key_manager):
        self.storage = storage
        self.system_key_manager = system_key_manager
        self.ensure_admin_called = False

    async def ensure_admin_user(self):
        self.ensure_admin_called = True
        self.storage.events.append("auth.ensure_admin_user")


class FakeSchedulerHost:
    def __init__(self):
        self.start_called = False
        self.shutdown_called = False
        self.schedulers: list[object] = []

    async def start(self):
        self.start_called = True
        if self.schedulers:
            storage = getattr(self.schedulers[0], "storage", None)
            if storage is not None:
                storage.events.append("scheduler_host.start")

    async def shutdown(self):
        self.shutdown_called = True


class FakeScheduler:
    def __init__(self, storage, *, scheduler_host=None):
        self.storage = storage
        self.scheduler_host = scheduler_host
        self.initialize_called = False
        self.start_called = False

        if scheduler_host is not None and hasattr(scheduler_host, "schedulers"):
            scheduler_host.schedulers.append(self)

    async def initialize(self):
        self.initialize_called = True
        self.storage.events.append("scheduler.initialize")

    async def start(self):
        self.start_called = True
        self.storage.events.append("scheduler.start")


class FakeExecutorScheduler:
    def __init__(self, storage, *, scheduler_host=None):
        self.storage = storage
        self.scheduler_host = scheduler_host
        self.initialize_called = False
        self.start_called = False
        self.shutdown_called = False

        if scheduler_host is not None and hasattr(scheduler_host, "schedulers"):
            scheduler_host.schedulers.append(self)

    async def initialize(self):
        self.initialize_called = True
        self.storage.events.append("executor.initialize")

    async def start(self):
        self.start_called = True
        self.storage.events.append("executor.start")

    async def shutdown(self):
        self.shutdown_called = True


@pytest.mark.asyncio
async def test_lifespan_starts_without_identity_drift_repair(monkeypatch):
    fake_storage_holder = {}
    fake_auth_holder = {}
    fake_scheduler_host_holder = {}
    fake_scheduler_holder = {}
    fake_executor_holder = {}

    def fake_storage_factory(db_path: str, system_key_manager=None):
        storage = FakeStorage(db_path)
        storage.system_key_manager = system_key_manager
        fake_storage_holder["storage"] = storage
        return storage

    def fake_auth_factory(storage, system_key_manager):
        auth = FakeAuthService(storage, system_key_manager)
        fake_auth_holder["auth"] = auth
        return auth

    def fake_scheduler_host_factory():
        host = FakeSchedulerHost()
        fake_scheduler_host_holder["scheduler_host"] = host
        return host

    def fake_scheduler_factory(storage, *, scheduler_host=None):
        scheduler = FakeScheduler(storage, scheduler_host=scheduler_host)
        fake_scheduler_holder["scheduler"] = scheduler
        return scheduler

    def fake_executor_factory(storage, *, scheduler_host=None):
        scheduler = FakeExecutorScheduler(storage, scheduler_host=scheduler_host)
        fake_executor_holder["executor"] = scheduler
        return scheduler

    monkeypatch.setattr(main_module, "SQLiteStorage", fake_storage_factory)
    monkeypatch.setattr(main_module, "AuthService", fake_auth_factory)
    monkeypatch.setattr(main_module, "SchedulerHost", fake_scheduler_host_factory)
    monkeypatch.setattr(main_module, "ReleaseScheduler", fake_scheduler_factory)
    monkeypatch.setattr(main_module, "ExecutorScheduler", fake_executor_factory)

    async with main_module.lifespan(main_module.app):
        storage = fake_storage_holder["storage"]
        auth = fake_auth_holder["auth"]
        scheduler_host = fake_scheduler_host_holder["scheduler_host"]
        scheduler = fake_scheduler_holder["scheduler"]
        executor = fake_executor_holder["executor"]

        assert main_module.app.state.storage is storage
        assert main_module.app.state.system_key_manager is storage.system_key_manager
        assert auth.system_key_manager is storage.system_key_manager
        assert main_module.app.state.scheduler_host is scheduler_host
        assert auth.ensure_admin_called is True
        assert scheduler_host.start_called is True
        assert scheduler.scheduler_host is scheduler_host
        assert executor.scheduler_host is scheduler_host
        assert scheduler.initialize_called is True
        assert scheduler.start_called is True
        assert executor.initialize_called is True
        assert executor.start_called is True
        assert storage.events == [
            "storage.initialize",
            "auth.ensure_admin_user",
            "scheduler.initialize",
            "executor.initialize",
            "scheduler_host.start",
            "scheduler.start",
            "executor.start",
        ]

    assert fake_storage_holder["storage"].closed is True
    assert fake_scheduler_host_holder["scheduler_host"].shutdown_called is True
    assert fake_executor_holder["executor"].shutdown_called is True
