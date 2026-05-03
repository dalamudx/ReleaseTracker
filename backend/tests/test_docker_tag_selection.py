import importlib.util
import logging
import sys
from pathlib import Path

import httpx
import pytest

from releasetracker.config import Channel

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
_apply_semver_published_at = docker_module._apply_semver_published_at
_sort_tags = docker_module._sort_tags


def _build_filtered_releases(tracker: DockerTracker, tags: list[str]):
    releases = [tracker._tag_to_release(tag) for tag in _sort_tags(tags)]

    releases = [release for release in releases if tracker._should_include(release)]
    _apply_semver_published_at(releases)
    return releases


def test_docker_semver_beats_older_after_exclude_filter():
    tracker = DockerTracker(
        name="docker-test",
        image="library/nginx",
        filter={"exclude_pattern": "-(arm64)"},
    )
    tags = ["v0.9.18", "v0.9.22", "v0.9.22-arm64", "v0.9.18-arm64"]
    releases = _build_filtered_releases(tracker, tags)
    latest = max(releases, key=lambda rel: rel.published_at)

    assert latest.tag_name == "v0.9.22"


def test_docker_exclude_suffix_keeps_base_tag():
    tracker = DockerTracker(
        name="docker-test",
        image="library/nginx",
        filter={"exclude_pattern": "-(arm64)$"},
    )
    tags = ["v1.2.3", "v1.2.3-arm64"]
    releases = _build_filtered_releases(tracker, tags)
    latest = max(releases, key=lambda rel: rel.published_at)

    assert latest.tag_name == "v1.2.3"


def test_docker_filter_does_not_promote_older_semver():
    tracker = DockerTracker(
        name="docker-test",
        image="library/nginx",
        filter={"exclude_pattern": "-(arm64)$"},
    )
    tags = [
        "v1.0.0",
        "v1.1.0",
        "v1.1.0-arm64",
        "v0.9.9",
    ]
    releases = _build_filtered_releases(tracker, tags)
    latest = max(releases, key=lambda rel: rel.published_at)

    assert latest.tag_name == "v1.1.0"


def test_ubuntu_style_two_part_tags_rank_by_version():
    tracker = DockerTracker(name="ubuntu-test", image="library/ubuntu")
    tags = ["14.04.5", "22.04", "24.04", "latest"]
    releases = _build_filtered_releases(tracker, tags)
    version_releases = [r for r in releases if r.tag_name != "latest"]
    winner = max(version_releases, key=lambda r: r.published_at)

    assert winner.tag_name == "24.04"


def test_two_part_tag_not_demoted_below_older_three_part():
    tracker = DockerTracker(name="ubuntu-test", image="library/ubuntu")
    tags = ["14.04.5", "22.04", "24.04"]
    releases = _build_filtered_releases(tracker, tags)
    ranked = sorted(releases, key=lambda r: r.published_at, reverse=True)
    tag_order = [r.tag_name for r in ranked]

    assert tag_order == ["24.04", "22.04", "14.04.5"]


def test_ubuntu_style_filter_interaction_numeric_winner_survives():
    tracker = DockerTracker(
        name="ubuntu-test",
        image="library/ubuntu",
        filter={"exclude_pattern": "-amd64$"},
    )
    tags = ["14.04.5", "22.04", "24.04", "24.04-amd64", "22.04-amd64"]
    releases = _build_filtered_releases(tracker, tags)
    winner = max(releases, key=lambda r: r.published_at)

    assert winner.tag_name == "24.04"


def test_latest_tag_keeps_raw_tag_version():
    tracker = DockerTracker(name="ubuntu-test", image="library/ubuntu")
    releases = _build_filtered_releases(tracker, ["latest", "24.04", "24.04.1", "22.04.3"])

    latest_release = next(release for release in releases if release.tag_name == "latest")

    assert latest_release.version == "latest"


def test_major_minor_shorthand_keeps_raw_tag_version():
    tracker = DockerTracker(name="ubuntu-test", image="library/ubuntu")
    releases = _build_filtered_releases(tracker, ["24.04", "24.04.1", "24.03.9"])

    shorthand_release = next(release for release in releases if release.tag_name == "24.04")
    patch_release = next(release for release in releases if release.tag_name == "24.04.1")

    assert shorthand_release.version == "24.04"
    assert patch_release.version == "24.04.1"


def test_24_0_keeps_raw_tag_version():
    tracker = DockerTracker(name="ubuntu-test", image="library/ubuntu")
    releases = _build_filtered_releases(tracker, ["latest", "24.0", "24.04", "24.04.1"])

    short_minor_zero = next(release for release in releases if release.tag_name == "24.0")
    newest_patch = next(release for release in releases if release.tag_name == "24.04.1")

    assert short_minor_zero.version == "24.0"
    assert newest_patch.version == "24.04.1"


