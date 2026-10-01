from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from releasetracker.routers.system import router


def test_health_without_storage_is_not_ready():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/api/health/live").json() == {"status": "ok"}
        assert client.get("/api/health/ready").status_code == 503


@pytest.mark.asyncio
async def test_health_ready_with_live_storage(storage):
    from httpx import ASGITransport, AsyncClient

    app = FastAPI()
    app.state.storage = storage
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/health/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
