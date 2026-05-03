from datetime import datetime

import pytest

from releasetracker.config import Channel, RuntimeConnectionConfig, TrackerConfig
from releasetracker.models import AggregateTracker, Release, ReleaseChannel, TrackerSource


def test_tracker_models_round_trip_canonical_source_contract():
    tracker_source = TrackerSource(
        source_key="image",
        source_type="container",
        source_rank=1,
        source_config={"image": "owner/repo", "registry": "ghcr.io"},
    )
    aggregate_tracker = AggregateTracker(
        name="canonical-model",
        primary_changelog_source_key="image",
        sources=[tracker_source],
    )
    tracker_config = TrackerConfig(
        name="canonical-model",
        type="container",
        image="owner/repo",
    )

    assert tracker_source.source_key == "image"
    assert tracker_source.model_dump(mode="json")["source_type"] == "container"
    assert aggregate_tracker.primary_changelog_source_key == "image"
    assert aggregate_tracker.model_dump(mode="json")["sources"][0]["source_key"] == "image"
    assert aggregate_tracker.model_dump(mode="json")["sources"][0]["source_type"] == "container"
    assert tracker_config.type == "container"


def test_tracker_source_model_round_trips_nested_release_channels():
    tracker_source = TrackerSource(
        source_key="image",
        source_type="container",
        source_rank=1,
        source_config={"image": "owner/repo", "registry": "ghcr.io"},
        release_channels=[
            ReleaseChannel(
                release_channel_key="image-preview",
                name="stable",
                type="prerelease",
                enabled=True,
            )
        ],
    )

    assert tracker_source.release_channels == [
        ReleaseChannel(
            release_channel_key="image-preview",
            name="stable",
            type="prerelease",
            enabled=True,
        )
    ]
    assert tracker_source.model_dump(mode="json")["release_channels"] == [
        {
            "release_channel_key": "image-preview",
            "name": "stable",
            "type": "prerelease",
            "include_pattern": None,
            "exclude_pattern": None,
            "enabled": True,
        }
    ]
    assert tracker_source.model_dump(mode="json")["source_config"] == {
        "image": "owner/repo",
        "registry": "ghcr.io",
    }


