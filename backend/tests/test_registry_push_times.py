"""Registry-native push times remain optional, credential-isolated and digest-bound."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from releasetracker.trackers.docker import DockerTracker

DIGEST = "sha256:" + "a" * 64
PUSHED = "2026-10-11T12:34:56Z"


def candidate(tracker, tag="1.2.0"):
    release = tracker._tag_to_release(tag)
    release.commit_sha = DIGEST
    return release


@pytest.mark.parametrize(
    "override,expected",
    [
        ({}, True),
        ({"digest": "sha256:" + "b" * 64}, False),
        ({"digest": None}, False),
        ({"name": "latest"}, False),
        ({"tag_last_pushed": None, "last_updated": PUSHED}, False),
        ({"tag_last_pushed": "invalid"}, False),
    ],
)
async def test_only_verified_provider_push_time_is_used(monkeypatch, override, expected):
    tracker = DockerTracker("test", "library/nginx", token="synthetic:secret")
    release = candidate(tracker)

    async def request(client, method, url, **kwargs):
        assert method == "GET" and url.startswith("https://hub.docker.com/")
        assert "Authorization" not in kwargs["headers"]
        assert kwargs["auth"] is None
        assert kwargs["allow_cross_origin_without_credentials"] is False
        assert "/blobs/" not in url
        return httpx.Response(
            200,
            json={
                "name": release.tag_name,
                "digest": DIGEST,
                "tag_last_pushed": PUSHED,
                **override,
            },
        )

    monkeypatch.setattr(tracker, "_request_with_redirect_policy", request)
    await tracker._enrich_registry_push_times([release])
    assert release.registry_pushed_at == (
        datetime(2026, 10, 11, 12, 34, 56, tzinfo=timezone.utc) if expected else None
    )
    assert release.published_at is None and release.artifact_created_at is None


@pytest.mark.parametrize("tag", [".", "..", "bad/tag", "bad?tag"])
async def test_invalid_tag_cannot_become_a_metadata_path(monkeypatch, tag):
    tracker = DockerTracker("test", "library/nginx")
    request = AsyncMock()
    monkeypatch.setattr(tracker, "_request_with_redirect_policy", request)
    await tracker._enrich_registry_push_times([candidate(tracker, tag)])
    request.assert_not_awaited()


@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_optional_provider_failure_is_not_a_fetch_failure(monkeypatch, status):
    tracker = DockerTracker("test", "library/nginx", token="synthetic:secret")
    request = AsyncMock(return_value=httpx.Response(status))
    monkeypatch.setattr(tracker, "_request_with_redirect_policy", request)
    releases = [candidate(tracker), candidate(tracker, "1.1.0")]
    await tracker._enrich_registry_push_times(releases)
    assert all(release.registry_pushed_at is None for release in releases)
    assert request.await_count == (2 if status == 500 else 1)


@pytest.mark.parametrize("registry", ["ghcr.io", "registry.test"])
async def test_unsupported_registry_does_not_probe_or_leak_credentials(monkeypatch, registry):
    tracker = DockerTracker("test", "team/app", registry=registry, token="synthetic:secret")
    request = AsyncMock()
    monkeypatch.setattr(tracker, "_request_with_redirect_policy", request)
    await tracker._enrich_registry_push_times([candidate(tracker)])
    request.assert_not_awaited()


async def test_request_count_and_total_deadline_are_bounded(monkeypatch):
    import releasetracker.trackers.docker as module

    tracker = DockerTracker("test", "library/nginx")
    request = AsyncMock(return_value=httpx.Response(404))
    monkeypatch.setattr(tracker, "_request_with_redirect_policy", request)
    releases = [candidate(tracker, f"1.0.{i}") for i in range(30)]
    await tracker._enrich_registry_push_times(releases)
    assert request.await_count == 10
    monkeypatch.setattr(module, "_REGISTRY_PUSH_TIME_BUDGET_SECONDS", 0.01)

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(tracker, "_request_with_redirect_policy", slow)
    async with asyncio.timeout(0.1):
        await tracker._enrich_registry_push_times(releases)
    assert all(release.registry_pushed_at is None for release in releases)


async def test_fetch_keeps_version_order_with_provider_time_and_zero_blobs(monkeypatch):
    tracker = DockerTracker("test", "library/nginx")
    original_client = httpx.AsyncClient
    calls = []

    def handle(request):
        calls.append((request.method, str(request.url)))
        assert "/blobs/" not in request.url.path
        if request.url.host == "hub.docker.com":
            assert "authorization" not in request.headers
            tag = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "name": tag,
                    "digest": DIGEST,
                    "tag_last_pushed": PUSHED if tag == "1.10.0" else "2026-10-12T12:34:56Z",
                },
            )
        if request.url.path.endswith("/tags/list"):
            return httpx.Response(200, json={"tags": ["1.2.0", "1.10.0"]})
        assert request.method == "HEAD"
        return httpx.Response(200, headers={"Docker-Content-Digest": DIGEST})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    tracker._get_bearer_token = AsyncMock(return_value=None)
    releases = await tracker.fetch_all(limit=2)
    assert [release.version for release in releases] == ["1.10.0", "1.2.0"]
    assert all(release.registry_pushed_at is not None for release in releases)
    assert [method for method, _ in calls] == ["GET", "HEAD", "HEAD", "GET", "GET"]


async def test_cross_origin_redirect_is_not_followed(monkeypatch):
    tracker = DockerTracker("test", "library/nginx", allow_registry_redirects=True)
    release = candidate(tracker)
    original_client = httpx.AsyncClient
    calls = []

    def handle(request):
        calls.append(str(request.url))
        assert request.url.host == "hub.docker.com"
        return httpx.Response(302, headers={"Location": "https://untrusted.test/tags"})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    await tracker._enrich_registry_push_times([release])
    assert len(calls) == 1 and release.registry_pushed_at is None
