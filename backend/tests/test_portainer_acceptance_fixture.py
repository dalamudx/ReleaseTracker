"""Check fixture isolation/cleanup without a real engine, network or production state."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

import real_portainer_fixture as fixture


@pytest.mark.parametrize(
    "failure", ["run", "pull", "api", "body", "cancel", "cleanup", "body_cleanup", None]
)
async def test_owned_fixture_cleans_exact_engine_on_all_paths(monkeypatch, failure):
    calls = []
    body_error = (
        asyncio.CancelledError() if failure == "cancel" else RuntimeError("synthetic failure")
    )
    clients = []

    async def command(*args, **kwargs):
        calls.append(args)
        if args[0] == "rm" and failure in ("cleanup", "body_cleanup"):
            raise OSError("synthetic cleanup failure")
        if (failure == "run" and args[0] == "run") or (failure == "pull" and "pull" in args):
            raise body_error
        return SimpleNamespace(
            returncode=1 if args[:2] == ("container", "exists") else 0, stdout="127.0.0.1:12345"
        )

    class Client:
        def __init__(self, **kwargs):
            self.is_closed = False
            clients.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.is_closed = True

        async def get(self, path):
            if failure == "api":
                raise body_error
            return httpx.Response(
                200,
                json={"Version": "2.45.1"},
                request=httpx.Request("GET", "http://127.0.0.1/api/status"),
            )

    monkeypatch.setattr(fixture, "podman", command)
    monkeypatch.setattr(fixture.httpx, "AsyncClient", Client)

    async def exercise():
        async with fixture.isolated_portainer() as instance:
            assert instance.base_url == "http://127.0.0.1:12345"
            if failure in ("body", "cancel", "body_cleanup"):
                raise body_error

    if failure:
        expected = (
            asyncio.CancelledError
            if failure == "cancel"
            else OSError if failure == "cleanup" else RuntimeError
        )
        with pytest.raises(expected) as caught:
            await exercise()
        if failure != "cleanup":
            assert caught.value is body_error
        if failure == "body_cleanup":
            assert caught.value.__notes__ == ["Owned fixture cleanup failed: OSError"]
    else:
        await exercise()
    name = calls[0][calls[0].index("--name") + 1]
    assert name.startswith("rt-owned-portainer-")
    if failure in ("cleanup", "body_cleanup"):
        assert calls[-1] == ("rm", "--force", "--time", "0", "--volumes", name)
    else:
        assert calls[-2] == ("rm", "--force", "--time", "0", "--volumes", name)
        assert calls[-1] == ("container", "exists", name)
    assert all(client.is_closed for client in clients)
    assert "127.0.0.1::9000" in calls[0]
    assert "--host=unix:///var/run/docker.sock" in calls[0]
    assert "-v" not in calls[0]
    assert not any("prune" in call for call in calls)


async def test_inventory_404_is_not_retried_as_container_replacement(monkeypatch):
    async def missing(*args):
        raise fixture.PortainerResourceNotFoundError(
            "synthetic missing inventory", "/api/endpoints/1/docker/containers/json"
        )

    monkeypatch.setattr(fixture.portainer_recovery, "containers", missing)
    with pytest.raises(fixture.PortainerResourceNotFoundError):
        await fixture.wait_native_stack(None, {"endpoint_id": 1}, {"api": "fixture:image"})
