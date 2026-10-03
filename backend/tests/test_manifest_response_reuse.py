"""A new artifact resolves digest and config metadata from one manifest GET."""

import hashlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from releasetracker.trackers import docker as module


async def exercise(
    monkeypatch, *, mode="auto", multiarch=False, second_fetch=False, fail_status=None
):
    module._registry_cooldowns.clear()
    tracker = module.DockerTracker(
        "probe",
        "team/app",
        registry="registry.test",
        published_at_mode=mode,
        token="user:synthetic" if fail_status else None,
    )
    tracker._get_bearer_token = AsyncMock(return_value="synthetic-bearer")
    tracker._fetch_tags = AsyncMock(return_value=["1.0.0"])
    config_digest = "sha256:" + "c" * 64
    child = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": config_digest},
    }
    child_digest = "sha256:" + hashlib.sha256(json.dumps(child).encode()).hexdigest()
    root = (
        {
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "digest": child_digest,
                    "mediaType": child["mediaType"],
                    "platform": {"os": "linux", "architecture": "amd64"},
                }
            ],
        }
        if multiarch
        else child
    )
    digest = "sha256:" + hashlib.sha256(json.dumps(root).encode()).hexdigest()
    calls = []

    async def request(client, method, url, **kwargs):
        calls.append((method, url))
        if "/blobs/" in url:
            return httpx.Response(
                200,
                json={
                    "created": "2026-04-22T12:34:56Z",
                    "config": {"Labels": {"org.opencontainers.image.revision": "synthetic-build"}},
                },
            )
        body = child if url.endswith(child_digest) else root
        if fail_status and method == "GET" and "/manifests/" in url:
            return httpx.Response(fail_status, request=httpx.Request(method, url))
        return httpx.Response(
            200,
            json=body,
            headers={"Docker-Content-Digest": digest, "Content-Type": body["mediaType"]},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(tracker, "_registry_request", request)
    monkeypatch.setattr(tracker, "_registry_config_blob_request", request)
    if fail_status:
        from releasetracker.services.registry_errors import RegistryAuthenticationError

        with pytest.raises(RegistryAuthenticationError):
            await tracker.fetch_all(limit=1)
        assert not any("/blobs/" in url for _, url in calls)
        return
    releases = await tracker.fetch_all(limit=1)
    assert releases[0].commit_sha == digest
    manifests = [(method, url) for method, url in calls if "/manifests/" in url]
    if mode == "first_observed":
        assert len(manifests) == 1 and manifests[0][0] == "HEAD"
        assert not any("/blobs/" in url for _, url in calls)
    else:
        assert len(manifests) == (
            2 if multiarch else 1
        ), "reuse manifest body instead of HEAD + repeated GET"
        assert all(method == "GET" for method, _ in manifests)
        assert releases[0].published_at_source == "artifact_created"
        assert releases[0].oci_revision == "synthetic-build"
    if second_fetch:
        calls.clear()
        await tracker.fetch_all(limit=1)
        assert any(
            "/manifests/" in url for _, url in calls
        ), "mutable tag must be re-resolved each round"
    module._registry_cooldowns.clear()


@pytest.mark.parametrize("mode", ["auto", "prefer_real", "first_observed"])
@pytest.mark.parametrize("multiarch", [False, True])
async def test_single_response_supplies_digest_and_metadata(monkeypatch, mode, multiarch):
    await exercise(monkeypatch, mode=mode, multiarch=multiarch)


async def test_response_reuse_never_crosses_fetch_rounds(monkeypatch):
    await exercise(monkeypatch, second_fetch=True)


@pytest.mark.parametrize("status", [401, 403])
async def test_new_manifest_path_keeps_configured_auth_rejections_strict(monkeypatch, status):
    await exercise(monkeypatch, fail_status=status)


@pytest.mark.parametrize("problem", ["missing_header", "timeout", "http_500", "http_429"])
async def test_optional_body_probe_falls_back_without_mixing_mutable_tag_versions(
    monkeypatch, problem
):
    module._registry_cooldowns.clear()
    tracker = module.DockerTracker(
        "probe", "team/app", registry="registry.test", published_at_mode="auto"
    )
    tracker._get_bearer_token = AsyncMock(return_value=None)
    tracker._fetch_tags = AsyncMock(return_value=["1.0.0"])
    digest = "sha256:" + "b" * 64
    first = True
    calls = []

    async def request(client, method, url, **kwargs):
        nonlocal first
        calls.append((method, url))
        if "/blobs/" in url:
            assert url.endswith(
                "sha256:" + "2" * 64
            ), "old GET body cannot be combined with newer HEAD digest"
            return httpx.Response(200, json={"created": "2026-04-22T12:34:56Z"})
        if method == "GET" and first:
            first = False
            if problem == "timeout":
                raise httpx.ReadTimeout("synthetic read budget", request=httpx.Request(method, url))
            if problem.startswith("http_"):
                return httpx.Response(int(problem.removeprefix("http_")))
            return httpx.Response(200, json={"config": {"digest": "sha256:" + "1" * 64}})
        if method == "HEAD":
            return httpx.Response(200, headers={"Docker-Content-Digest": digest})
        return httpx.Response(
            200,
            json={"config": {"digest": "sha256:" + "2" * 64}},
            headers={"Docker-Content-Digest": digest},
        )

    monkeypatch.setattr(tracker, "_registry_request", request)
    monkeypatch.setattr(tracker, "_registry_config_blob_request", request)
    releases = await tracker.fetch_all(limit=1)
    assert releases[0].commit_sha == digest
    assert any(method == "HEAD" for method, _ in calls)
    if problem == "http_429":
        assert module._is_registry_cooling_down("registry.test")
        assert releases[0].published_at_source == "first_observed"
        assert not any("/blobs/" in url for _, url in calls)
    else:
        assert releases[0].published_at_source == "artifact_created"
    module._registry_cooldowns.clear()


async def test_persisted_metadata_keeps_lightweight_head_and_detects_new_digest(monkeypatch):
    tracker = module.DockerTracker(
        "warm", "team/app", registry="registry.test", published_at_mode="auto"
    )
    old, new = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    tracker.configure_incremental_fetch(
        alias_last_observed={},
        alias_digest_by_name={"1.0.0": old},
        artifact_created_by_digest={},
        artifact_metadata_by_digest={old: {"revision": "old-build"}},
    )
    tracker._get_bearer_token = AsyncMock(return_value=None)
    tracker._fetch_tags = AsyncMock(return_value=["1.0.0"])
    current = old
    calls = []

    async def request(client, method, url, **kwargs):
        calls.append((method, url))
        if "/blobs/" in url:
            return httpx.Response(
                200, json={"config": {"Labels": {"org.opencontainers.image.revision": "new-build"}}}
            )
        return httpx.Response(
            200,
            headers={"Docker-Content-Digest": current},
            json={"config": {"digest": "sha256:" + "c" * 64}},
        )

    monkeypatch.setattr(tracker, "_registry_request", request)
    monkeypatch.setattr(tracker, "_registry_config_blob_request", request)
    first = await tracker.fetch_all(limit=1)
    assert calls == [("HEAD", "https://registry.test/v2/team/app/manifests/1.0.0")]
    assert first[0].oci_revision == "old-build"
    current = new
    calls.clear()
    second = await tracker.fetch_all(limit=1)
    assert second[0].commit_sha == new
    assert second[0].oci_revision == "new-build"
    assert [method for method, url in calls if "/manifests/" in url] == ["HEAD", "GET"]


@pytest.mark.parametrize("multiarch", [False, True])
async def test_ten_tag_request_reduction_preserves_results_against_head_baseline(
    monkeypatch, multiarch
):
    async def run(reuse):
        tracker = module.DockerTracker(
            "comparison", "team/app", registry="registry.test", published_at_mode="auto"
        )
        tracker._get_bearer_token = AsyncMock(return_value=None)
        tracker._fetch_tags = AsyncMock(return_value=[f"1.0.{i}" for i in range(10)])
        calls, children = [], {}
        if not reuse:
            resolver = tracker._resolve_manifest_digest

            async def baseline(client, tag, bearer_token, scope, **_options):
                return await resolver(client, tag, bearer_token, scope)

            tracker._resolve_manifest_digest = baseline

        async def request(client, method, url, **kwargs):
            calls.append((method, url))
            if "/blobs/" in url:
                return httpx.Response(
                    200,
                    json={
                        "created": "2026-04-22T12:34:56Z",
                        "config": {"Labels": {"org.opencontainers.image.revision": "test-build"}},
                    },
                )
            tag = url.rsplit("/", 1)[-1]
            if tag in children:
                body = children[tag]
            else:
                child = {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "config": {"digest": "sha256:" + hashlib.sha256(tag.encode()).hexdigest()},
                }
                child_response = httpx.Response(200, json=child)
                child_digest = "sha256:" + hashlib.sha256(child_response.content).hexdigest()
                children[child_digest] = child
                body = (
                    {
                        "mediaType": "application/vnd.oci.image.index.v1+json",
                        "manifests": [
                            {
                                "mediaType": child["mediaType"],
                                "digest": child_digest,
                                "platform": {"os": "linux", "architecture": "amd64"},
                            }
                        ],
                    }
                    if multiarch
                    else child
                )
            response = httpx.Response(200, json=body, headers={"Content-Type": body["mediaType"]})
            response.headers["Docker-Content-Digest"] = (
                "sha256:" + hashlib.sha256(response.content).hexdigest()
            )
            return response

        monkeypatch.setattr(tracker, "_registry_request", request)
        monkeypatch.setattr(tracker, "_registry_config_blob_request", request)
        releases = await tracker.fetch_all(limit=10)
        snapshot = [
            (r.tag_name, r.commit_sha, r.published_at, r.published_at_source, r.oci_revision)
            for r in releases
        ]
        manifests = [(method, url) for method, url in calls if "/manifests/" in url]
        blobs = [(method, url) for method, url in calls if "/blobs/" in url]
        return snapshot, manifests, blobs

    baseline, optimized = await run(False), await run(True)
    assert baseline[0] == optimized[0]
    assert len(baseline[1]) == (30 if multiarch else 20)
    assert len(optimized[1]) == (20 if multiarch else 10)
    assert len(baseline[1]) - len(optimized[1]) == 10
    assert len(baseline[2]) == len(optimized[2]) == 10


async def test_token_refreshed_by_initial_get_is_used_for_blob(monkeypatch):
    tracker = module.DockerTracker(
        "probe", "team/app", registry="registry.test", token="user:synthetic"
    )
    tracker._get_bearer_token = AsyncMock(side_effect=["old-token", "new-token"])
    tracker._fetch_tags = AsyncMock(return_value=["1.0.0"])
    digest = "sha256:" + "a" * 64
    calls = []

    async def request(client, method, url, **kwargs):
        auth = kwargs["headers"].get("Authorization")
        calls.append((method, url, auth))
        if auth == "Bearer old-token":
            return httpx.Response(401)
        assert auth == "Bearer new-token"
        if "/blobs/" in url:
            return httpx.Response(200, json={"created": "2026-04-22T12:34:56Z"})
        return httpx.Response(
            200,
            headers={"Docker-Content-Digest": digest},
            json={"config": {"digest": "sha256:" + "c" * 64}},
        )

    monkeypatch.setattr(tracker, "_registry_request", request)
    monkeypatch.setattr(tracker, "_registry_config_blob_request", request)
    releases = await tracker.fetch_all(limit=1)
    assert releases[0].published_at_source == "artifact_created"
    assert [entry[0] for entry in calls] == ["GET", "GET", "GET"]
    assert tracker._get_bearer_token.await_count == 2
    assert calls[-1][2] == "Bearer new-token"
