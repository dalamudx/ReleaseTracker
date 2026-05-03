from datetime import datetime

import aiosqlite
import pytest

from releasetracker.config import TrackerConfig
from releasetracker.scheduler import ReleaseScheduler
from releasetracker.models import AggregateTracker, Release, ReleaseChannel, TrackerSource, TrackerStatus


def make_tracker_payload(name: str, **overrides):
    payload = {
        "name": name,
        "enabled": True,
        "description": f"{name} aggregate tracker",
        "primary_changelog_source_key": "repo",
        "sources": [
            {
                "source_key": "repo",
                "source_type": "github",
                "enabled": True,
                "source_rank": 0,
                "source_config": {"repo": f"owner/{name}"},
            },
            {
                "source_key": "image",
                "source_type": "container",
                "enabled": True,
                "source_rank": 1,
                "source_config": {"image": f"owner/{name}", "registry": "ghcr.io"},
            },
        ],
        "interval": 60,
        "version_sort_mode": "published_at",
        "fetch_limit": 10,
        "fetch_timeout": 15,
        "fallback_tags": False,
        "github_fetch_mode": "graphql_first",
        "channels": [{"name": "stable", "type": "release", "enabled": True}],
    }
    payload.update(overrides)
    return payload


async def _materialize_projection_rows(
    storage,
    aggregate_tracker: AggregateTracker,
    releases_by_source_key: dict[str, list[Release]],
    projection_releases: list[Release] | None = None,
):
    assert aggregate_tracker.id is not None

    projected_releases: list[Release] = []
    for source_key, releases in releases_by_source_key.items():
        source = next(source for source in aggregate_tracker.sources if source.source_key == source_key)
        assert source.id is not None
        if releases:
            await storage.save_source_observations(
                aggregate_tracker.id,
                source,
                releases,
                observed_at=max(release.published_at for release in releases),
            )
        for release in releases:
            identity_key = storage.release_identity_key_for_source(
                release,
                source_type=source.source_type,
            )
            source_history_id = await storage.get_source_release_history_id(source.id, identity_key)
            assert source_history_id is not None
            await storage.upsert_tracker_release_history(
                aggregate_tracker.id,
                release,
                primary_source_release_history_id=source_history_id,
                source_type=source.source_type,
            )
        projected_releases.extend(releases)

    await storage.refresh_tracker_current_releases(
        aggregate_tracker.id,
        projection_releases if projection_releases is not None else projected_releases,
    )


