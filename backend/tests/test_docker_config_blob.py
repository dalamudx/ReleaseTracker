"""Tests for the container `config blob` based real-time lookup.

The tracker now tries to upgrade a tag's placeholder `published_at` to the
image's actual build time (OCI config's `created` field) when the registry
policy allows it. This file covers:

    * `auto` mode + Docker Hub anonymous → config blob is NEVER fetched
    * `auto` mode + GHCR anonymous → config blob IS fetched
    * `prefer_real` mode + Docker Hub anonymous → config blob IS fetched
    * `first_observed` mode → config blob is NEVER fetched
    * Reproducible-build epoch timestamp → ignored, placeholder retained
    * 429 response → triggers per-registry cooldown so subsequent tags skip
    * Manifest index multi-arch → picks linux/amd64 sub-manifest
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

docker_path = (
    Path(__file__).resolve().parents[1] / "src" / "releasetracker" / "trackers" / "docker.py"
)
spec = importlib.util.spec_from_file_location("releasetracker.trackers.docker", docker_path)
assert spec is not None
assert spec.loader is not None
docker_module = importlib.util.module_from_spec(spec)
sys.modules["releasetracker.trackers.docker"] = docker_module
spec.loader.exec_module(docker_module)

DockerTracker = docker_module.DockerTracker


MANIFEST_DIGEST = "sha256:" + "a" * 64
CONFIG_DIGEST = "sha256:" + "b" * 64
REAL_CREATED = "2026-04-22T12:34:56Z"


def _build_single_arch_manifest() -> dict:
    return {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "schemaVersion": 2,
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": CONFIG_DIGEST,
            "size": 1234,
        },
        "layers": [],
    }


def _build_index_manifest(amd64_digest: str) -> dict:
    return {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "schemaVersion": 2,
        "manifests": [
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": amd64_digest,
                "platform": {"os": "linux", "architecture": "amd64"},
            },
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": "sha256:" + "c" * 64,
                "platform": {"os": "linux", "architecture": "arm64"},
            },
            {
                "mediaType": "application/vnd.in-toto+json",
                "digest": "sha256:" + "d" * 64,
                "platform": {"os": "unknown", "architecture": "unknown"},
            },
        ],
    }


def _build_config_blob(created: str | None = REAL_CREATED) -> dict:
    body: dict = {
        "architecture": "amd64",
        "os": "linux",
        "rootfs": {"type": "layers", "diff_ids": []},
    }
    if created is not None:
        body["created"] = created
    return body


class _FakeResponse(httpx.Response):
    """Minimal helper that wraps a dict body as JSON."""


def _json_response(body: dict, *, status_code: int = 200, headers: dict | None = None) -> httpx.Response:
    serialized = json.dumps(body).encode("utf-8")
    merged_headers = {"Content-Type": "application/vnd.oci.image.manifest.v1+json"}
    if headers:
        merged_headers.update(headers)
    return httpx.Response(status_code, content=serialized, headers=merged_headers)


def _head_response(digest: str, *, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers={
            "Docker-Content-Digest": digest,
            "Content-Type": "application/vnd.oci.image.manifest.v1+json",
        },
    )


@pytest.fixture(autouse=True)
def reset_registry_cooldowns():
    docker_module._registry_cooldowns.clear()
    yield
    docker_module._registry_cooldowns.clear()


def _patch_httpx_mock(monkeypatch, *, responses: list[tuple[str, str, httpx.Response]]):
    """Replay the provided sequence of (method, url_fragment, response) tuples.

    URL matching is done by `endswith`, so tests can assert on the path without
    re-typing the `https://registry-1.docker.io` prefix every time.
    """
    calls: list[tuple[str, str]] = []

    async def fake_request(self, method, url, **kwargs):
        calls.append((method.upper(), str(url)))
        for i, (expected_method, expected_url, response) in enumerate(responses):
            if expected_method.upper() == method.upper() and str(url).endswith(expected_url):
                responses.pop(i)
                return response
        raise AssertionError(f"unexpected HTTP call {method} {url}")

    async def fake_get(self, url, **kwargs):
        return await fake_request(self, "GET", url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    return calls


@pytest.mark.asyncio
async def test_config_blob_upgrades_published_at_for_ghcr_anonymous(monkeypatch):
    """GHCR anonymous + auto mode should fetch config blob and rewrite published_at."""
    tracker = DockerTracker(
        name="sample",
        image="owner/sample",
        registry="ghcr.io",
        published_at_mode="auto",
    )

    async def fake_bearer(self, client, scope):
        return None

    async def fake_tags(self, client, bearer_token):
        return ["v1.0.0"]

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_bearer)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_tags)

    _patch_httpx_mock(
        monkeypatch,
        responses=[
            # digest resolution (HEAD)
            ("HEAD", "/manifests/v1.0.0", _head_response(MANIFEST_DIGEST)),
            # config blob path: GET manifest → GET blob
            ("GET", "/manifests/v1.0.0", _json_response(_build_single_arch_manifest())),
            ("GET", f"/blobs/{CONFIG_DIGEST}", _json_response(_build_config_blob())),
        ],
    )

    releases = await tracker.fetch_all(limit=1)

    assert len(releases) == 1
    release = releases[0]
    assert release.commit_sha == MANIFEST_DIGEST
    # published_at was upgraded to the real creation time
    expected = datetime.fromisoformat(REAL_CREATED.replace("Z", "+00:00"))
    assert release.published_at == expected


@pytest.mark.asyncio
async def test_config_blob_skipped_for_docker_hub_anonymous(monkeypatch):
    """Anonymous docker.io + auto mode must NOT fetch config blob."""
    tracker = DockerTracker(
        name="sample",
        image="library/sample",
        registry="registry-1.docker.io",
        published_at_mode="auto",
    )

    async def fake_bearer(self, client, scope):
        return None

    async def fake_tags(self, client, bearer_token):
        return ["v1.0.0"]

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_bearer)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_tags)

    # Only the digest HEAD should be called; no GET manifest, no blob GET.
    calls = _patch_httpx_mock(
        monkeypatch,
        responses=[
            ("HEAD", "/manifests/v1.0.0", _head_response(MANIFEST_DIGEST)),
        ],
    )

    releases = await tracker.fetch_all(limit=1)
    assert len(releases) == 1
    assert releases[0].commit_sha == MANIFEST_DIGEST
    # published_at should still be the tracker's placeholder (near "now"),
    # not the real image build time.
    assert releases[0].published_at.year >= datetime.now().year

    # Verify no blob request was attempted.
    assert not any("/blobs/" in url for _method, url in calls)


@pytest.mark.asyncio
async def test_prefer_real_overrides_docker_hub_default(monkeypatch):
    """prefer_real mode forces config blob fetch even on docker.io anonymous."""
    tracker = DockerTracker(
        name="sample",
        image="library/sample",
        registry="registry-1.docker.io",
        published_at_mode="prefer_real",
    )

    async def fake_bearer(self, client, scope):
        return None

    async def fake_tags(self, client, bearer_token):
        return ["v1.0.0"]

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_bearer)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_tags)

    _patch_httpx_mock(
        monkeypatch,
        responses=[
            ("HEAD", "/manifests/v1.0.0", _head_response(MANIFEST_DIGEST)),
            ("GET", "/manifests/v1.0.0", _json_response(_build_single_arch_manifest())),
            ("GET", f"/blobs/{CONFIG_DIGEST}", _json_response(_build_config_blob())),
        ],
    )

    releases = await tracker.fetch_all(limit=1)
    expected = datetime.fromisoformat(REAL_CREATED.replace("Z", "+00:00"))
    assert releases[0].published_at == expected


@pytest.mark.asyncio
async def test_first_observed_mode_never_fetches_config_blob(monkeypatch):
    tracker = DockerTracker(
        name="sample",
        image="owner/sample",
        registry="ghcr.io",
        published_at_mode="first_observed",
    )

    async def fake_bearer(self, client, scope):
        return None

    async def fake_tags(self, client, bearer_token):
        return ["v1.0.0"]

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_bearer)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_tags)

    calls = _patch_httpx_mock(
        monkeypatch,
        responses=[
            ("HEAD", "/manifests/v1.0.0", _head_response(MANIFEST_DIGEST)),
        ],
    )

    await tracker.fetch_all(limit=1)
    assert not any("/blobs/" in url for _method, url in calls)


@pytest.mark.asyncio
async def test_reproducible_epoch_timestamp_is_rejected(monkeypatch):
    """Bazel/ko/nixpkgs often write 1970-01-01 as `created`; must be ignored."""
    tracker = DockerTracker(
        name="sample",
        image="owner/sample",
        registry="ghcr.io",
        published_at_mode="auto",
    )

    async def fake_bearer(self, client, scope):
        return None

    async def fake_tags(self, client, bearer_token):
        return ["v1.0.0"]

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_bearer)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_tags)

    _patch_httpx_mock(
        monkeypatch,
        responses=[
            ("HEAD", "/manifests/v1.0.0", _head_response(MANIFEST_DIGEST)),
            ("GET", "/manifests/v1.0.0", _json_response(_build_single_arch_manifest())),
            (
                "GET",
                f"/blobs/{CONFIG_DIGEST}",
                _json_response(_build_config_blob(created="1970-01-01T00:00:00Z")),
            ),
        ],
    )

    releases = await tracker.fetch_all(limit=1)
    # published_at should NOT be 1970 — placeholder retained.
    assert releases[0].published_at > datetime(2000, 1, 1)


@pytest.mark.asyncio
async def test_429_response_triggers_registry_cooldown(monkeypatch):
    """A 429 on the first tag should make us skip the blob fetch for subsequent tags."""
    tracker = DockerTracker(
        name="sample",
        image="owner/sample",
        registry="ghcr.io",
        published_at_mode="auto",
    )

    async def fake_bearer(self, client, scope):
        return None

    async def fake_tags(self, client, bearer_token):
        return ["v2.0.0", "v1.0.0"]

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_bearer)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_tags)

    calls = _patch_httpx_mock(
        monkeypatch,
        responses=[
            # tag v2.0.0 — digest OK, but the subsequent GET manifest returns 429
            ("HEAD", "/manifests/v2.0.0", _head_response(MANIFEST_DIGEST)),
            (
                "GET",
                "/manifests/v2.0.0",
                httpx.Response(429, headers={"Content-Type": "application/json"}),
            ),
            # tag v1.0.0 — only digest HEAD should happen, no further GETs
            ("HEAD", "/manifests/v1.0.0", _head_response(MANIFEST_DIGEST)),
        ],
    )

    await tracker.fetch_all(limit=2)
    # Verify we stopped trying to fetch blobs after the 429.
    assert docker_module._is_registry_cooling_down("ghcr.io")
    blob_calls = [url for method, url in calls if "/blobs/" in url]
    assert blob_calls == []


@pytest.mark.asyncio
async def test_multi_arch_index_picks_amd64_sub_manifest(monkeypatch):
    """Manifest index → select linux/amd64 manifest before reading config blob."""
    tracker = DockerTracker(
        name="sample",
        image="owner/sample",
        registry="ghcr.io",
        published_at_mode="auto",
    )

    async def fake_bearer(self, client, scope):
        return None

    async def fake_tags(self, client, bearer_token):
        return ["v1.0.0"]

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_bearer)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_tags)

    amd64_manifest_digest = "sha256:" + "e" * 64
    index_body = _build_index_manifest(amd64_manifest_digest)
    amd64_body = _build_single_arch_manifest()
    config_body = _build_config_blob()

    calls = _patch_httpx_mock(
        monkeypatch,
        responses=[
            # digest resolution
            (
                "HEAD",
                "/manifests/v1.0.0",
                _head_response(
                    MANIFEST_DIGEST,
                ),
            ),
            # config blob upgrade: GET index → GET amd64 manifest → GET config
            (
                "GET",
                "/manifests/v1.0.0",
                _json_response(
                    index_body,
                    headers={"Content-Type": "application/vnd.oci.image.index.v1+json"},
                ),
            ),
            (
                "GET",
                f"/manifests/{amd64_manifest_digest}",
                _json_response(amd64_body),
            ),
            ("GET", f"/blobs/{CONFIG_DIGEST}", _json_response(config_body)),
        ],
    )

    releases = await tracker.fetch_all(limit=1)
    expected = datetime.fromisoformat(REAL_CREATED.replace("Z", "+00:00"))
    assert releases[0].published_at == expected

    # Sanity: the amd64 sub-manifest was actually requested.
    assert any(amd64_manifest_digest in url for _method, url in calls)


@pytest.mark.asyncio
async def test_missing_config_in_blob_leaves_placeholder(monkeypatch):
    """Config blob missing `created` → we must silently fall back, not crash."""
    tracker = DockerTracker(
        name="sample",
        image="owner/sample",
        registry="ghcr.io",
        published_at_mode="auto",
    )

    async def fake_bearer(self, client, scope):
        return None

    async def fake_tags(self, client, bearer_token):
        return ["v1.0.0"]

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_bearer)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_tags)

    _patch_httpx_mock(
        monkeypatch,
        responses=[
            ("HEAD", "/manifests/v1.0.0", _head_response(MANIFEST_DIGEST)),
            ("GET", "/manifests/v1.0.0", _json_response(_build_single_arch_manifest())),
            ("GET", f"/blobs/{CONFIG_DIGEST}", _json_response(_build_config_blob(created=None))),
        ],
    )

    releases = await tracker.fetch_all(limit=1)
    # Placeholder preserved (tracker's local 'now' value, not epoch-ish).
    assert releases[0].published_at.year >= datetime.now(tz=timezone.utc).year
