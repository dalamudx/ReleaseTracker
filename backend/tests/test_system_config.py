import pytest


@pytest.mark.asyncio
async def test_removed_config_endpoint(authed_client):
    response = authed_client.get("/api/config")

    assert response.status_code == 404, response.text