@pytest.mark.asyncio
async def test_tracker_api_returns_canonical_source_contract(authed_client):
    response = authed_client.post(
        "/api/trackers",
        json={
            "name": "canonical-tracker",
            "enabled": True,
            "description": "canonical tracker",
            "primary_changelog_source_key": "image",
            "sources": [
                {
                    "source_key": "repo",
                    "source_type": "github",
                    "enabled": True,
                    "source_rank": 0,
                    "source_config": {"repo": "owner/canonical-tracker"},
                },
                {
                    "source_key": "image",
                    "source_type": "container",
                    "enabled": True,
                    "source_rank": 1,
                    "source_config": {
                        "image": "owner/canonical-tracker",
                        "registry": "ghcr.io",
                    },
                },
            ],
            "interval": 60,
            "version_sort_mode": "published_at",
            "fetch_limit": 10,
            "fetch_timeout": 15,
            "fallback_tags": False,
            "channels": [{"name": "stable", "type": "release", "enabled": True}],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["primary_changelog_source_key"] == "image"
    sources = {source["source_key"]: source for source in body["sources"]}
    assert set(sources) == {"repo", "image"}
    assert {source["source_type"] for source in sources.values()} == {"github", "container"}
    assert sources["repo"]["release_channels"] == []
    assert sources["image"]["release_channels"] == []
    assert body["channels"] == []
    assert body["status"]["source_types"] == ["container", "github"]


@pytest.mark.asyncio
async def test_tracker_api_rejects_channel_era_alias_fields(authed_client):
    response = authed_client.post(
        "/api/trackers",
        json={
            "name": "alias-rejected-tracker",
            "enabled": True,
            "description": "legacy aliases should be rejected",
            "primary_changelog_channel_key": "image",
            "tracker_channels": [
                {
                    "source_key": "image",
                    "source_type": "container",
                    "enabled": True,
                    "source_rank": 0,
                    "source_config": {
                        "image": "owner/alias-rejected-tracker",
                        "registry": "ghcr.io",
                    },
                }
            ],
            "interval": 60,
            "version_sort_mode": "published_at",
            "fetch_limit": 10,
            "fetch_timeout": 15,
            "fallback_tags": False,
            "channels": [{"name": "stable", "type": "release", "enabled": True}],
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_tracker_api_rejects_nested_source_channel_alias_fields(authed_client):
    response = authed_client.post(
        "/api/trackers",
        json={
            "name": "nested-alias-rejected-tracker",
            "enabled": True,
            "description": "nested source aliases should be rejected",
            "primary_changelog_source_key": "image",
            "sources": [
                {
                    "channel_key": "image",
                    "channel_type": "docker",
                    "enabled": True,
                    "channel_rank": 0,
                    "channel_config": {
                        "image": "owner/nested-alias-rejected-tracker",
                        "registry": "ghcr.io",
                    },
                }
            ],
            "interval": 60,
            "version_sort_mode": "published_at",
            "fetch_limit": 10,
            "fetch_timeout": 15,
            "fallback_tags": False,
            "channels": [{"name": "stable", "type": "release", "enabled": True}],
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_tracker_api_rejects_mixed_canonical_and_legacy_top_level_keys(authed_client):
    response = authed_client.post(
        "/api/trackers",
        json={
            "name": "mixed-top-level-alias-tracker",
            "enabled": True,
            "description": "mixed canonical and legacy keys must be rejected",
            "primary_changelog_source_key": "image",
            "primary_changelog_channel_key": "image",
            "sources": [
                {
                    "source_key": "image",
                    "source_type": "container",
                    "enabled": True,
                    "source_rank": 0,
                    "source_config": {
                        "image": "owner/mixed-top-level-alias-tracker",
                        "registry": "ghcr.io",
                    },
                }
            ],
            "interval": 60,
            "version_sort_mode": "published_at",
            "fetch_limit": 10,
            "fetch_timeout": 15,
            "fallback_tags": False,
            "channels": [{"name": "stable", "type": "release", "enabled": True}],
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize("legacy_field_name", ["channel_key", "key"])
@pytest.mark.asyncio
async def test_tracker_api_rejects_release_channel_legacy_alias_keys(
    authed_client, legacy_field_name
):
    response = authed_client.post(
        "/api/trackers",
        json={
            "name": "release-channel-alias-rejected-tracker",
            "enabled": True,
            "description": "nested release_channel aliases should be rejected",
            "primary_changelog_source_key": "image",
            "sources": [
                {
                    "source_key": "image",
                    "source_type": "container",
                    "enabled": True,
                    "source_rank": 0,
                    "source_config": {
                        "image": "owner/release-channel-alias-rejected-tracker",
                        "registry": "ghcr.io",
                    },
                    "release_channels": [
                        {
                            legacy_field_name: "image-stable",
                            "name": "stable",
                            "type": "release",
                            "enabled": True,
                        }
                    ],
                }
            ],
            "interval": 60,
            "version_sort_mode": "published_at",
            "fetch_limit": 10,
            "fetch_timeout": 15,
            "fallback_tags": False,
            "channels": [{"name": "stable", "type": "release", "enabled": True}],
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_executor_api_uses_canonical_tracker_source_id_contract(
    authed_client, storage, monkeypatch
):
    runtime_id = await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name="canonical-runtime",
            type="docker",
            enabled=True,
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={"token": "runtime-secret"},
            description="runtime",
        )
    )
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="canonical-executor",
            primary_changelog_source_key="image",
            sources=[
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=0,
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="image-stable",
                            name="stable",
                            type="release",
                            enabled=True,
                        )
                    ],
                    source_config={"image": "ghcr.io/acme/canonical-executor", "registry": "ghcr.io"},
                )
            ],
        )
    )
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="canonical-executor",
            type="container",
            enabled=True,
            image="ghcr.io/acme/canonical-executor",
            channels=[Channel(name="stable", enabled=True, type="release")],
        )
    )
    tracker_source_id = aggregate_tracker.sources[0].id
    assert tracker_source_id is not None

    class FakeDiscoveryAdapter:
        def __init__(self, runtime_connection):
            self.runtime_connection = runtime_connection

        async def validate_target_ref(self, target_ref):
            return None

    monkeypatch.setattr(
        "releasetracker.routers.executors.DockerRuntimeAdapter",
        lambda runtime_connection: FakeDiscoveryAdapter(runtime_connection),
    )

    create_response = authed_client.post(
        "/api/executors",
        json={
                "name": "canonical-executor-binding",
                "runtime_type": "docker",
                "runtime_connection_id": runtime_id,
                "tracker_name": "canonical-executor",
            "tracker_source_id": tracker_source_id,
            "channel_name": "stable",
            "enabled": True,
            "image_selection_mode": "use_tracker_image_and_tag",
            "update_mode": "manual",
                "target_ref": {
                    "mode": "container",
                    "container_id": "abc",
                    "container_name": "canonical-executor",
                },
            },
    )

    assert create_response.status_code == 200, create_response.text
    executor_id = create_response.json()["id"]
    config_response = authed_client.get(f"/api/executors/{executor_id}/config")
    detail_response = authed_client.get(f"/api/executors/{executor_id}")

    assert config_response.status_code == 200, config_response.text
    assert detail_response.status_code == 200, detail_response.text
    assert config_response.json()["tracker_source_id"] == tracker_source_id
    assert detail_response.json()["tracker_source_id"] == tracker_source_id