@pytest.mark.asyncio
async def test_get_all_tracker_configs_cleans_blank_tracker_rows_before_materializing(storage):
    async with aiosqlite.connect(storage.db_path) as db:
        await db.execute(
            """
            INSERT INTO trackers (name, type, enabled, repo, channels, interval, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "valid-storage-tracker",
                "github",
                1,
                None,
                "[]",
                60,
                datetime.now().isoformat(),
                datetime.now().isoformat(),
            ),
        )
        await db.execute(
            """
            INSERT INTO trackers (name, type, enabled, repo, channels, interval, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "   ",
                "github",
                1,
                "dirty/repo",
                "[]",
                60,
                datetime.now().isoformat(),
                datetime.now().isoformat(),
            ),
        )
        await db.execute(
            """
            INSERT INTO tracker_status (name, type, enabled, last_check, last_version, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("   ", "github", 1, None, "v0.0.1", None),
        )
        await db.commit()

    configs = await storage.get_all_tracker_configs()

    assert [config.name for config in configs] == []

    async with aiosqlite.connect(storage.db_path) as db:
        tracker_count = await (await db.execute("SELECT COUNT(*) FROM trackers")).fetchone()
        status_count = await (await db.execute("SELECT COUNT(*) FROM tracker_status")).fetchone()

    assert tracker_count is not None
    assert status_count is not None
    assert tracker_count[0] == 1
    assert status_count[0] == 0


@pytest.mark.asyncio
async def test_create_tracker(authed_client):
    data = make_tracker_payload("test-tracker")
    response = authed_client.post("/api/trackers", json=data)
    assert response.status_code == 200, f"Failed: {response.text}"
    body = response.json()
    assert body["name"] == "test-tracker"
    assert body["primary_changelog_source_key"] == "repo"
    assert body["github_fetch_mode"] == "graphql_first"
    assert len(body["sources"]) == 2
    assert body["sources"][1]["source_type"] == "container"
    assert body["status"]["source_count"] == 2

    resp = authed_client.get("/api/trackers/test-tracker")
    assert resp.status_code == 200
    assert resp.json()["name"] == "test-tracker"
    assert resp.json()["sources"][0]["source_key"] == "repo"


@pytest.mark.asyncio
async def test_create_tracker_returns_canonical_source_contract(authed_client):
    response = authed_client.post(
        "/api/trackers",
        json={
            "name": "legacy-source-payload",
            "enabled": True,
            "description": "legacy-source-payload aggregate tracker",
            "primary_changelog_source_key": "image",
            "sources": [
                {
                    "source_key": "repo",
                    "source_type": "github",
                    "enabled": True,
                    "source_rank": 0,
                    "source_config": {"repo": "owner/legacy-source-payload"},
                },
                {
                    "source_key": "image",
                    "source_type": "container",
                    "enabled": True,
                    "source_rank": 1,
                    "source_config": {
                        "image": "owner/legacy-source-payload",
                        "registry": "ghcr.io",
                    },
                },
            ],
            "interval": 60,
            "version_sort_mode": "published_at",
            "fetch_limit": 10,
            "fetch_timeout": 15,
            "fallback_tags": False,
            "github_fetch_mode": "rest_first",
            "channels": [{"name": "stable", "type": "release", "enabled": True}],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["github_fetch_mode"] == "rest_first"
    assert body["primary_changelog_source_key"] == "image"
    assert {source["source_key"] for source in body["sources"]} == {"repo", "image"}
    assert {source["source_type"] for source in body["sources"]} == {
        "github",
        "container",
    }


@pytest.mark.asyncio
async def test_canonical_runtime_config_wins_over_conflicting_legacy_trackers_row(storage):
    await storage.create_aggregate_tracker(
        AggregateTracker(
            name="canonical-runtime-wins",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/canonical-runtime-wins"},
                )
            ],
        )
    )
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="canonical-runtime-wins",
            type="github",
            enabled=True,
            repo="owner/canonical-runtime-wins",
            interval=90,
            fetch_limit=10,
            fetch_timeout=15,
            github_fetch_mode="rest_first",
        )
    )

    async with aiosqlite.connect(storage.db_path) as db:
        await db.execute(
            """
            UPDATE trackers
            SET type = ?,
                repo = ?,
                image = ?,
                registry = ?,
                credential_name = ?,
                interval = ?,
                version_sort_mode = ?,
                fetch_limit = ?,
                fetch_timeout = ?,
                fallback_tags = ?,
                github_fetch_mode = ?,
                channels = ?,
                updated_at = ?
            WHERE name = ?
            """,
            (
                "docker",
                "legacy/override",
                "ghcr.io/legacy/override",
                "ghcr.io",
                "legacy-token",
                321,
                "semver",
                77,
                66,
                1,
                "graphql_first",
                '[{"name":"legacy","type":"prerelease","enabled":true}]',
                datetime.now().isoformat(),
                "canonical-runtime-wins",
            ),
        )
        await db.commit()

    config = await storage.get_tracker_config("canonical-runtime-wins")

    assert config is not None
    assert config.name == "canonical-runtime-wins"
    assert config.type == "github"
    assert config.repo == "owner/canonical-runtime-wins"
    assert config.image is None
    assert config.credential_name is None
    assert config.interval == 360
    assert config.version_sort_mode == "published_at"
    assert config.fetch_limit == 10
    assert config.fetch_timeout == 15
    assert config.fallback_tags is False
    assert config.github_fetch_mode == "rest_first"


def test_tracker_config_trims_padded_name():
    tracker = TrackerConfig(
        name="  padded-tracker  ",
        type="github",
        repo="owner/repo",
        interval=60,
    )

    assert tracker.name == "padded-tracker"


@pytest.mark.asyncio
async def test_create_tracker_trims_name_and_blocks_trimmed_duplicate(authed_client):
    response = authed_client.post(
        "/api/trackers",
        json=make_tracker_payload("  dup-tracker  "),
    )

    assert response.status_code == 200, f"Failed: {response.text}"

    trimmed_lookup = authed_client.get("/api/trackers/dup-tracker")
    padded_lookup = authed_client.get("/api/trackers/%20%20dup-tracker%20%20")
    duplicate_response = authed_client.post(
        "/api/trackers",
        json=make_tracker_payload("dup-tracker"),
    )

    assert trimmed_lookup.status_code == 200
    assert trimmed_lookup.json()["name"] == "dup-tracker"
    assert padded_lookup.status_code == 404
    assert duplicate_response.status_code == 400
    assert "名称已存在" in duplicate_response.json()["detail"]


@pytest.mark.asyncio
async def test_get_trackers_pagination(authed_client):
    authed_client.post("/api/trackers", json=make_tracker_payload("t1"))
    authed_client.post("/api/trackers", json=make_tracker_payload("t2"))

    response = authed_client.get("/api/trackers?limit=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["total"] >= 2
    assert "sources" in data["items"][0]


@pytest.mark.asyncio
async def test_tracker_status_last_version_uses_projection_derived_latest_identity(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="tracker-status-mixed",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/project"},
                ),
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=1,
                    source_config={"image": "owner/project", "registry": "ghcr.io"},
                ),
            ],
        )
    )

    repo_source = next(
        source for source in aggregate_tracker.sources if source.source_key == "repo"
    )
    image_source = next(
        source for source in aggregate_tracker.sources if source.source_key == "image"
    )
    repo_release = Release(
        tracker_name="tracker-status-mixed",
        tracker_type="github",
        version="2.0.0",
        name="Release 2.0.0",
        tag_name="v2.0.0",
        url="http://example.com/releases/v2.0.0",
        published_at=datetime(2025, 9, 1, 12, 0, 0),
        prerelease=False,
    )
    image_release = Release(
        tracker_name="tracker-status-mixed",
        tracker_type="container",
        version="3.0.0",
        name="Image 3.0.0",
        tag_name="3.0.0",
        url="http://example.com/images/3.0.0",
        published_at=datetime(2025, 9, 2, 12, 0, 0),
        prerelease=False,
    )
    await _materialize_projection_rows(
        storage,
        aggregate_tracker,
        {"repo": [repo_release], "image": [image_release]},
    )

    list_response = authed_client.get("/api/trackers")
    detail_response = authed_client.get("/api/trackers/tracker-status-mixed")

    assert list_response.status_code == 200, list_response.text
    assert detail_response.status_code == 200, detail_response.text

    listed_item = next(
        item for item in list_response.json()["items"] if item["name"] == "tracker-status-mixed"
    )
    assert listed_item["status"]["last_version"] == "3.0.0"
    assert detail_response.json()["status"]["last_version"] == "3.0.0"


@pytest.mark.asyncio
async def test_tracker_status_last_version_uses_container_history_for_container_only_aggregate(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="tracker-status-container-only",
            primary_changelog_source_key="image",
            sources=[
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=0,
                    source_config={"image": "owner/project", "registry": "ghcr.io"},
                )
            ],
        )
    )
    image_source = aggregate_tracker.sources[0]
    image_release = Release(
        tracker_name="tracker-status-container-only",
        tracker_type="container",
        version="4.0.0",
        name="Image 4.0.0",
        tag_name="4.0.0",
        url="http://example.com/images/4.0.0",
        published_at=datetime(2025, 10, 1, 12, 0, 0),
        prerelease=False,
    )
    await _materialize_projection_rows(
        storage,
        aggregate_tracker,
        {"image": [image_release]},
    )

    detail_response = authed_client.get("/api/trackers/tracker-status-container-only")

    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["status"]["last_version"] == "4.0.0"


@pytest.mark.asyncio
async def test_tracker_status_last_version_prefers_current_projection_over_stale_status_value(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="tracker-status-stale-latest",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/project"},
                )
            ],
        )
    )

    repo_source = aggregate_tracker.sources[0]
    observed_at = datetime(2025, 11, 1, 12, 0, 0)

    repo_release = Release(
        tracker_name="tracker-status-stale-latest",
        tracker_type="github",
        version="v1.13.7",
        name="Release v1.13.7",
        tag_name="v1.13.7",
        url="http://example.com/releases/v1.13.7",
        published_at=observed_at,
        prerelease=False,
    )
    await _materialize_projection_rows(
        storage,
        aggregate_tracker,
        {"repo": [repo_release]},
    )
    await storage.update_tracker_status(
        TrackerStatus(
            name="tracker-status-stale-latest",
            type="github",
            enabled=True,
            last_check=observed_at,
            last_version="v1.13.8",
            error=None,
        )
    )

    detail_response = authed_client.get("/api/trackers/tracker-status-stale-latest")

    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["status"]["last_version"] == "v1.13.7"


@pytest.mark.asyncio
async def test_update_tracker(authed_client):
    authed_client.post("/api/trackers", json=make_tracker_payload("update-test"))

    response = authed_client.put(
        "/api/trackers/update-test",
        json=make_tracker_payload(
            "update-test",
            interval=120,
            primary_changelog_source_key="image",
            description="updated aggregate tracker",
        ),
    )
    assert response.status_code == 200, f"Failed: {response.text}"
    body = response.json()
    assert body["interval"] == 120
    assert body["primary_changelog_source_key"] == "image"

    resp = authed_client.get("/api/trackers/update-test/config")
    assert resp.status_code == 200
    assert resp.json()["interval"] == 120
    assert resp.json()["description"] == "updated aggregate tracker"


@pytest.mark.asyncio
async def test_update_tracker_does_not_trigger_remote_check(authed_client, monkeypatch):
    authed_client.post("/api/trackers", json=make_tracker_payload("update-check-refresh"))
    called: list[str] = []
    scheduler = authed_client.app.state.scheduler
    real_scheduler = ReleaseScheduler(authed_client.app.state.storage)

    async def fake_check_tracker_now_v2(name: str):
        called.append(name)
        raise RuntimeError("remote temporarily unavailable")

    scheduler.check_tracker_now_v2.side_effect = fake_check_tracker_now_v2
    scheduler.rebuild_tracker_views_from_storage.side_effect = (
        real_scheduler.rebuild_tracker_views_from_storage
    )

    response = authed_client.put(
        "/api/trackers/update-check-refresh",
        json=make_tracker_payload(
            "update-check-refresh",
            channels=[],
            sources=[
                {
                    "source_key": "repo",
                    "source_type": "github",
                    "enabled": True,
                    "source_rank": 0,
                    "source_config": {"repo": "owner/update-check-refresh"},
                    "release_channels": [
                        {
                            "release_channel_key": "repo-stable",
                            "name": "stable",
                            "type": "release",
                            "enabled": True,
                            "exclude_pattern": r"-(?:amd64|arm64|riscv64)$",
                        }
                    ],
                }
            ],
        ),
    )

    assert response.status_code == 200, response.text
    assert called == []
    assert (
        response.json()["sources"][0]["release_channels"][0]["exclude_pattern"]
        == r"-(?:amd64|arm64|riscv64)$"
    )


@pytest.mark.asyncio
async def test_update_tracker_rebuilds_existing_observations_without_remote_refetch(
    authed_client, storage, monkeypatch
):
    tracker_payload = make_tracker_payload("local-rebuild-only")
    create_response = authed_client.post("/api/trackers", json=tracker_payload)
    assert create_response.status_code == 200, create_response.text

    aggregate_tracker = await storage.get_aggregate_tracker("local-rebuild-only")
    assert aggregate_tracker is not None
    repo_source = next(
        source for source in aggregate_tracker.sources if source.source_key == "repo"
    )

    release_timestamp = datetime(2025, 10, 1, 12, 0, 0)
    await _materialize_projection_rows(
        storage,
        aggregate_tracker,
        {
            "repo": [
                Release(
                    tracker_name="local-rebuild-only",
                    tracker_type="github",
                    version="2.0.0",
                    name="Release 2.0.0",
                    tag_name="v2.0.0",
                    url="http://example.com/releases/v2.0.0",
                    published_at=release_timestamp,
                    prerelease=False,
                ),
                Release(
                    tracker_name="local-rebuild-only",
                    tracker_type="github",
                    version="2.0.0-riscv64",
                    name="Release 2.0.0-riscv64",
                    tag_name="v2.0.0-riscv64",
                    url="http://example.com/releases/v2.0.0-riscv64",
                    published_at=release_timestamp,
                    prerelease=False,
                ),
            ]
        },
    )

    scheduler = authed_client.app.state.scheduler
    real_scheduler = ReleaseScheduler(storage)

    async def fail_if_remote_check(name: str):
        raise AssertionError(f"remote check should not be called for {name}")

    monkeypatch.setattr(scheduler, "check_tracker_now_v2", fail_if_remote_check)
    scheduler.rebuild_tracker_views_from_storage.side_effect = (
        real_scheduler.rebuild_tracker_views_from_storage
    )

    update_response = authed_client.put(
        "/api/trackers/local-rebuild-only",
        json=make_tracker_payload(
            "local-rebuild-only",
            channels=[],
            sources=[
                {
                    "source_key": "repo",
                    "source_type": "github",
                    "enabled": True,
                    "source_rank": 0,
                    "source_config": {"repo": "owner/local-rebuild-only"},
                    "release_channels": [
                        {
                            "release_channel_key": "repo-stable",
                            "name": "stable",
                            "type": "release",
                            "enabled": True,
                            "exclude_pattern": r"-riscv64$",
                        }
                    ],
                }
            ],
        ),
    )

    assert update_response.status_code == 200, update_response.text

    global_history_response = authed_client.get("/api/releases?tracker=local-rebuild-only")
    history_response = authed_client.get("/api/trackers/local-rebuild-only/releases/history")
    current_response = authed_client.get("/api/trackers/local-rebuild-only/current")

    assert global_history_response.status_code == 200, global_history_response.text
    assert history_response.status_code == 200, history_response.text
    assert current_response.status_code == 200, current_response.text
    global_history_items = global_history_response.json()["items"]
    history_items = history_response.json()["items"]
    assert [item["tag_name"] for item in global_history_items] == [
        "v2.0.0-riscv64",
        "v2.0.0",
    ]
    assert [item["tag_name"] for item in history_items] == [
        "v2.0.0",
    ]
    assert all("is_current" not in item for item in global_history_items)
    assert all("is_current" not in item for item in history_items)
    current_rows = current_response.json()["matrix"]["rows"]
    assert [item["version"] for item in current_rows] == ["2.0.0"]
    selected_row = next(row for row in current_rows if row["version"] == "2.0.0")
    assert selected_row["channel_keys"] == ["stable"]


@pytest.mark.asyncio
async def test_tracker_current_redesign_only_response_shape(authed_client, storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="current-redesign-shape",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/current-redesign-shape"},
                )
            ],
        )
    )

    await _materialize_projection_rows(
        storage,
        aggregate_tracker,
        {
            "repo": [
                Release(
                    tracker_name="current-redesign-shape",
                    tracker_type="github",
                    version="1.0.0",
                    name="Release 1.0.0",
                    tag_name="v1.0.0",
                    url="http://example.com/releases/v1.0.0",
                    published_at=datetime(2026, 1, 1, 12, 0, 0),
                    prerelease=False,
                )
            ]
        },
    )

    response = authed_client.get("/api/trackers/current-redesign-shape/current")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tracker"]["name"] == "current-redesign-shape"
    assert "primary_changelog_channel_key" not in body["tracker"]
    assert "tracker_channels" not in body["tracker"]


@pytest.mark.asyncio
async def test_tracker_current_latest_release_emits_channel_name_when_available(authed_client, storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="current-channel-name-shape",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/current-channel-name-shape"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="repo-stable",
                            name="stable",
                            type="release",
                        )
                    ],
                )
            ],
        )
    )

    await _materialize_projection_rows(
        storage,
        aggregate_tracker,
        {
            "repo": [
                Release(
                    tracker_name="current-channel-name-shape",
                    tracker_type="github",
                    version="1.0.0",
                    name="Release 1.0.0",
                    tag_name="v1.0.0",
                    channel_name="stable",
                    url="http://example.com/releases/v1.0.0",
                    published_at=datetime(2026, 1, 1, 12, 0, 0),
                    prerelease=False,
                )
            ]
        },
    )

    response = authed_client.get("/api/trackers/current-channel-name-shape/current")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["latest_release"]["channel_name"] == "stable"


@pytest.mark.asyncio
async def test_create_tracker_reject_legacy_request_shape(authed_client):
    legacy_policy_response = authed_client.post(
        "/api/trackers",
        json=make_tracker_payload("legacy-policy-shape", changelog_policy="primary_channel"),
    )

    legacy_primary_key_payload = make_tracker_payload("legacy-primary-key-shape")
    legacy_primary_key_payload.pop("primary_changelog_source_key")
    legacy_primary_key_payload["primary_changelog_channel_key"] = "repo"
    legacy_primary_key_response = authed_client.post(
        "/api/trackers",
        json=legacy_primary_key_payload,
    )

    assert legacy_policy_response.status_code == 422, legacy_policy_response.text
    assert "primary_source" in legacy_policy_response.text
    assert legacy_primary_key_response.status_code == 422, legacy_primary_key_response.text
    assert "primary_changelog_source_key" in legacy_primary_key_response.text


@pytest.mark.asyncio
async def test_local_rebuild_status_ignores_canonical_only_rows_without_redesign_authority(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="local-rebuild-canonical-only",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/local-rebuild-canonical-only"},
                )
            ],
        )
    )
    source = aggregate_tracker.sources[0]
    assert aggregate_tracker.id is not None
    assert source.id is not None

    observed_at = datetime(2026, 4, 22, 0, 0, 0).isoformat()
    async with aiosqlite.connect(storage.db_path) as db:
        observation_cursor = await db.execute(
            "INSERT INTO source_release_observations (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source.id,
                "v9.9.9",
                "Canonical-only 9.9.9",
                "v9.9.9",
                "9.9.9",
                observed_at,
                "https://example.com/releases/v9.9.9",
                None,
                0,
                "canonical-only body",
                None,
                '{"source_type":"github"}',
                observed_at,
                observed_at,
                observed_at,
            ),
        )
        observation_id = observation_cursor.lastrowid
        canonical_cursor = await db.execute(
            "INSERT INTO canonical_releases (aggregate_tracker_id, canonical_key, version, name, tag_name, published_at, url, changelog_url, prerelease, body, primary_observation_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                aggregate_tracker.id,
                "9.9.9",
                "9.9.9",
                "Canonical-only 9.9.9",
                "v9.9.9",
                observed_at,
                "https://example.com/releases/v9.9.9",
                None,
                0,
                "canonical-only body",
                observation_id,
                observed_at,
                observed_at,
            ),
        )
        canonical_release_id = canonical_cursor.lastrowid
        await db.execute(
            "INSERT INTO canonical_release_observations (canonical_release_id, source_release_observation_id, contribution_kind, created_at) VALUES (?, ?, ?, ?)",
            (canonical_release_id, observation_id, "primary", observed_at),
        )
        await db.commit()

    scheduler = ReleaseScheduler(storage)
    status = await scheduler.rebuild_tracker_views_from_storage("local-rebuild-canonical-only")

    assert status.last_version is None
    assert status.error == "未找到版本信息"


@pytest.mark.asyncio
async def test_tracker_api_persists_channel_regex_edits_across_create_and_update_roundtrips(
    authed_client,
):
    create_response = authed_client.post(
        "/api/trackers",
        json=make_tracker_payload(
            "regex-roundtrip",
            channels=[],
            sources=[
                {
                    "source_key": "repo",
                    "source_type": "github",
                    "enabled": True,
                    "source_rank": 0,
                    "source_config": {"repo": "owner/regex-roundtrip"},
                    "release_channels": [
                        {
                            "release_channel_key": "repo-stable",
                            "name": "stable",
                            "type": "release",
                            "enabled": True,
                            "include_pattern": r"^v?\d+\.\d+\.\d+$",
                            "exclude_pattern": r"-(?:rc|beta)",
                        },
                        {
                            "release_channel_key": "repo-beta",
                            "name": "beta",
                            "type": "prerelease",
                            "enabled": True,
                            "include_pattern": "beta",
                            "exclude_pattern": r"-arm64$",
                        },
                    ],
                }
            ],
        ),
    )

    assert create_response.status_code == 200, create_response.text
    create_body = create_response.json()
    assert create_body["channels"] == [
        {
            "name": "stable",
            "type": "release",
            "include_pattern": r"^v?\d+\.\d+\.\d+$",
            "exclude_pattern": r"-(?:rc|beta)",
            "enabled": True,
        },
        {
            "name": "beta",
            "type": "prerelease",
            "include_pattern": "beta",
            "exclude_pattern": r"-arm64$",
            "enabled": True,
        },
    ]
    assert create_body["sources"][0]["release_channels"] == [
        {
            "release_channel_key": "repo-stable",
            "name": "stable",
            "type": "release",
            "include_pattern": r"^v?\d+\.\d+\.\d+$",
            "exclude_pattern": r"-(?:rc|beta)",
            "enabled": True,
        },
        {
            "release_channel_key": "repo-beta",
            "name": "beta",
            "type": "prerelease",
            "include_pattern": "beta",
            "exclude_pattern": r"-arm64$",
            "enabled": True,
        },
    ]

    created_detail = authed_client.get("/api/trackers/regex-roundtrip/config")
    assert created_detail.status_code == 200, created_detail.text
    assert created_detail.json()["channels"] == create_body["channels"]
    assert (
        created_detail.json()["sources"][0]["release_channels"]
        == create_body["sources"][0]["release_channels"]
    )

    update_response = authed_client.put(
        "/api/trackers/regex-roundtrip",
        json=make_tracker_payload(
            "regex-roundtrip",
            channels=[],
            sources=[
                {
                    "source_key": "repo",
                    "source_type": "github",
                    "enabled": True,
                    "source_rank": 0,
                    "source_config": {"repo": "owner/regex-roundtrip"},
                    "release_channels": [
                        {
                            "release_channel_key": "repo-stable",
                            "name": "stable",
                            "type": "release",
                            "enabled": True,
                            "include_pattern": r"^release-",
                            "exclude_pattern": r"-hotfix$",
                        },
                        {
                            "release_channel_key": "repo-canary",
                            "name": "canary",
                            "type": "prerelease",
                            "enabled": False,
                            "include_pattern": "canary",
                            "exclude_pattern": None,
                        },
                    ],
                }
            ],
        ),
    )

    assert update_response.status_code == 200, update_response.text
    update_body = update_response.json()
    assert update_body["primary_changelog_source_key"] == "repo"
    assert update_body["channels"] == [
        {
            "name": "stable",
            "type": "release",
            "include_pattern": r"^release-",
            "exclude_pattern": r"-hotfix$",
            "enabled": True,
        },
        {
            "name": "canary",
            "type": "prerelease",
            "include_pattern": "canary",
            "exclude_pattern": None,
            "enabled": False,
        },
    ]
    assert update_body["sources"][0]["release_channels"] == [
        {
            "release_channel_key": "repo-stable",
            "name": "stable",
            "type": "release",
            "include_pattern": r"^release-",
            "exclude_pattern": r"-hotfix$",
            "enabled": True,
        },
        {
            "release_channel_key": "repo-canary",
            "name": "canary",
            "type": "prerelease",
            "include_pattern": "canary",
            "exclude_pattern": None,
            "enabled": False,
        },
    ]
    
    updated_detail = authed_client.get("/api/trackers/regex-roundtrip/config")
    assert updated_detail.status_code == 200, updated_detail.text
    assert updated_detail.json()["channels"] == update_body["channels"]
    assert (
        updated_detail.json()["sources"][0]["release_channels"]
        == update_body["sources"][0]["release_channels"]
    )


@pytest.mark.asyncio
async def test_update_tracker_accepts_padded_current_name(authed_client):
    authed_client.post("/api/trackers", json=make_tracker_payload("padded-update"))

    response = authed_client.put(
        "/api/trackers/padded-update",
        json=make_tracker_payload("  padded-update  ", interval=120),
    )

    assert response.status_code == 200, f"Failed: {response.text}"

    resp = authed_client.get("/api/trackers/padded-update/config")
    assert resp.status_code == 200
    assert resp.json()["name"] == "padded-update"
    assert resp.json()["interval"] == 120


@pytest.mark.asyncio
async def test_update_tracker_still_rejects_true_rename(authed_client):
    authed_client.post("/api/trackers", json=make_tracker_payload("rename-test"))

    response = authed_client.put(
        "/api/trackers/rename-test",
        json=make_tracker_payload("different-name", interval=120),
    )

    assert response.status_code == 400
    assert "不支持修改追踪器名称" in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_tracker(authed_client, storage):
    authed_client.post("/api/trackers", json=make_tracker_payload("del-test"))

    response = authed_client.delete("/api/trackers/del-test")
    assert response.status_code == 200
    assert response.json() == {"name": "del-test", "deleted": True}

    resp = authed_client.get("/api/trackers/del-test")
    assert resp.status_code == 404
    assert await storage.get_aggregate_tracker("del-test") is None


@pytest.mark.asyncio
async def test_get_trackers_cleans_up_blank_tracker_rows(authed_client, storage):
    authed_client.post("/api/trackers", json=make_tracker_payload("valid-tracker"))

    async with aiosqlite.connect(storage.db_path) as db:
        await db.execute(
            """
            INSERT INTO trackers (name, type, enabled, repo, channels, interval, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "   ",
                "github",
                1,
                "dirty/repo",
                "[]",
                60,
                datetime.now().isoformat(),
                datetime.now().isoformat(),
            ),
        )
        await db.execute(
            """
            INSERT INTO tracker_status (name, type, enabled, last_check, last_version, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("   ", "github", 1, None, "v0.0.1", None),
        )
        await db.commit()

    response = authed_client.get("/api/trackers")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert [item["name"] for item in data["items"]] == ["valid-tracker"]

    async with aiosqlite.connect(storage.db_path) as db:
        tracker_rows = await (
            await db.execute("SELECT name FROM trackers ORDER BY name ASC")
        ).fetchall()
        status_count = await (await db.execute("SELECT COUNT(*) FROM tracker_status")).fetchone()

    assert status_count is not None
    assert [row[0] for row in tracker_rows] == ["valid-tracker"]
    assert status_count[0] == 0



@pytest.mark.asyncio
async def test_create_duplicate_failure(authed_client):
    data = make_tracker_payload("dup")
    authed_client.post("/api/trackers", json=data)

    response = authed_client.post("/api/trackers", json=data)
    assert response.status_code == 400
    assert "名称已存在" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_name", ["", "   "])
async def test_create_tracker_rejects_blank_name(authed_client, invalid_name):
    response = authed_client.post(
        "/api/trackers",
        json=make_tracker_payload(invalid_name),
    )

    assert response.status_code == 422
    assert "name must be a non-empty string" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_name", ["", "   "])
async def test_update_tracker_rejects_blank_name(authed_client, invalid_name):
    authed_client.post("/api/trackers", json=make_tracker_payload("blank-update"))

    response = authed_client.put(
        "/api/trackers/blank-update",
        json=make_tracker_payload(invalid_name),
    )

    assert response.status_code == 422
    assert "name must be a non-empty string" in response.text


@pytest.mark.asyncio
async def test_create_tracker_requires_primary_changelog_source_key_to_match_tracker_source(
    authed_client,
):
    response = authed_client.post(
        "/api/trackers",
        json=make_tracker_payload("invalid-primary", primary_changelog_source_key="missing"),
    )

    assert response.status_code == 422
    assert "primary_changelog_source_key" in response.text


@pytest.mark.asyncio
async def test_create_tracker_preserves_nested_release_channel_types_per_source(
    authed_client,
):
    response = authed_client.post(
        "/api/trackers",
        json=make_tracker_payload(
            "nested-release-channels",
            channels=[],
            sources=[
                {
                    "source_key": "repo",
                    "source_type": "github",
                    "enabled": True,
                    "source_rank": 0,
                    "source_config": {"repo": "owner/nested-release-channels"},
                    "release_channels": [
                        {
                            "release_channel_key": "repo-preview",
                            "name": "stable",
                            "type": "prerelease",
                            "enabled": True,
                        }
                    ],
                },
                {
                    "source_key": "image",
                    "source_type": "container",
                    "enabled": True,
                    "source_rank": 1,
                    "source_config": {
                        "image": "owner/nested-release-channels",
                        "registry": "ghcr.io",
                    },
                    "release_channels": [
                        {
                            "release_channel_key": "image-stable",
                            "name": "stable",
                            "type": "release",
                            "enabled": True,
                        }
                    ],
                },
            ],
        ),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    sources = {source["source_key"]: source for source in body["sources"]}
    detail_response = authed_client.get("/api/trackers/nested-release-channels/config")

    assert sources["repo"]["release_channels"] == [
        {
            "release_channel_key": "repo-preview",
            "name": "stable",
            "type": "prerelease",
            "include_pattern": None,
            "exclude_pattern": None,
            "enabled": True,
        }
    ]
    assert detail_response.status_code == 200, detail_response.text
    detail_sources = {
        source["source_key"]: source for source in detail_response.json()["sources"]
    }
    assert detail_sources["repo"]["release_channels"][0]["type"] == "prerelease"
    assert detail_sources["image"]["release_channels"][0]["release_channel_key"] == "image-stable"
    assert sources["image"]["release_channels"] == [
        {
            "release_channel_key": "image-stable",
            "name": "stable",
            "type": "release",
            "include_pattern": None,
            "exclude_pattern": None,
            "enabled": True,
        }
    ]


@pytest.mark.asyncio
async def test_create_tracker_keeps_flat_runtime_channels_out_of_canonical_sources(
    authed_client,
):
    response = authed_client.post(
        "/api/trackers",
        json=make_tracker_payload(
            "runtime-flat-release-channels",
            primary_changelog_source_key="repo",
            channels=[
                {"name": "stable", "type": "release", "enabled": True},
                {"name": "beta", "type": "prerelease", "enabled": False},
            ],
        ),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    sources = {source["source_key"]: source for source in body["sources"]}

    assert sources["repo"]["release_channels"] == []
    assert sources["image"]["release_channels"] == []
    assert body["channels"] == []
