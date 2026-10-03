"""Local regression coverage for explicit auth failure and slow private-registry blobs."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from releasetracker.services.registry_errors import RegistryAuthenticationError
from releasetracker.services.task_queue import classify_fetch_error
from releasetracker.trackers.docker import DockerTracker


@pytest.mark.parametrize("status", [401, 403])
async def test_bad_configured_token_stops_at_auth_endpoint(monkeypatch, status):
    tracker = DockerTracker(
        "private",
        "team/app",
        registry="private.test",
        token="user:private-password",
        credential_name="registry-login",
    )
    response = httpx.Response(
        status, content=b"private-token", request=httpx.Request("GET", "https://private.test/auth")
    )
    monkeypatch.setattr(
        tracker,
        "_registry_request",
        AsyncMock(
            return_value=httpx.Response(
                401,
                headers={
                    "www-authenticate": 'Bearer realm="https://private.test/auth",service="registry"'
                },
            )
        ),
    )
    auth_request = AsyncMock(return_value=response)
    monkeypatch.setattr(tracker, "_request_with_redirect_policy", auth_request)
    async with httpx.AsyncClient() as client:
        with pytest.raises(RegistryAuthenticationError) as caught:
            await tracker._get_bearer_token(client, "repository:team/app:pull")
    assert classify_fetch_error(caught.value).code == (
        "registry_authentication_failed" if status == 401 else "registry_access_denied"
    )
    assert classify_fetch_error(caught.value).retryable is False
    assert "private-password" not in str(caught.value)
    assert "private-token" not in str(caught.value)
    auth_request.assert_awaited_once()


async def test_anonymous_token_service_failure_still_allows_public_registry(monkeypatch):
    tracker = DockerTracker("public", "library/app")
    monkeypatch.setattr(
        tracker,
        "_request_with_redirect_policy",
        AsyncMock(
            return_value=httpx.Response(
                401, request=httpx.Request("GET", "https://auth.docker.io/token")
            )
        ),
    )
    async with httpx.AsyncClient() as client:
        assert await tracker._get_bearer_token(client, "repository:library/app:pull") is None


@pytest.mark.parametrize("stage", ["manifest", "blob", "tags"])
@pytest.mark.parametrize("status", [401, 403])
async def test_authenticated_registry_operations_do_not_swallow_rejection(
    monkeypatch, stage, status
):
    tracker = DockerTracker("private", "team/app", registry="private.test", token="user:bad")
    monkeypatch.setattr(tracker, "_get_bearer_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(
        tracker,
        "_registry_request",
        AsyncMock(
            return_value=httpx.Response(
                status, request=httpx.Request("GET", "https://private.test/v2/team/app")
            )
        ),
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(RegistryAuthenticationError):
            if stage == "manifest":
                await tracker._request_manifest(
                    client, "GET", "https://private.test/v2/team/app/manifests/1.0", "token", "pull"
                )
            elif stage == "blob":
                monkeypatch.setattr(
                    tracker,
                    "_request_manifest",
                    AsyncMock(
                        return_value=(
                            httpx.Response(200, json={"config": {"digest": "sha256:" + "a" * 64}}),
                            "token",
                        )
                    ),
                )
                monkeypatch.setattr(
                    tracker,
                    "_registry_config_blob_request",
                    AsyncMock(
                        return_value=httpx.Response(
                            status,
                            request=httpx.Request(
                                "GET", "https://private.test/v2/team/app/blobs/sha256:abc"
                            ),
                        )
                    ),
                )
                await tracker._fetch_image_created(client, "1.0", "token", "pull")
            else:
                await tracker.fetch_all()


@pytest.mark.parametrize("mode", ["auto", "prefer_real", "first_observed"])
async def test_slow_blob_has_total_deadline_and_skips_remaining_tags(monkeypatch, mode):
    import releasetracker.trackers.docker as module

    monkeypatch.setattr(module, "_CONFIG_BLOB_TIMEOUT_SECONDS", 0.02)
    tracker = module.DockerTracker(
        "private", "team/app", registry="private.test", max_tags=10, published_at_mode=mode
    )
    monkeypatch.setattr(tracker, "_get_bearer_token", AsyncMock(return_value=None))
    blobs = []

    async def request(client, method, url, **kwargs):
        if "/tags/list" in url:
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={"tags": [f"1.0.{i}" for i in range(10)]},
            )
        if "/blobs/" in url:
            blobs.append(url)
            assert kwargs["timeout"] == 0.02
            await asyncio.sleep(1)
        tag = url.rsplit("/", 1)[-1]
        digest = "sha256:" + format(int(tag.split(".")[-1]) + 1, "064x")
        return httpx.Response(
            200,
            headers={"Docker-Content-Digest": digest},
            json={
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {"digest": "sha256:" + "a" * 64},
                "layers": [],
            },
        )

    monkeypatch.setattr(tracker, "_registry_request", request)
    monkeypatch.setattr(tracker, "_registry_config_blob_request", request)
    async with asyncio.timeout(0.5):
        releases = await tracker.fetch_all()
    assert len(releases) == 10
    assert [release.version for release in releases] == [f"1.0.{i}" for i in range(9, -1, -1)]
    assert len(blobs) == (0 if mode == "first_observed" else 1)
    assert tracker._config_blob_timed_out is (mode != "first_observed")
    assert all(release.published_at_source != "artifact_created" for release in releases)


async def test_auth_failure_propagates_to_failed_task_without_fallback(storage, monkeypatch):
    from test_task_queue_integration import sources, run_one

    tracker, scheduler, adapters, handler, queue = await sources(storage, monkeypatch)
    adapters["container"].fetch_all = AsyncMock(side_effect=RegistryAuthenticationError(401))
    await scheduler.check_tracker_now_v2(tracker.name)
    task = await run_one(storage, queue)
    assert task["state"] == "failed"
    assert task["error_code"] == "registry_authentication_failed"
    assert task["attempts"] == 1
    assert adapters["container"].fallback_calls == 0
