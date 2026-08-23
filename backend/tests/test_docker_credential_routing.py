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
async def test_docker_rejects_cross_origin_registry_probe_redirect():
    tracker = DockerTracker(
        name="private",
        image="org/image",
        registry="registry.example",
        allow_registry_redirects=True,
    )
    client = _Client(
        [
            httpx.Response(
                302,
                headers={"Location": "https://attacker.example/v2/"},
                request=httpx.Request("GET", "https://registry.example/v2/"),
            ),
        ]
    )

    with pytest.raises(ValueError, match="cross-origin"):
        await tracker._get_bearer_token(client, "repository:org/image:pull")
    assert client.calls == [("GET", "https://registry.example/v2/", {})]


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
        name="private",
        image="org/image",
        registry="registry.example",
        token="user:secret",
        allow_registry_redirects=True,
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
    assert tracker.allow_registry_redirects is False
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


@pytest.mark.asyncio
async def test_docker_allows_same_origin_metadata_redirect_with_credentials():
    tracker = DockerTracker(
        name="private",
        image="org/image",
        registry="registry.example",
        token="user:secret",
        allow_registry_redirects=True,
    )
    client = _Client(
        [
            httpx.Response(
                307,
                headers={"Location": "/v2/org/image/tags/list?canonical=1"},
                request=httpx.Request("GET", "https://registry.example/v2/org/image/tags/list"),
            ),
            httpx.Response(
                200,
                json={"tags": ["latest"]},
                request=httpx.Request(
                    "GET", "https://registry.example/v2/org/image/tags/list?canonical=1"
                ),
            ),
        ]
    )

    assert await tracker._fetch_tags(client, None) == ["latest"]
    assert client.calls == [
        (
            "GET",
            "https://registry.example/v2/org/image/tags/list",
            {"Authorization": "Basic dXNlcjpzZWNyZXQ="},
        ),
        (
            "GET",
            "https://registry.example/v2/org/image/tags/list?canonical=1",
            {"Authorization": "Basic dXNlcjpzZWNyZXQ="},
        ),
    ]


@pytest.mark.asyncio
async def test_docker_resolves_relative_pagination_from_current_page():
    tracker = DockerTracker(
        name="private", image="org/image", registry="registry.example", token="user:secret"
    )
    client = _Client(
        [
            httpx.Response(
                200,
                json={"tags": ["v1"]},
                headers={"Link": '<?last=v1>; rel="next"'},
                request=httpx.Request("GET", "https://registry.example/v2/org/image/tags/list"),
            ),
            httpx.Response(
                200,
                json={"tags": ["v2"]},
                request=httpx.Request(
                    "GET", "https://registry.example/v2/org/image/tags/list?last=v1"
                ),
            ),
        ]
    )

    assert await tracker._fetch_tags(client, None) == ["v1", "v2"]
    assert [call[1] for call in client.calls] == [
        "https://registry.example/v2/org/image/tags/list",
        "https://registry.example/v2/org/image/tags/list?last=v1",
    ]


@pytest.mark.asyncio
async def test_docker_rejects_pagination_loop():
    tracker = DockerTracker(name="private", image="org/image", registry="registry.example")
    client = _Client(
        [
            httpx.Response(
                200,
                json={"tags": ["v1"]},
                headers={"Link": '<?last=v1>; rel="next"'},
                request=httpx.Request("GET", "https://registry.example/v2/org/image/tags/list"),
            ),
            httpx.Response(
                200,
                json={"tags": ["v1"]},
                headers={"Link": '<?last=v1>; rel="next"'},
                request=httpx.Request(
                    "GET", "https://registry.example/v2/org/image/tags/list?last=v1"
                ),
            ),
        ]
    )

    with pytest.raises(ValueError, match="pagination loop"):
        await tracker._fetch_tags(client, None)
    assert [call[1] for call in client.calls] == [
        "https://registry.example/v2/org/image/tags/list",
        "https://registry.example/v2/org/image/tags/list?last=v1",
    ]


