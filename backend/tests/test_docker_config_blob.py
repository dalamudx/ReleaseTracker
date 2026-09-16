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


def _build_config_blob(
    created: str | None = REAL_CREATED, *, labels: dict[str, str] | None = None
) -> dict:
    body: dict = {
        "architecture": "amd64",
        "os": "linux",
        "rootfs": {"type": "layers", "diff_ids": []},
    }
    if created is not None:
        body["created"] = created
    if labels is not None:
        body["config"] = {"Labels": labels}
    return body


class _FakeResponse(httpx.Response):
    """Minimal helper that wraps a dict body as JSON."""


def _json_response(
    body: dict, *, status_code: int = 200, headers: dict | None = None
) -> httpx.Response:
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


def _patch_httpx_mock_with_headers(
    monkeypatch, *, responses: list[tuple[str, str, httpx.Response]]
):
    calls = []

    async def fake_request(self, method, url, **kwargs):
        call = {
            "method": method.upper(),
            "url": str(url),
            "headers": kwargs.get("headers"),
            "follow_redirects": kwargs.get("follow_redirects"),
        }
        calls.append(call)
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
            (
                "GET",
                f"/blobs/{CONFIG_DIGEST}",
                _json_response(
                    _build_config_blob(
                        labels={
                            "org.opencontainers.image.version": "v1.0.0-build123",
                            "org.opencontainers.image.revision": "build123",
                            "org.opencontainers.image.source": "https://example.com/owner/sample",
                        }
                    )
                ),
            ),
        ],
    )

    releases = await tracker.fetch_all(limit=1)

    assert len(releases) == 1
    release = releases[0]
    assert release.commit_sha == MANIFEST_DIGEST
    # published_at was upgraded to the real creation time
    expected = datetime.fromisoformat(REAL_CREATED.replace("Z", "+00:00"))
    assert release.published_at == expected
    assert release.published_at_source == "artifact_created"
    assert release.oci_version == "v1.0.0-build123"
    assert release.oci_revision == "build123"
    assert release.oci_source == "https://example.com/owner/sample"


def test_tag_without_created_time_marks_published_at_as_first_observed():
    tracker = DockerTracker(name="sample", image="owner/sample")

    release = tracker._tag_to_release("v1.0.0")

    assert release.published_at_source == "first_observed"


@pytest.mark.asyncio
async def test_config_blob_redirect_is_rejected_when_redirect_support_is_disabled():
    tracker = DockerTracker(
        name="sample",
        image="owner/sample",
        registry="ghcr.io",
        published_at_mode="auto",
    )
    blob_url = f"https://ghcr.io/v2/owner/sample/blobs/{CONFIG_DIGEST}"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            307,
            headers={"Location": "https://storage.example/blob"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="redirect"):
            await tracker._registry_config_blob_request(
                client,
                "GET",
                blob_url,
                headers={"Authorization": "Bearer registry-token"},
                timeout=tracker.timeout,
            )


