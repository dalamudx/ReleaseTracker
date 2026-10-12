"""Provider dates are digest-bound, bounded, optional and never Config metadata."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from releasetracker.trackers.docker import DockerTracker
from releasetracker.trackers.registry_metadata import github_package_token, registry_date

DIGEST = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
DATE = "2026-10-11T13:15:24Z"
HTTP_DATE = "Sun, 11 Oct 2026 13:15:24 GMT"
EXPECTED = datetime(2026, 10, 11, 13, 15, 24, tzinfo=timezone.utc)


def candidate(tracker, tag="1.2.0", digest=DIGEST):
    release = tracker._tag_to_release(tag)
    release.commit_sha = digest
    return release


@pytest.mark.parametrize("value", [DATE, HTTP_DATE, EXPECTED.timestamp()])
def test_provider_date_formats(value):
    assert registry_date(value) == EXPECTED


@pytest.mark.parametrize("value", [None, True, False, "invalid", {}, float("inf"), -1])
def test_provider_date_invalid(value):
    assert registry_date(value) is None


@pytest.mark.parametrize("registry", ["registry.test", "reg.aoodc.com", "registry.gitlab.com"])
async def test_manifest_date_is_modified_not_push_or_build(monkeypatch, registry):
    tracker = DockerTracker("test", "team/app", registry=registry)
    original = httpx.AsyncClient
    calls = []

    def handle(request):
        calls.append(request.method)
        assert request.method == "HEAD" and "/blobs/" not in request.url.path
        return httpx.Response(
            200, headers={"Docker-Content-Digest": DIGEST, "Last-Modified": HTTP_DATE}
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw)
    )
    async with httpx.AsyncClient() as client:
        digest, _, error, _ = await tracker._resolve_manifest_digest(
            client, "1.2.0", None, "repository:team/app:pull"
        )
    assert digest == DIGEST and error is None
    release = candidate(tracker)
    await tracker._enrich_registry_push_times([release])
    assert release.registry_modified_at == EXPECTED
    assert release.registry_pushed_at is None and release.artifact_created_at is None
    changed = candidate(tracker, digest=OTHER)
    await tracker._enrich_registry_push_times([changed])
    assert changed.registry_modified_at is None
    assert calls == ["HEAD"]


@pytest.mark.parametrize(
    "override,valid", [({}, True), ({"manifest_digest": OTHER}, False), ({"name": "latest"}, False)]
)
async def test_quay_tag_identity_and_semantics(monkeypatch, override, valid):
    tracker = DockerTracker("test", "team/app", registry="quay.io", token="robot:secret")
    release = candidate(tracker)

    async def request(client, method, url, **kw):
        assert url.startswith("https://quay.io/api/v1/repository/team/app/tag/")
        assert "specificTag=1.2.0" in url
        assert kw["origin_url"] == "https://quay.io"
        return httpx.Response(
            200,
            json={
                "tags": [
                    {
                        "name": "1.2.0",
                        "manifest_digest": DIGEST,
                        "last_modified": HTTP_DATE,
                        "start_ts": EXPECTED.timestamp(),
                        **override,
                    }
                ]
            },
        )

    monkeypatch.setattr(tracker, "_request_with_redirect_policy", request)
    await tracker._enrich_registry_push_times([release])
    assert release.registry_modified_at == (EXPECTED if valid else None)
    assert release.registry_created_at == (EXPECTED if valid else None)
    assert release.registry_pushed_at is None


@pytest.mark.parametrize("owner_type,path", [("Organization", "orgs"), ("User", "users")])
async def test_ghcr_packages_created_time_and_fixed_first_party_auth(monkeypatch, owner_type, path):
    tracker = DockerTracker(
        "test", "owner/nested/app", registry="ghcr.io", token="owner:ghp_synthetic_test"
    )
    release = candidate(tracker)
    calls = []

    async def request(client, method, url, **kw):
        calls.append(url)
        assert url.startswith("https://api.github.com/")
        assert kw["headers"]["Authorization"] == "Bearer ghp_synthetic_test"
        assert kw["origin_url"] == "https://api.github.com"
        assert kw["allow_cross_origin_without_credentials"] is False
        if url.endswith("/users/owner"):
            return httpx.Response(200, json={"login": "owner", "type": owner_type})
        assert f"/{path}/owner/packages/container/nested%2Fapp/versions" in url
        return httpx.Response(
            200,
            json=[
                {
                    "name": DIGEST,
                    "created_at": DATE,
                    "updated_at": "2099-01-01T00:00:00Z",
                    "metadata": {"container": {"tags": ["1.2.0"]}},
                }
            ],
        )

    monkeypatch.setattr(tracker, "_request_with_redirect_policy", request)
    await tracker._enrich_registry_push_times([release])
    assert release.registry_created_at == EXPECTED
    assert release.registry_pushed_at is None and release.registry_modified_at is None
    assert len(calls) == 2


@pytest.mark.parametrize(
    "token", ["secret", "robot:password", "OCI-exchanged-bearer", None, "ghr_refresh"]
)
async def test_ghcr_does_not_transfer_arbitrary_registry_tokens(monkeypatch, token):
    tracker = DockerTracker("test", "owner/app", registry="ghcr.io", token=token)
    request = AsyncMock()
    monkeypatch.setattr(tracker, "_request_with_redirect_policy", request)
    await tracker._enrich_registry_push_times([candidate(tracker)])
    request.assert_not_awaited()
    assert github_package_token(token) is None


@pytest.mark.parametrize(
    "version",
    [
        {"name": OTHER, "created_at": DATE, "metadata": {"container": {"tags": ["1.2.0"]}}},
        {"name": DIGEST, "created_at": DATE, "metadata": {"container": {"tags": ["other"]}}},
        {"name": DIGEST, "updated_at": DATE, "metadata": {"container": {"tags": ["1.2.0"]}}},
        {"name": DIGEST, "created_at": DATE, "metadata": None},
    ],
)
async def test_ghcr_never_uses_wrong_artifact_or_updated_at(monkeypatch, version):
    tracker = DockerTracker("test", "owner/app", registry="ghcr.io", token="ghp_synthetic")
    request = AsyncMock(
        side_effect=[
            httpx.Response(200, json={"login": "owner", "type": "User"}),
            httpx.Response(200, json=[version]),
        ]
    )
    monkeypatch.setattr(tracker, "_request_with_redirect_policy", request)
    release = candidate(tracker)
    await tracker._enrich_registry_push_times([release])
    assert release.registry_created_at is None


async def test_ghcr_pagination_respects_shared_request_budget(monkeypatch):
    tracker = DockerTracker("test", "owner/app", registry="ghcr.io", token="ghp_synthetic")
    calls = []

    async def request(client, method, url, **kw):
        calls.append(url)
        if url.endswith("/users/owner"):
            return httpx.Response(200, json={"login": "owner", "type": "User"})
        return httpx.Response(200, json=[{"name": OTHER}] * 100)

    monkeypatch.setattr(tracker, "_request_with_redirect_policy", request)
    await tracker._enrich_registry_push_times([candidate(tracker)])
    assert len(calls) == 10 and calls[-1].endswith("page=9")


@pytest.mark.parametrize("valid", [True, False])
async def test_harbor_native_push_and_nested_repository(monkeypatch, valid):
    tracker = DockerTracker(
        "test", "project/team/app", registry="harbor.test", token="robot:secret"
    )
    tracker._registry_metadata_provider = "harbor"

    async def request(client, method, url, **kw):
        assert "/repositories/team%252Fapp/artifacts/sha256%3A" in url
        assert kw["origin_url"] == "https://harbor.test"
        assert kw["headers"]["Authorization"].startswith("Basic ")
        return httpx.Response(
            200,
            json={
                "digest": DIGEST if valid else OTHER,
                "push_time": DATE,
                "tags": [{"name": "1.2.0"}],
            },
        )

    monkeypatch.setattr(tracker, "_request_with_redirect_policy", request)
    release = candidate(tracker)
    await tracker._enrich_registry_push_times([release])
    assert release.registry_pushed_at == (EXPECTED if valid else None)


@pytest.mark.parametrize(
    "realm,provider",
    [
        ("/service/token", "harbor"),
        ("/v2/auth", "quay"),
        ("/artifactory/api/docker/cr/v2/token", None),
    ],
)
async def test_native_provider_discovery_uses_same_origin_challenge(monkeypatch, realm, provider):
    tracker = DockerTracker("test", "team/app", registry="registry.test")
    request = AsyncMock(
        side_effect=[
            httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": f'Bearer realm="https://registry.test{realm}",service="registry.test"'
                },
            ),
            httpx.Response(200, json={"token": "OCI-token"}),
        ]
    )
    monkeypatch.setattr(tracker, "_request_with_redirect_policy", request)
    async with httpx.AsyncClient() as client:
        await tracker._get_bearer_token(client, "repository:team/app:pull")
    assert tracker._registry_metadata_provider == provider


@pytest.mark.parametrize(
    "registry,token", [("ghcr.io", "ghp_synthetic"), ("quay.io", "oauth_secret")]
)
async def test_native_api_credentials_never_follow_cross_origin_redirect(
    monkeypatch, registry, token
):
    tracker = DockerTracker(
        "test", "owner/app", registry=registry, token=token, allow_registry_redirects=True
    )
    original = httpx.AsyncClient
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://untrusted.test/stolen"})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw)
    )
    await tracker._enrich_registry_push_times([candidate(tracker)])
    assert len(calls) == 1 and all(request.url.host != "untrusted.test" for request in calls)


async def test_native_api_timeout_preserves_verified_manifest_date(monkeypatch):
    import releasetracker.trackers.docker as module

    tracker = DockerTracker("test", "team/app", registry="quay.io")
    release = candidate(tracker)
    tracker._manifest_modified[(release.tag_name, DIGEST)] = EXPECTED

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(tracker, "_request_with_redirect_policy", slow)
    monkeypatch.setattr(module, "_REGISTRY_PUSH_TIME_BUDGET_SECONDS", 0.01)
    async with asyncio.timeout(0.1):
        await tracker._enrich_registry_push_times([release])
    assert release.registry_modified_at == EXPECTED


async def test_quay_metadata_reuses_pull_credential_on_same_origin(monkeypatch):
    tracker = DockerTracker("test", "owner/app", registry="quay.io", token="robot:pull_secret")
    request = AsyncMock(return_value=httpx.Response(200, json={"tags": []}))
    monkeypatch.setattr(tracker, "_request_with_redirect_policy", request)
    await tracker._enrich_registry_push_times([candidate(tracker)])
    assert request.call_args.kwargs["headers"]["Authorization"].startswith("Basic ")


def test_retired_api_credential_key_is_dropped_from_container_sources():
    from releasetracker.models import TrackerSource

    source = TrackerSource(
        source_key="image",
        source_type="container",
        source_config={
            "registry": "ghcr.io",
            "image": "owner/app",
            "metadata_credential_name": "native-api",
        },
    )
    assert source.source_config == {"registry": "ghcr.io", "image": "owner/app"}


def test_artifact_summary_keeps_registry_date_semantics():
    from releasetracker.routers.trackers import _build_artifact_summaries

    rows = _build_artifact_summaries(
        [
            {
                "source_type": "container",
                "source_key": "image",
                "digest": DIGEST,
                "registry_created_at": DATE,
                "registry_modified_at": DATE,
            }
        ]
    )
    assert rows[0]["registry_created_at"] == DATE and rows[0]["registry_modified_at"] == DATE
    assert rows[0]["registry_pushed_at"] is None


async def test_existing_head_dates_are_not_limited_by_metadata_request_budget(monkeypatch):
    tracker = DockerTracker("test", "owner/app", registry="registry.test")
    releases = [candidate(tracker, tag=f"1.2.{index}") for index in range(12)]
    for release in releases:
        tracker._manifest_modified[(release.tag_name, DIGEST)] = EXPECTED
    request = AsyncMock()
    monkeypatch.setattr(tracker, "_request_with_redirect_policy", request)
    await tracker._enrich_registry_push_times(releases)
    assert all(release.registry_modified_at == EXPECTED for release in releases)
    request.assert_not_awaited()