@pytest.mark.asyncio
async def test_docker_auth_realm_allows_only_same_origin_redirects(monkeypatch):
    tracker = DockerTracker(
        name="private",
        image="org/image",
        registry="registry.example",
        token="user:secret",
        allow_registry_redirects=True,
    )
    monkeypatch.setitem(
        DockerTracker._get_bearer_token.__globals__["_REGISTRY_AUTH"],
        tracker.registry,
        {"realm": "https://auth.example/token", "service": "registry.example"},
    )
    client = _Client(
        [
            httpx.Response(
                307,
                headers={"Location": "/token2"},
                request=httpx.Request("GET", "https://auth.example/token"),
            ),
            httpx.Response(
                200,
                json={"token": "registry-bearer"},
                request=httpx.Request("GET", "https://auth.example/token2"),
            ),
        ]
    )

    assert await tracker._get_bearer_token(client, "repository:org/image:pull") == "registry-bearer"
    assert client.calls == [
        ("GET", "https://auth.example/token", {}),
        ("GET", "https://auth.example/token2", {}),
    ]


@pytest.mark.asyncio
async def test_docker_auth_realm_relative_redirect_replaces_query_without_reapplying_params(
    monkeypatch,
):
    tracker = DockerTracker(
        name="private",
        image="org/image",
        registry="registry.example",
        allow_registry_redirects=True,
    )
    monkeypatch.setitem(
        DockerTracker._get_bearer_token.__globals__["_REGISTRY_AUTH"],
        tracker.registry,
        {"realm": "https://auth.example/token", "service": "registry.example"},
    )
    seen_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        if request.url.query == b"ticket=abc":
            return httpx.Response(200, json={"token": "registry-bearer"}, request=request)
        return httpx.Response(307, headers={"Location": "?ticket=abc"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        token = await tracker._get_bearer_token(client, "repository:org/image:pull")

    assert token == "registry-bearer"
    assert len(seen_urls) == 2
    assert seen_urls[0].startswith("https://auth.example/token?")
    assert "scope=repository%3Aorg%2Fimage%3Apull" in seen_urls[0]
    assert "service=registry.example" in seen_urls[0]
    assert seen_urls[1] == "https://auth.example/token?ticket=abc"


@pytest.mark.asyncio
async def test_docker_pagination_page_limit_is_enforced(monkeypatch):
    tracker = DockerTracker(name="private", image="org/image", registry="registry.example")
    monkeypatch.setitem(
        DockerTracker._fetch_tags.__globals__,
        "_REGISTRY_MAX_TAG_PAGES",
        2,
    )
    client = _Client(
        [
            httpx.Response(
                200,
                json={"tags": ["v1"]},
                headers={"Link": '<?page=2>; rel="next"'},
                request=httpx.Request("GET", "https://registry.example/v2/org/image/tags/list"),
            ),
            httpx.Response(
                200,
                json={"tags": ["v2"]},
                headers={"Link": '<?page=3>; rel="next"'},
                request=httpx.Request(
                    "GET", "https://registry.example/v2/org/image/tags/list?page=2"
                ),
            ),
        ]
    )

    with pytest.raises(ValueError, match="pagination exceeded limit"):
        await tracker._fetch_tags(client, None)
    assert [call[1] for call in client.calls] == [
        "https://registry.example/v2/org/image/tags/list",
        "https://registry.example/v2/org/image/tags/list?page=2",
    ]


@pytest.mark.asyncio
async def test_docker_auth_realm_rejects_cross_origin_redirect(monkeypatch):
    tracker = DockerTracker(
        name="private",
        image="org/image",
        registry="registry.example",
        token="user:secret",
        allow_registry_redirects=True,
    )
    monkeypatch.setitem(
        DockerTracker._get_bearer_token.__globals__["_REGISTRY_AUTH"],
        tracker.registry,
        {"realm": "https://registry.example/token", "service": "registry.example"},
    )
    client = _Client(
        [
            httpx.Response(
                302,
                headers={"Location": "https://auth.example/token"},
                request=httpx.Request("GET", "https://registry.example/token"),
            ),
        ]
    )

    assert await tracker._get_bearer_token(client, "repository:org/image:pull") is None
    assert client.calls == [
        ("GET", "https://registry.example/token", {"Authorization": "Basic dXNlcjpzZWNyZXQ="})
    ]
