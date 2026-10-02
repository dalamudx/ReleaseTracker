import hashlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from releasetracker.trackers.docker import DockerTracker


def encode(document):
    body = json.dumps(document, separators=(",", ":")).encode()
    return body, "sha256:" + hashlib.sha256(body).hexdigest()


@pytest.fixture
def tracker(monkeypatch):
    monkeypatch.setattr(DockerTracker, "_get_bearer_token", AsyncMock(return_value=None))
    return DockerTracker(name="audit", image="team/app", registry="registry.example")


@pytest.mark.asyncio
async def test_single_manifest_and_digest_content_authentication(monkeypatch, tracker):
    body, digest = encode(
        {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json"}
    )
    request = AsyncMock(return_value=(httpx.Response(200, content=body), None))
    monkeypatch.setattr(tracker, "_request_manifest", request)
    assert await tracker.verify_running_manifest(digest, digest) == "confirmed"
    request.assert_not_awaited()
    assert await tracker.verify_running_manifest(digest, "sha256:" + "b" * 64) == "superseded"
    assert await tracker.verify_running_manifest("sha256:" + "f" * 64, digest) == "unknown"


@pytest.mark.asyncio
async def test_nested_index_membership_not_config_or_attestation(monkeypatch, tracker):
    actual = "sha256:" + "a" * 64
    nested, nested_digest = encode(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "digest": actual,
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "platform": {"os": "linux", "architecture": "arm64"},
                }
            ],
        }
    )
    index, digest = encode(
        {
            "schemaVersion": 2,
            "manifests": [
                {"digest": nested_digest, "mediaType": "application/vnd.oci.image.index.v1+json"},
                {
                    "digest": "sha256:" + "c" * 64,
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "annotations": {"vnd.docker.reference.type": "attestation-manifest"},
                },
            ],
        }
    )
    bodies = {digest: index, nested_digest: nested}

    async def request(client, method, url, token, scope):
        return httpx.Response(200, content=bodies[url.rsplit("/", 1)[-1]]), token

    monkeypatch.setattr(tracker, "_request_manifest", request)
    assert await tracker.verify_running_manifest(digest, actual) == "confirmed"
    assert await tracker.verify_running_manifest(digest, "sha256:" + "c" * 64) == "superseded"


@pytest.mark.asyncio
async def test_unavailable_or_unsupported_evidence_stays_unknown(monkeypatch, tracker):
    body = b"not-json"
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    request = AsyncMock(return_value=(httpx.Response(403), None))
    monkeypatch.setattr(tracker, "_request_manifest", request)
    assert await tracker.verify_running_manifest(digest, "sha256:" + "a" * 64) == "unknown"
    request.return_value = httpx.Response(200, content=body), None
    assert await tracker.verify_running_manifest(digest, "sha256:" + "a" * 64) == "unknown"
    body, digest = encode({"schemaVersion": 2, "mediaType": "unsupported"})
    request.return_value = httpx.Response(200, content=body), None
    assert await tracker.verify_running_manifest(digest, "sha256:" + "a" * 64) == "unknown"


@pytest.mark.asyncio
async def test_manifest_cardinality_is_bounded(monkeypatch, tracker):
    body, digest = encode(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "digest": "sha256:" + f"{i:064x}",
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "platform": {"os": "linux", "architecture": "amd64"},
                }
                for i in range(101)
            ],
        }
    )
    request = AsyncMock(return_value=(httpx.Response(200, content=body), None))
    monkeypatch.setattr(tracker, "_request_manifest", request)
    assert await tracker.verify_running_manifest(digest, "sha256:" + "a" * 64) == "unknown"
    assert request.await_count == 1