def test_stable_and_lts_tags_remain_independent_versions():
    tracker = DockerTracker(name="ubuntu-test", image="library/ubuntu")
    releases = _build_filtered_releases(tracker, ["latest", "stable", "lts", "24.04.1"])

    stable_release = next(release for release in releases if release.tag_name == "stable")
    lts_release = next(release for release in releases if release.tag_name == "lts")
    latest_release = next(release for release in releases if release.tag_name == "latest")

    assert stable_release.version == "stable"
    assert lts_release.version == "lts"
    assert latest_release.version == "latest"


def test_latest_does_not_fold_into_suffixed_numeric_target():
    tracker = DockerTracker(name="docker-test", image="library/nginx")
    releases = _build_filtered_releases(tracker, ["latest", "2.0.0-rc.1", "1.9.0"])

    latest_release = next(release for release in releases if release.tag_name == "latest")

    assert latest_release.version == "latest"


def test_jenkins_style_tags_rank_by_natural_suffix_after_filter():
    tracker = DockerTracker(
        name="jenkins-agent",
        image="jenkins/inbound-agent",
        registry="docker.io",
        channels=[
            Channel(
                name="stable",
                include_pattern=r"^\d+\.v[\w_]+-\d+$",
                exclude_pattern=r".*jdk.*",
            )
        ],
    )
    releases = _build_filtered_releases(
        tracker,
        [
            "3355.v388858a_47b_33-9",
            "3355.v388858a_47b_33-19",
            "3355.v388858a_47b_33-20",
            "3355.v388858a_47b_33-20-jdk17",
            "3355.v388858a_47b_33-2",
        ],
    )
    ranked_tags = [
        release.tag_name
        for release in sorted(releases, key=lambda release: release.published_at, reverse=True)
    ]

    assert ranked_tags == [
        "3355.v388858a_47b_33-20",
        "3355.v388858a_47b_33-19",
        "3355.v388858a_47b_33-9",
        "3355.v388858a_47b_33-2",
    ]


def test_prerelease_tags_rank_by_numeric_suffix():
    tracker = DockerTracker(
        name="aether-test",
        image="fawney19/aether",
        registry="ghcr.io",
        channels=[Channel(name="canary", include_pattern=r".*rc.*")],
    )
    releases = _build_filtered_releases(
        tracker,
        ["latest", "0.6.3", *[f"0.7.0-rc{index}" for index in range(1, 20)]],
    )
    rc_releases = [release for release in releases if release.tag_name.startswith("0.7.0-rc")]
    ranked_rc_tags = [
        release.tag_name
        for release in sorted(rc_releases, key=lambda release: release.published_at, reverse=True)
    ]

    assert ranked_rc_tags[:3] == ["0.7.0-rc19", "0.7.0-rc18", "0.7.0-rc17"]


def test_latest_alias_stays_independent_from_numeric_tags():
    tracker = DockerTracker(name="openbao-test", image="ghcr.io/openbao/openbao")
    releases = _build_filtered_releases(tracker, ["latest", "2.5.3", "2.5.2"])

    latest_alias = next(release for release in releases if release.tag_name == "latest")
    newest_numeric_release = next(release for release in releases if release.tag_name == "2.5.3")
    numeric_release = next(release for release in releases if release.tag_name == "2.5.2")

    assert latest_alias.version == "latest"
    assert newest_numeric_release.version == "2.5.3"
    assert newest_numeric_release.published_at > numeric_release.published_at


def test_openbao_arch_exclude_keeps_raw_tag_matches():
    tracker = DockerTracker(
        name="openbao-test",
        image="openbao/openbao",
        registry="ghcr.io",
        channels=[
            Channel(
                name="stable",
                type="release",
                exclude_pattern=r"-(?:amd64|arm64|armv7|armv6|arm|aarch64|x86_64|riscv64|ppc64le|s390x)$",
            )
        ],
    )
    releases = _build_filtered_releases(
        tracker,
        ["2.5", "2.5.3", "2.5.3-amd64", "2.5.3-arm64"],
    )

    assert {release.tag_name for release in releases} == {"2.5", "2.5.3"}
    shorthand_release = next(release for release in releases if release.tag_name == "2.5")
    concrete_release = next(release for release in releases if release.tag_name == "2.5.3")

    assert shorthand_release.version == "2.5"
    assert concrete_release.version == "2.5.3"


def test_docker_channel_exclude_uses_raw_tag_only():
    tracker = DockerTracker(
        name="jenkins-agent",
        image="jenkins/inbound-agent",
        channels=[
            Channel(
                name="stable",
                type="release",
                include_pattern=r"(latest|trixie)",
                exclude_pattern=r"^.*-.*$",
            )
        ],
    )

    latest_release = tracker._tag_to_release("latest")
    latest_release.version = "3256.3258.v858f3c9a_f69d-1"

    assert tracker.should_include_in_channel(latest_release, tracker.config["channels"][0]) is True
    assert tracker._should_include(latest_release) is True


