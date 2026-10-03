"""Request-owned Portainer pools close on discovery success, failure and cancellation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
import httpx
import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.routers import runtime_connections


@pytest.mark.parametrize(
    "outcome", ["success", "forbidden", "missing", "timeout", "invalid", "cancelled"]
)
async def test_portainer_endpoint_discovery_closes_real_owned_pool(monkeypatch, outcome):
    config = RuntimeConnectionConfig(
        name="synthetic-discovery",
        type="portainer",
        credential_id=1,
        config={"base_url": "https://portainer.test", "endpoint_id": 1},
        secrets={"api_key": "synthetic-test-key"},
    )
    request_count = 0

    def respond(request):
        nonlocal request_count
        request_count += 1
        assert request.method == "GET"
        assert request.url.path == "/api/endpoints"
        if outcome == "cancelled":
            raise asyncio.CancelledError("synthetic request cancelled")
        if outcome == "timeout":
            raise httpx.ReadTimeout("synthetic request timeout", request=request)
        status = {"forbidden": 403, "missing": 404}.get(outcome, 200)
        payload = [{"Id": 1, "Name": "test", "Type": 1, "Status": 1}]
        if outcome in {"forbidden", "missing", "invalid"}:
            payload = {"message": "synthetic invalid response"}
        return httpx.Response(status, json=payload)

    original = httpx.AsyncClient
    created = []

    def client_factory(**kwargs):
        client = original(transport=httpx.MockTransport(respond), **kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        runtime_connections,
        "materialize_runtime_connection_credentials",
        AsyncMock(return_value=config),
    )
    storage = SimpleNamespace()
    draft = {
        "type": "portainer",
        "credential_id": 1,
        "config": {"base_url": "https://portainer.test"},
    }
    try:
        if outcome == "success":
            payload = await runtime_connections.discover_portainer_endpoints(draft, storage)
            assert payload == {"items": [{"id": 1, "name": "test", "type": "1", "status": "1"}]}
        elif outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError, match="synthetic request cancelled"):
                await runtime_connections.discover_portainer_endpoints(draft, storage)
        else:
            with pytest.raises(HTTPException) as caught:
                await runtime_connections.discover_portainer_endpoints(draft, storage)
            assert caught.value.status_code == 400
            assert "Endpoint discovery failed" in caught.value.detail
        assert request_count >= 1
        assert len(created) == 1
        assert created[0].is_closed, "request-owned HTTP pool leaked"
    finally:
        # A red regression must itself release its fixture resources.
        for client in created:
            await client.aclose()


async def test_materialization_failure_never_creates_pool(monkeypatch):
    def forbidden_factory(**kwargs):
        raise AssertionError("no HTTP pool allowed before credential materialization")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden_factory)
    monkeypatch.setattr(
        runtime_connections,
        "materialize_runtime_connection_credentials",
        AsyncMock(side_effect=ValueError("synthetic missing credential")),
    )
    with pytest.raises(HTTPException) as caught:
        await runtime_connections.discover_portainer_endpoints(
            {
                "type": "portainer",
                "credential_id": 1,
                "config": {"base_url": "https://portainer.test"},
            },
            SimpleNamespace(),
        )
    assert caught.value.status_code == 400
    assert "missing credential" in caught.value.detail
