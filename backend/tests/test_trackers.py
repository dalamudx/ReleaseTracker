import pytest


@pytest.mark.asyncio
async def test_create_tracker(authed_client):
    data = {
        "name": "test-tracker",
        "type": "github",
        "repo": "owner/repo",
        "channels": [{"name": "stable", "type": "release", "enabled": True}],
        "interval": 60,
        "enabled": True,
    }
    response = authed_client.post("/api/trackers", json=data)
    assert response.status_code == 200, f"Failed: {response.text}"
    assert response.json()["message"] == "追踪器 test-tracker 已创建"

    # Verify it exists
    resp = authed_client.get("/api/trackers/test-tracker")
    assert resp.status_code == 200
    assert resp.json()["name"] == "test-tracker"


@pytest.mark.asyncio
async def test_get_trackers_pagination(authed_client):
    # Create 2 trackers
    authed_client.post(
        "/api/trackers", json={"name": "t1", "type": "github", "repo": "o/r1", "interval": 60}
    )
    authed_client.post(
        "/api/trackers", json={"name": "t2", "type": "github", "repo": "o/r2", "interval": 60}
    )

    response = authed_client.get("/api/trackers?limit=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["total"] >= 2


@pytest.mark.asyncio
async def test_update_tracker(authed_client):
    # Setup
    authed_client.post(
        "/api/trackers",
        json={"name": "update-test", "type": "github", "repo": "o/r", "interval": 60},
    )

    # Update interval
    response = authed_client.put(
        "/api/trackers/update-test",
        json={
            "name": "update-test",  # Name must match or be omitted if valid
            "type": "github",
            "repo": "o/r",
            "interval": 120,
        },
    )
    assert response.status_code == 200, f"Failed: {response.text}"

    # Verify
    resp = authed_client.get("/api/trackers/update-test/config")
    assert resp.status_code == 200
    assert resp.json()["interval"] == 120


@pytest.mark.asyncio
async def test_delete_tracker(authed_client):
    authed_client.post(
        "/api/trackers", json={"name": "del-test", "type": "github", "repo": "o/r", "interval": 60}
    )

    response = authed_client.delete("/api/trackers/del-test")
    assert response.status_code == 200

    # Verify gone
    resp = authed_client.get("/api/trackers/del-test")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_duplicate_failure(authed_client):
    data = {"name": "dup", "type": "github", "repo": "o/r", "interval": 60}
    authed_client.post("/api/trackers", json=data)

    response = authed_client.post("/api/trackers", json=data)
    assert response.status_code == 400
    assert "名称已存在" in response.json()["detail"]
