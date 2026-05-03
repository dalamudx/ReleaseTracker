import asyncio
import pytest
import pytest_asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Any, cast
import jwt
from fastapi.testclient import TestClient
from db_helpers import clone_sqlite_database, initialize_storage_with_schema

from releasetracker.main import app
from releasetracker.storage.sqlite import SQLiteStorage
from releasetracker.dependencies import get_executor_scheduler, get_scheduler
from releasetracker.executor_scheduler import ExecutorScheduler
from releasetracker.scheduler_host import SchedulerHost
from releasetracker.models import RegisterRequest, Session
from releasetracker.services.auth import AuthService
from releasetracker.services.system_keys import SystemKeyManager
from unittest.mock import AsyncMock


@pytest_asyncio.fixture(scope="function")
async def system_key_manager(tmp_path):
    manager = SystemKeyManager(tmp_path / "system-secrets.json")
    await manager.initialize()
    return manager


@pytest.fixture(scope="function")
async def storage(tmp_path, migrated_db_template, system_key_manager):
    """Create an isolated storage instance from a migrated template."""
    db_path = tmp_path / "test.db"
    clone_sqlite_database(migrated_db_template, db_path)
    storage = SQLiteStorage(str(db_path), system_key_manager=system_key_manager)
    try:
        yield storage
    finally:
        await storage.close()
        await asyncio.sleep(0)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def migrated_db_template(tmp_path_factory):
    template_dir = tmp_path_factory.mktemp("sqlite-template")
    template_db_path = template_dir / "template.db"

    manager = SystemKeyManager(template_dir / "system-secrets.json")
    await manager.initialize()
    storage = SQLiteStorage(str(template_db_path), system_key_manager=manager)
    await initialize_storage_with_schema(storage)

    auth_service = AuthService(storage, manager)
    await auth_service.ensure_admin_user()
    if await storage.get_user_by_username("authtester") is None:
        await auth_service.register(
            RegisterRequest(
                username="authtester",
                email="authtester@example.com",
                password="password123",
            )
        )

    await storage.close()
    return template_db_path


@pytest.fixture(scope="function")
def client(storage, system_key_manager):
    """Create a TestClient with overridden dependencies."""

    mock_scheduler = AsyncMock()
    scheduler_host = SchedulerHost()
    executor_scheduler = ExecutorScheduler(storage, scheduler_host=scheduler_host)
    # Mock refresh_tracker and remove_tracker to return success
    mock_scheduler.refresh_tracker.return_value = None
    mock_scheduler.remove_tracker.return_value = None
    mock_scheduler.check_tracker_now_v2.return_value = None

    def override_get_storage():
        return storage

    def override_get_scheduler():
        return mock_scheduler

    def override_get_executor_scheduler():
        return executor_scheduler

    app.dependency_overrides[get_scheduler] = override_get_scheduler
    app.dependency_overrides[get_executor_scheduler] = override_get_executor_scheduler

    # Override lifespan to preventing real startup
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def mock_lifespan(_app):
        _app.state.storage = storage
        _app.state.system_key_manager = system_key_manager

        _app.state.scheduler_host = scheduler_host
        _app.state.scheduler = mock_scheduler
        _app.state.executor_scheduler = executor_scheduler
        yield
        await executor_scheduler.shutdown()

    app.router.lifespan_context = mock_lifespan

    with TestClient(app) as c:
        cast(Any, c).executor_scheduler = executor_scheduler
        yield c

    # Restore
    app.router.lifespan_context = original_lifespan

    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
async def authed_client(client, auth_service):
    """Returns a client authenticated as a test user."""
    user = await auth_service.storage.get_user_by_username("authtester")
    if user is None:
        await auth_service.register(
            RegisterRequest(
                username="authtester",
                email="authtester@example.com",
                password="password123",
            )
        )
        user = await auth_service.storage.get_user_by_username("authtester")

    assert user is not None and user.id is not None

    token_pair = auth_service._create_token_pair(user)
    access_token = token_pair.access_token
    token_hash = auth_service._hash_token(access_token)
    refresh_token_hash = auth_service._hash_token(token_pair.refresh_token)
    expires_at = datetime.fromtimestamp(
        jwt.decode(access_token, auth_service.secret_key, algorithms=["HS256"])["exp"]
    )
    await auth_service.storage.create_session(
        Session(
            user_id=user.id,
            token_hash=token_hash,
            refresh_token_hash=refresh_token_hash,
            expires_at=expires_at,
        )
    )

    token = access_token
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.fixture(scope="function")
async def auth_service(storage, system_key_manager):
    return AuthService(storage, system_key_manager)