def test_docker_channel_include_remains_tag_based_for_aliases():
    tracker = DockerTracker(
        name="jenkins-agent",
        image="jenkins/inbound-agent",
        channels=[
            Channel(
                name="stable",
                type="release",
                include_pattern=r"(latest|trixie)",
                exclude_pattern=r"-windows$",
            )
        ],
    )

    latest_release = tracker._tag_to_release("latest")
    latest_release.version = "3256.3258.v858f3c9a_f69d-1"

    assert tracker.should_include_in_channel(latest_release, tracker.config["channels"][0]) is True
    assert tracker._should_include(latest_release) is True


@pytest.mark.asyncio
async def test_fetch_all_enriches_digest_with_head_then_get_fallback(monkeypatch):
    tracker = DockerTracker(name="ubuntu-test", image="library/ubuntu")
    request_calls: list[tuple[str, str]] = []

    async def fake_get_bearer_token(self, client, scope):
        return None

    async def fake_fetch_tags(self, client, bearer_token):
        return ["latest", "24.04", "24.04.1"]

    async def fake_request(self, method, url, headers=None, timeout=None):
        request_calls.append((method, url))
        request = httpx.Request(method, url, headers=headers)
        tag = url.rsplit("/", 1)[-1]

        if method == "HEAD" and tag == "latest":
            return httpx.Response(200, request=request)
        if method == "GET" and tag == "latest":
            return httpx.Response(
                200,
                headers={
                    "Docker-Content-Digest": "sha256:digest-latest",
                    "Content-Type": "application/vnd.oci.image.index.v1+json",
                },
                request=request,
            )
        if method == "HEAD" and tag == "24.04.1":
            return httpx.Response(
                200,
                headers={
                    "Docker-Content-Digest": "sha256:digest-24041",
                    "Content-Type": "application/vnd.oci.image.manifest.v1+json",
                },
                request=request,
            )
        if method == "HEAD" and tag == "24.04":
            return httpx.Response(
                200,
                headers={
                    "Docker-Content-Digest": "sha256:digest-2404",
                    "Content-Type": "application/vnd.oci.image.manifest.v1+json",
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_get_bearer_token)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_fetch_tags)
    monkeypatch.setattr(docker_module.httpx.AsyncClient, "request", fake_request)

    releases = await tracker.fetch_all(limit=3)

    assert [release.tag_name for release in releases] == ["latest", "24.04.1", "24.04"]
    assert [release.version for release in releases] == ["latest", "24.04.1", "24.04"]
    assert [release.commit_sha for release in releases] == [
        "sha256:digest-latest",
        "sha256:digest-24041",
        "sha256:digest-2404",
    ]
    assert request_calls[:4] == [
        ("HEAD", "https://registry-1.docker.io/v2/library/ubuntu/manifests/latest"),
        ("GET", "https://registry-1.docker.io/v2/library/ubuntu/manifests/latest"),
        ("HEAD", "https://registry-1.docker.io/v2/library/ubuntu/manifests/24.04.1"),
        ("HEAD", "https://registry-1.docker.io/v2/library/ubuntu/manifests/24.04"),
    ]


@pytest.mark.asyncio
async def test_fetch_all_normalizes_full_ghcr_image_ref(monkeypatch):
    tracker = DockerTracker(
        name="openbao-test",
        image="ghcr.io/openbao/openbao",
        registry="https://ghcr.io/",
    )
    token_scopes: list[str] = []
    tag_fetches: list[tuple[str, str | None]] = []
    manifest_calls: list[tuple[str, str]] = []

    async def fake_get_bearer_token(self, client, scope):
        token_scopes.append(scope)
        return "bearer-token"

    async def fake_fetch_tags(self, client, bearer_token):
        tag_fetches.append((self.registry, self.image))
        return ["latest", "2.5.3"]

    async def fake_request(self, method, url, headers=None, timeout=None):
        manifest_calls.append((method, url))
        request = httpx.Request(method, url, headers=headers)
        return httpx.Response(
            200,
            headers={
                "Docker-Content-Digest": "sha256:openbao",
                "Content-Type": "application/vnd.oci.image.index.v1+json",
            },
            request=request,
        )

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_get_bearer_token)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_fetch_tags)
    monkeypatch.setattr(docker_module.httpx.AsyncClient, "request", fake_request)

    releases = await tracker.fetch_all(limit=2)

    assert token_scopes == ["repository:openbao/openbao:pull"]
    assert tag_fetches == [("ghcr.io", "openbao/openbao")]
    assert manifest_calls == [
        ("HEAD", "https://ghcr.io/v2/openbao/openbao/manifests/latest"),
        ("HEAD", "https://ghcr.io/v2/openbao/openbao/manifests/2.5.3"),
    ]
    assert [release.url for release in releases] == [
        "https://ghcr.io/openbao/openbao:latest",
        "https://ghcr.io/openbao/openbao:2.5.3",
    ]


