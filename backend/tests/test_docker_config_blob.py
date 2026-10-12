"""Container fetching never reads Config blobs, build timestamps, or OCI labels."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from releasetracker.models import TrackerSource
from releasetracker.trackers.docker import DockerTracker

DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize("mode", [None, "auto", "prefer_real", "first_observed", "future_mode"])
@pytest.mark.parametrize(
    "registry,token",
    [("registry-1.docker.io", None), ("ghcr.io", None), ("registry.test", "synthetic:password")],
)
async def test_all_legacy_modes_only_fetch_tags_and_manifest_digests(
    monkeypatch, mode, registry, token
):
    legacy = {"published_at_mode": mode} if mode is not None else {}
    tracker = DockerTracker("probe", "team/app", registry=registry, token=token, **legacy)
    tracker._get_bearer_token = AsyncMock(return_value="synthetic-bearer")
    # Provider push-time retrieval has separate identity/credential regression tests.
    tracker._enrich_registry_push_times = AsyncMock()
    calls = []

    async def request(client, method, url, **kwargs):
        calls.append((method, url))
        assert "/blobs/" not in url
        if "/tags/list" in url:
            return httpx.Response(
                200, json={"tags": ["1.2.0", "1.10.0"]}, request=httpx.Request(method, url)
            )
        assert method == "HEAD"
        return httpx.Response(200, headers={"Docker-Content-Digest": DIGEST})

    monkeypatch.setattr(tracker, "_registry_request", request)
    releases = await tracker.fetch_all(limit=2)
    assert [r.tag_name for r in releases] == ["1.10.0", "1.2.0"]
    assert [method for method, _ in calls] == ["GET", "HEAD", "HEAD"]
    assert all(r.commit_sha == DIGEST for r in releases)
    assert all(r.published_at is None and r.artifact_created_at is None for r in releases)
    assert all(
        r.oci_version is None and r.oci_revision is None and r.oci_source is None for r in releases
    )
    assert not hasattr(tracker, "_fetch_image_created")


@pytest.mark.parametrize("mode", ["auto", "prefer_real", "first_observed"])
def test_old_source_config_is_accepted_but_not_exposed(mode):
    source = TrackerSource(
        source_key="registry",
        source_type="container",
        source_config={
            "image": "team/app",
            "registry": "registry.test",
            "published_at_mode": mode,
        },
    )
    assert source.model_dump()["source_config"] == {
        "image": "team/app",
        "registry": "registry.test",
    }


async def test_digest_change_still_prioritizes_previous_artifact_siblings(monkeypatch):
    tracker = DockerTracker("probe", "team/app", registry="registry.test")
    observed = datetime(2026, 10, 11, tzinfo=timezone.utc)
    tracker.configure_incremental_fetch(
        alias_last_observed={
            "latest": observed.replace(day=10),
            "1.0.0": observed,
            "1.0": observed,
        },
        alias_digest_by_name={"latest": DIGEST, "1.0.0": DIGEST, "1.0": DIGEST},
    )
    tracker._get_bearer_token = AsyncMock(return_value=None)
    tracker._fetch_tags = AsyncMock(return_value=["latest", "1.0.0", "1.0"])
    seen = []

    async def resolve(client, tag, bearer, scope):
        seen.append(tag)
        return "sha256:" + "b" * 64, None, None, bearer

    monkeypatch.setattr(tracker, "_resolve_manifest_digest", resolve)
    releases = await tracker.fetch_all(limit=3)
    assert set(seen) == {"latest", "1.0.0", "1.0"}
    assert len(releases) == 3
    assert all(r.oci_version is None for r in releases)