@pytest.mark.asyncio
async def test_config_blob_follows_multihop_redirects_and_strips_credentials_irreversibly(
    monkeypatch,
):
    """Blob redirects may cross origins, but credentials never cross with them."""
    tracker = DockerTracker(
        name="sample",
        image="owner/sample",
        registry="ghcr.io",
        token="user:secret",
        published_at_mode="auto",
        allow_registry_redirects=True,
    )

    async def fake_bearer(self, client, scope):
        return "registry-bearer"

    async def fake_tags(self, client, bearer_token):
        return ["v1.0.0"]

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_bearer)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_tags)

    storage_url = "https://pkg-containers.githubusercontent.com/ghcr1/blobs/config?sig=redacted"
    storage_final_url = "https://pkg-containers.githubusercontent.com/ghcr1/blobs/final"
    registry_return_url = f"https://ghcr.io/v2/owner/sample/blobs/{CONFIG_DIGEST}?signed=1"
    calls = _patch_httpx_mock_with_headers(
        monkeypatch,
        responses=[
            ("HEAD", "/manifests/v1.0.0", _head_response(MANIFEST_DIGEST)),
            ("GET", "/manifests/v1.0.0", _json_response(_build_single_arch_manifest())),
            (
                "GET",
                f"/blobs/{CONFIG_DIGEST}",
                httpx.Response(
                    307, headers={"Location": f"/v2/owner/sample/blobs/{CONFIG_DIGEST}?signed=1"}
                ),
            ),
            ("GET", registry_return_url, httpx.Response(307, headers={"Location": storage_url})),
            ("GET", storage_url, httpx.Response(307, headers={"Location": "final"})),
            ("GET", storage_final_url, _json_response(_build_config_blob())),
        ],
    )

    releases = await tracker.fetch_all(limit=1)

    expected = datetime.fromisoformat(REAL_CREATED.replace("Z", "+00:00"))
    assert releases[0].published_at == expected
    registry_blob_calls = [
        call
        for call in calls
        if call["url"].startswith("https://ghcr.io/") and "/blobs/" in call["url"]
    ]
    assert registry_blob_calls
    assert all(
        call["headers"] == {"Authorization": "Bearer registry-bearer"}
        for call in registry_blob_calls
    )
    redirected_calls = [
        call
        for call in calls
        if call["url"].startswith("https://pkg-containers.githubusercontent.com/")
    ]
    assert {call["url"] for call in redirected_calls} == {storage_url, storage_final_url}
    for call in redirected_calls:
        redirected_headers = call["headers"] or {}
        lowered_headers = {name.lower() for name in redirected_headers}
        assert "authorization" not in lowered_headers
        assert "proxy-authorization" not in lowered_headers
        assert "cookie" not in lowered_headers
        assert call["follow_redirects"] is False


@pytest.mark.asyncio
async def test_config_blob_cross_origin_redirect_does_not_send_client_cookie_jar():
    tracker = DockerTracker(
        name="sample",
        image="org/image",
        registry="registry.example.com",
        published_at_mode="auto",
        allow_registry_redirects=True,
    )
    blob_url = f"https://registry.example.com/v2/org/image/blobs/{CONFIG_DIGEST}"
    storage_url = "https://storage.example.com/blob"
    seen_requests: list[tuple[str, str | None, str | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(
            (
                str(request.url),
                request.headers.get("authorization"),
                request.headers.get("cookie"),
            )
        )
        if request.url.host == "registry.example.com":
            return httpx.Response(
                307,
                headers={
                    "Location": storage_url,
                    "Set-Cookie": "registry_session=secret; Domain=.example.com; Path=/; Secure",
                },
                request=request,
            )
        return httpx.Response(200, json=_build_config_blob(), request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    ) as client:
        response = await tracker._registry_config_blob_request(
            client,
            "GET",
            blob_url,
            headers={"Authorization": "Bearer registry-token"},
            timeout=tracker.timeout,
        )

    assert response.status_code == 200
    assert seen_requests == [
        (blob_url, "Bearer registry-token", None),
        (storage_url, None, None),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("location", "target_response", "expected_target_calls"),
    [
        ("http://pkg-containers.githubusercontent.com/ghcr1/blobs/config", None, 0),
        (
            "https://pkg-containers.githubusercontent.com/ghcr1/blobs/config?sig=redacted",
            httpx.Response(
                307,
                headers={
                    "Location": "https://pkg-containers.githubusercontent.com/ghcr1/blobs/config?sig=redacted"
                },
            ),
            1,
        ),
        (
            "https://pkg-containers.githubusercontent.com/ghcr1/blobs/missing-location",
            httpx.Response(307),
            1,
        ),
    ],
)
async def test_config_blob_rejects_unsafe_or_looping_redirects_without_failing(
    monkeypatch,
    location,
    target_response,
    expected_target_calls,
):
    tracker = DockerTracker(
        name="sample",
        image="owner/sample",
        registry="ghcr.io",
        token="user:secret",
        published_at_mode="auto",
        allow_registry_redirects=True,
    )

    async def fake_bearer(self, client, scope):
        return "registry-bearer"

    async def fake_tags(self, client, bearer_token):
        return ["v1.0.0"]

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_bearer)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_tags)

    responses = [
        ("HEAD", "/manifests/v1.0.0", _head_response(MANIFEST_DIGEST)),
        ("GET", "/manifests/v1.0.0", _json_response(_build_single_arch_manifest())),
        (
            "GET",
            f"/blobs/{CONFIG_DIGEST}",
            httpx.Response(307, headers={"Location": location}),
        ),
    ]
    if target_response is not None:
        responses.append(("GET", location, target_response))
    calls = _patch_httpx_mock_with_headers(monkeypatch, responses=responses)

    releases = await tracker.fetch_all(limit=1)

    expected = datetime.fromisoformat(REAL_CREATED.replace("Z", "+00:00"))
    assert releases[0].commit_sha == MANIFEST_DIGEST
    assert releases[0].published_at != expected
    target_calls = [call for call in calls if call["url"] == location]
    assert len(target_calls) == expected_target_calls
    for call in target_calls:
        headers = call["headers"] or {}
        assert "Authorization" not in headers
        assert "authorization" not in {name.lower() for name in headers}
        assert call["follow_redirects"] is False