@pytest.mark.asyncio
async def test_fetch_all_keeps_candidates_when_digest_lookup_fails(monkeypatch):
    tracker = DockerTracker(name="ubuntu-test", image="library/ubuntu")

    async def fake_get_bearer_token(self, client, scope):
        return None

    async def fake_fetch_tags(self, client, bearer_token):
        return ["latest", "24.04", "24.04.1"]

    async def fake_resolve_manifest_digest(self, client, tag, bearer_token, scope):
        return None, None, "manifest_not_found", bearer_token

    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_get_bearer_token)
    monkeypatch.setattr(DockerTracker, "_fetch_tags", fake_fetch_tags)
    monkeypatch.setattr(DockerTracker, "_resolve_manifest_digest", fake_resolve_manifest_digest)

    releases = await tracker.fetch_all(limit=3)

    assert [release.tag_name for release in releases] == ["latest", "24.04.1", "24.04"]
    assert [release.version for release in releases] == ["latest", "24.04.1", "24.04"]
    assert all(release.commit_sha is None for release in releases)


@pytest.mark.asyncio
async def test_manifest_request_retries_once_after_401_with_token_refresh(monkeypatch):
    tracker = DockerTracker(name="docker-test", image="library/nginx")
    seen_auth: list[str | None] = []

    async def fake_request(self, method, url, headers=None, timeout=None):
        seen_auth.append((headers or {}).get("Authorization"))
        request = httpx.Request(method, url, headers=headers)
        if len(seen_auth) == 1:
            return httpx.Response(401, request=request)
        return httpx.Response(200, request=request)

    async def fake_get_bearer_token(self, client, scope):
        return "refreshed-token"

    monkeypatch.setattr(docker_module.httpx.AsyncClient, "request", fake_request)
    monkeypatch.setattr(DockerTracker, "_get_bearer_token", fake_get_bearer_token)

    async with docker_module.httpx.AsyncClient() as client:
        response, refreshed_token = await tracker._request_manifest(
            client,
            "HEAD",
            "https://registry-1.docker.io/v2/library/nginx/manifests/latest",
            bearer_token=None,
            scope="repository:library/nginx:pull",
        )

    assert response.status_code == 200
    assert refreshed_token == "refreshed-token"
    assert seen_auth == [None, "Bearer refreshed-token"]


def test_docker_channel_filters_keep_enabled_regex_rules_isolated():
    tracker = DockerTracker(
        name="docker-test",
        image="library/nginx",
        channels=[
            Channel(
                name="stable",
                type="release",
                include_pattern=r"^v1\.",
                exclude_pattern=r"-arm64$",
            ),
            Channel(
                name="prerelease",
                type="prerelease",
                include_pattern=r"beta",
                exclude_pattern=r"-arm64$",
            ),
        ],
    )
    tags = [
        "v1.2.0",
        "v1.2.0-arm64",
        "v2.0.0-beta.1",
        "v2.0.0-beta.1-arm64",
        "v2.0.0-rc.1",
    ]
    releases = [tracker._tag_to_release(tag) for tag in _sort_tags(tags)]

    filtered = tracker.filter_by_channels(releases)

    assert {release.tag_name for release in filtered["stable"]} == {"v1.2.0"}
    assert {release.tag_name for release in filtered["prerelease"]} == {"v2.0.0-beta.1"}


def test_container_channel_type_does_not_filter_tag_classification():
    tracker = DockerTracker(
        name="docker-test",
        image="library/nginx",
        channels=[Channel(name="canary", type="prerelease")],
    )
    stable_release = tracker._tag_to_release("v1.2.0")

    assert stable_release.prerelease is False
    assert tracker.should_include_in_channel(stable_release, tracker.config["channels"][0]) is True


def test_docker_channel_invalid_regex_logs_and_keeps_other_rules_deterministic(caplog):
    tracker = DockerTracker(
        name="docker-test",
        image="library/nginx",
        channels=[
            Channel(
                name="stable",
                type="release",
                include_pattern="[",
                exclude_pattern=r"-arm64$",
            )
        ],
    )
    stable_channel = tracker.config["channels"][0]
    plain_release = tracker._tag_to_release("v1.2.3")
    arm64_release = tracker._tag_to_release("v1.2.3-arm64")

    with caplog.at_level(logging.ERROR):
        assert tracker.should_include_in_channel(plain_release, stable_channel) is True
        assert tracker.should_include_in_channel(arm64_release, stable_channel) is False

    assert "Invalid include_pattern regex for channel 'stable'" in caplog.text
