"""Portainer live-image reads release HTTP pools and respect injected ownership."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.portainer import PortainerRuntimeAdapter


def runtime():
    return RuntimeConnectionConfig(
        name="test-portainer",
        type="portainer",
        credential_id=1,
        config={"base_url": "http://portainer.test", "endpoint_id": 1},
        secrets={"api_key": "synthetic-test-key"},
    )


async def test_close_owned_client_is_idempotent_and_can_reopen():
    adapter = PortainerRuntimeAdapter(runtime())
    first = adapter._get_client()
    assert not first.is_closed
    await adapter.close()
    assert first.is_closed
    assert adapter._client is None
    await adapter.close()
    second = adapter._get_client()
    assert second is not first
    assert not second.is_closed
    await adapter.close()
    assert second.is_closed


async def test_close_before_first_request_does_not_create_client():
    config = runtime()
    config.secrets = {}
    adapter = PortainerRuntimeAdapter(config)
    await adapter.close()
    assert adapter._client is None


async def test_close_does_not_steal_injected_client_ownership():
    async with httpx.AsyncClient() as injected:
        adapter = PortainerRuntimeAdapter(runtime(), client=injected)
        assert adapter._get_client() is injected
        await adapter.close()
        assert not injected.is_closed
        owned = adapter._get_client()
        assert owned is not injected
        await adapter.close()
        assert owned.is_closed


async def test_close_failure_still_releases_adapter_reference(monkeypatch):
    client = SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError("synthetic close failure")))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    adapter = PortainerRuntimeAdapter(runtime())
    assert adapter._get_client() is client
    with pytest.raises(RuntimeError, match="synthetic close failure"):
        await adapter.close()
    assert adapter._client is None
    await adapter.close()
    client.aclose.assert_awaited_once()