@pytest.mark.asyncio
async def test_config_blob_excessive_redirect_chain_falls_back_without_credentials(monkeypatch):
    tracker = DockerTracker(
        name="sample",
        image="owner/sample",
        registry="ghcr.io",
        token="user:secret",
        published_at_mode="auto",
        allow_registry_redirects=True,
    )

    async def fake_bearer(self, client, scope):
        return "registry-bearer"

    async def fake_tags(self, client, bearer_token):
        return ["v1.0.0"]

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_bearer)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_tags)

    storage_urls = [f"https://storage.example/config-hop-{index}" for index in range(6)]
    responses = [
        ("HEAD", "/manifests/v1.0.0", _head_response(MANIFEST_DIGEST)),
        ("GET", "/manifests/v1.0.0", _json_response(_build_single_arch_manifest())),
        (
            "GET",
            f"/blobs/{CONFIG_DIGEST}",
            httpx.Response(307, headers={"Location": storage_urls[0]}),
        ),
    ]
    responses.extend(
        (
            "GET",
            storage_urls[index],
            httpx.Response(307, headers={"Location": storage_urls[index + 1]}),
        )
        for index in range(5)
    )
    calls = _patch_httpx_mock_with_headers(monkeypatch, responses=responses)

    releases = await tracker.fetch_all(limit=1)

    expected = datetime.fromisoformat(REAL_CREATED.replace("Z", "+00:00"))
    assert releases[0].commit_sha == MANIFEST_DIGEST
    assert releases[0].published_at != expected
    storage_calls = [call for call in calls if call["url"].startswith("https://storage.example/")]
    assert [call["url"] for call in storage_calls] == storage_urls[:5]
    for call in storage_calls:
        headers = call["headers"] or {}
        assert "authorization" not in {name.lower() for name in headers}
        assert call["follow_redirects"] is False


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


@pytest.mark.asyncio
async def test_same_digest_aliases_fetch_config_metadata_once(monkeypatch):
    tracker = DockerTracker(
        name="sample",
        image="owner/sample",
        registry="ghcr.io",
        published_at_mode="prefer_real",
    )
    created = datetime.fromisoformat(REAL_CREATED.replace("Z", "+00:00"))
    metadata_calls: list[str] = []

    async def fake_bearer(self, client, scope):
        return None

    async def fake_tags(self, client, bearer_token):
        return ["1.0.0", "latest"]

    async def fake_digest(self, client, tag, bearer_token, scope):
        return MANIFEST_DIGEST, "application/vnd.oci.image.manifest.v1+json", None, bearer_token

    async def fake_created(self, client, tag, bearer_token, scope):
        metadata_calls.append(tag)
        return created, bearer_token

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_bearer)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_tags)
    monkeypatch.setattr(DockerTracker, "_resolve_manifest_digest", fake_digest)
    monkeypatch.setattr(DockerTracker, "_fetch_image_created", fake_created)

    releases = await tracker.fetch_all(limit=2)

    assert len(releases) == 2
    assert metadata_calls == ["latest"]
    assert {release.published_at for release in releases} == {created}
    assert {release.published_at_source for release in releases} == {"artifact_created"}


