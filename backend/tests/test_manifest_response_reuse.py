"""Digest probing uses HEAD; manifest membership verification still uses GET."""
import hashlib
from unittest.mock import AsyncMock

import httpx
import pytest

from releasetracker.trackers.docker import DockerTracker

DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize("head_digest", [True, False])
async def test_digest_fallback_never_reads_config_or_labels(monkeypatch, head_digest):
    tracker = DockerTracker("probe", "team/app", registry="registry.test")
    tracker._get_bearer_token = AsyncMock(return_value=None)
    tracker._fetch_tags = AsyncMock(return_value=["1.0.0"])
    calls = []

    async def request(client, method, url, **kwargs):
        calls.append((method, url))
        assert url.endswith("/manifests/1.0.0")
        headers = {"Docker-Content-Digest": DIGEST} if head_digest or method == "GET" else {}
        return httpx.Response(200, headers=headers, json={
            "config": {"digest": "sha256:" + "c" * 64},
            "created": "2026-10-11T12:34:56Z",
        })

    monkeypatch.setattr(tracker, "_registry_request", request)
    release = (await tracker.fetch_all(limit=1))[0]
    assert release.commit_sha == DIGEST
    assert release.artifact_created_at is None and release.published_at is None
    assert [method for method, _ in calls] == (["HEAD"] if head_digest else ["HEAD", "GET"])


async def test_manifest_head_refreshes_authentication_without_blob_reads(monkeypatch):
    tracker = DockerTracker("probe", "team/app", registry="registry.test", token="synthetic:password")
    tracker._get_bearer_token = AsyncMock(side_effect=["old", "new"])
    tracker._fetch_tags = AsyncMock(return_value=["1.0.0"])
    calls = []

    async def request(client, method, url, **kwargs):
        auth = kwargs["headers"]["Authorization"]
        calls.append((method, auth))
        assert url.endswith("/manifests/1.0.0")
        return httpx.Response(401 if auth == "Bearer old" else 200, headers={"Docker-Content-Digest": DIGEST})

    monkeypatch.setattr(tracker, "_registry_request", request)
    release = (await tracker.fetch_all(limit=1))[0]
    assert release.commit_sha == DIGEST
    assert calls == [("HEAD", "Bearer old"), ("HEAD", "Bearer new")]
    assert tracker._get_bearer_token.await_count == 2


@pytest.mark.parametrize("tamper", [False, True])
async def test_running_manifest_membership_verification_survives_metadata_removal(monkeypatch, tamper):
    actual = "sha256:" + "b" * 64
    body = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": [{
        "digest": actual, "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "platform": {"os": "linux", "architecture": "amd64"},
    }]}
    response = httpx.Response(200, json=body)
    pinned = "sha256:" + hashlib.sha256(response.content).hexdigest()
    tracker = DockerTracker("probe", "team/app", registry="registry.test")
    tracker._get_bearer_token = AsyncMock(return_value=None)
    calls = []

    async def request(client, method, url, token, scope):
        calls.append((method, url))
        assert "/blobs/" not in url
        return (httpx.Response(200, json={}) if tamper else response), token

    monkeypatch.setattr(tracker, "_request_manifest", request)
    assert await tracker.verify_running_manifest(pinned, actual) == ("unknown" if tamper else "confirmed")
    assert calls == [("GET", f"https://registry.test/v2/team/app/manifests/{pinned}")]
