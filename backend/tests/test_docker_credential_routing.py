"""Regression coverage for credential routing in Docker registry discovery."""

from __future__ import annotations

import httpx
import pytest

from releasetracker.trackers.docker import DockerTracker


class _Client:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []

    async def get(self, url: str, *, headers=None, **_kwargs):
        self.calls.append(("GET", url, headers))
        return self.responses.pop(0)

    async def request(self, method: str, url: str, *, headers=None, **_kwargs):
        self.calls.append((method, url, headers))
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_docker_auth_realm_never_receives_cross_origin_registry_credential(monkeypatch):
    tracker = DockerTracker(
        name="private", image="org/image", registry="registry.example", token="user:secret"
    )
    monkeypatch.setitem(
        DockerTracker._get_bearer_token.__globals__["_REGISTRY_AUTH"],
        tracker.registry,
        {"realm": "https://auth.example/token", "service": "registry.example"},
    )
    client = _Client(
        [
            httpx.Response(
                200,
                json={"token": "registry-bearer"},
                request=httpx.Request("GET", "https://auth.example/token"),
            )
        ]
    )

    assert await tracker._get_bearer_token(client, "repository:org/image:pull") == "registry-bearer"
    assert client.calls == [("GET", "https://auth.example/token", {})]


@pytest.mark.asyncio
async def test_docker_rejects_cleartext_auth_realm_before_network_access(monkeypatch):
    tracker = DockerTracker(
        name="private", image="org/image", registry="registry.example", token="user:secret"
    )
    monkeypatch.setitem(
        DockerTracker._get_bearer_token.__globals__["_REGISTRY_AUTH"],
        tracker.registry,
        {"realm": "http://attacker.example/token", "service": "registry.example"},
    )
    client = _Client([])

    with pytest.raises(ValueError, match="HTTPS"):
        await tracker._get_bearer_token(client, "repository:org/image:pull")
    assert client.calls == []


@pytest.mark.asyncio
async def test_docker_rejects_cross_origin_pagination_before_forwarding_credential():
    tracker = DockerTracker(
        name="private", image="org/image", registry="registry.example", token="user:secret"
    )
    client = _Client(
        [
            httpx.Response(
                200,
                json={"tags": ["latest"]},
                headers={
                    "Link": '<https://attacker.example/v2/org/image/tags/list?n=1>; rel="next"'
                },
                request=httpx.Request("GET", "https://registry.example/v2/org/image/tags/list"),
            )
        ]
    )

    with pytest.raises(ValueError, match="cross-origin"):
        await tracker._fetch_tags(client, None)
    assert len(client.calls) == 1
    assert client.calls[0][2] == {"Authorization": "Basic dXNlcjpzZWNyZXQ="}


@pytest.mark.asyncio
async def test_docker_rejects_registry_redirect_before_following_it():
    tracker = DockerTracker(name="private", image="org/image", registry="registry.example")
    client = _Client(
        [
            httpx.Response(
                302,
                headers={"Location": "https://attacker.example/"},
                request=httpx.Request("GET", "https://registry.example/v2/"),
            )
        ]
    )

    with pytest.raises(ValueError, match="redirect"):
        await tracker._registry_request(client, "GET", "https://registry.example/v2/")
    assert len(client.calls) == 1