@pytest.mark.asyncio
async def test_persisted_digest_metadata_cache_skips_config_lookup(monkeypatch):
    tracker = DockerTracker(
        name="sample",
        image="owner/sample",
        registry="ghcr.io",
        published_at_mode="prefer_real",
    )
    created = datetime.fromisoformat(REAL_CREATED.replace("Z", "+00:00"))
    tracker.configure_incremental_fetch(
        alias_last_observed={},
        artifact_created_by_digest={MANIFEST_DIGEST: created},
        artifact_metadata_by_digest={
            MANIFEST_DIGEST: {
                "version": "1.0.0",
                "revision": "abc123",
                "source": "https://example.com/owner/sample",
            }
        },
    )

    async def fake_bearer(self, client, scope):
        return None

    async def fake_tags(self, client, bearer_token):
        return ["1.0.0", "latest"]

    async def fake_digest(self, client, tag, bearer_token, scope):
        return MANIFEST_DIGEST, "application/vnd.oci.image.manifest.v1+json", None, bearer_token

    async def fail_created(self, client, tag, bearer_token, scope):
        raise AssertionError("cached digest must not fetch config metadata")

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_bearer)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_tags)
    monkeypatch.setattr(DockerTracker, "_resolve_manifest_digest", fake_digest)
    monkeypatch.setattr(DockerTracker, "_fetch_image_created", fail_created)

    releases = await tracker.fetch_all(limit=2)

    assert len(releases) == 2
    assert {release.published_at for release in releases} == {created}
    assert {release.published_at_source for release in releases} == {"artifact_created"}
    assert {release.oci_version for release in releases} == {"1.0.0"}


@pytest.mark.asyncio
async def test_digest_change_prioritizes_oci_version_and_previous_digest_siblings(monkeypatch):
    tracker = DockerTracker(
        name="sample",
        image="owner/sample",
        registry="ghcr.io",
        published_at_mode="prefer_real",
    )
    exact_tag = "3.6.0-dev-0b0d27d3f61c"
    floating_tag = "3.6.0-dev"
    sibling_tag = "3.6-dev"
    old_floating_digest = "sha256:" + "1" * 64
    old_exact_digest = "sha256:" + "2" * 64
    new_digest = "sha256:" + "3" * 64
    observed_at = datetime(2026, 9, 16, tzinfo=timezone.utc)
    tracker.configure_incremental_fetch(
        alias_last_observed={
            floating_tag: observed_at.replace(day=15),
            exact_tag: observed_at,
            sibling_tag: observed_at,
        },
        alias_digest_by_name={
            floating_tag: old_floating_digest,
            exact_tag: old_exact_digest,
            sibling_tag: old_exact_digest,
        },
        artifact_created_by_digest={},
    )
    resolved_tags: list[str] = []
    metadata_tags: list[str] = []
    created = datetime.fromisoformat(REAL_CREATED.replace("Z", "+00:00"))

    async def fake_bearer(self, client, scope):
        return None

    async def fake_tags(self, client, bearer_token):
        return [floating_tag, exact_tag, sibling_tag, "old-build-a", "old-build-b"]

    async def fake_digest(self, client, tag, bearer_token, scope):
        resolved_tags.append(tag)
        digest = new_digest if tag in {floating_tag, exact_tag, sibling_tag} else MANIFEST_DIGEST
        return digest, "application/vnd.oci.image.manifest.v1+json", None, bearer_token

    async def fake_created(self, client, tag, bearer_token, scope):
        metadata_tags.append(tag)
        self._oci_metadata_by_tag[tag] = {
            "version": exact_tag,
            "revision": "0b0d27d3f61cbdabb1466549610a87f21978ba6a",
            "source": "https://git.example.com/canvas/nginx_frontend",
        }
        return created, bearer_token

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_bearer)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_tags)
    monkeypatch.setattr(DockerTracker, "_resolve_manifest_digest", fake_digest)
    monkeypatch.setattr(DockerTracker, "_fetch_image_created", fake_created)

    releases = await tracker.fetch_all(limit=3)

    assert resolved_tags == [floating_tag, exact_tag, sibling_tag]
    assert [release.tag_name for release in releases] == resolved_tags
    assert {release.commit_sha for release in releases} == {new_digest}
    assert {release.oci_version for release in releases} == {exact_tag}
    assert metadata_tags == [floating_tag]
