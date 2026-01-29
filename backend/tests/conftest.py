import pytest
import os
import asyncio
from contextlib import asynccontextmanager
from typing import Generator
from fastapi.testclient import TestClient

# Mock environment variables BEFORE importing app
os.environ["JWT_SECRET"] = "test-secret"
os.environ["ENCRYPTION_KEY"] = "Z7wz8u_u8Y7j6B1b4C9d2E5f8G1h3I4j5K6l7M8n9O0="

from releasetracker.main import app
from releasetracker.storage.sqlite import SQLiteStorage
from releasetracker.dependencies import get_storage, get_scheduler
from releasetracker.services.auth import AuthService
from unittest.mock import AsyncMock





@pytest.fixture(scope="function")
async def storage(tmp_path):
    """Create a temporary storage instance."""
    db_path = tmp_path / "test.db"
    storage = SQLiteStorage(str(db_path))
    await storage.initialize()
    return storage





@pytest.fixture(scope="function")
def client(storage):
    """Create a TestClient with overridden dependencies."""
    
    mock_scheduler = AsyncMock()
    # Mock refresh_tracker and remove_tracker to return success
    mock_scheduler.refresh_tracker.return_value = None
    mock_scheduler.remove_tracker.return_value = None
    mock_scheduler.check_tracker_now_v2.return_value = None
    
    def override_get_storage():
        return storage
        
    def override_get_scheduler():
        return mock_scheduler

    app.dependency_overrides[get_scheduler] = override_get_scheduler
    
    # Override lifespan to preventing real startup
    original_lifespan = app.router.lifespan_context
    
    @asynccontextmanager
    async def mock_lifespan(_app):
        _app.state.storage = storage

        _app.state.scheduler = mock_scheduler
        yield
        
    app.router.lifespan_context = mock_lifespan
    
    with TestClient(app) as c:
        yield c
        
    # Restore
    app.router.lifespan_context = original_lifespan
    
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
async def authed_client(client, auth_service):
    """Returns a client authenticated as a test user."""
    # 确保管理员存在
    await auth_service.ensure_admin_user()
    
    # 先以管理员登录
    admin_response = client.post("/api/auth/token", data={
        "username": "admin",
        "password": "admin"
    })
    admin_token = admin_response.json()["access_token"]
    
    # 管理员创建测试用户
    client.post("/api/auth/register", 
        json={
            "username": "authtester",
            "email": "authtester@example.com",
            "password": "password123"
        },
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    
    # 以测试用户身份登录
    response = client.post("/api/auth/token", data={
        "username": "authtester",
        "password": "password123"
    })
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.fixture(scope="function")
async def auth_service(storage):
    return AuthService(storage)
