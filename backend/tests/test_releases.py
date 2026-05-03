import pytest
from datetime import datetime, timedelta, timezone
import aiosqlite
from typing import cast

from releasetracker.config import Channel, TrackerConfig
from releasetracker.models import (
    AggregateTracker,
    Release,
    ReleaseChannel,
    TrackerSource,
    TrackerSourceType,
)
from releasetracker.storage.sqlite import SQLiteStorage
from releasetracker.trackers.helm import HelmTracker


async def _seed_runtime_release(storage: SQLiteStorage, release: Release) -> None:
    if await storage.get_aggregate_tracker(release.tracker_name) is None:
        await storage.save_tracker_config(
            TrackerConfig(
                name=release.tracker_name,
                type=cast(TrackerSourceType, release.tracker_type),
                enabled=True,
                repo=(
                    f"owner/{release.tracker_name}"
                    if release.tracker_type in {"github", "gitea"}
                    else None
                ),
                project=release.tracker_name if release.tracker_type == "gitlab" else None,
                image=(
                    f"ghcr.io/{release.tracker_name}" if release.tracker_type == "container" else None
                ),
                interval=60,
            )
        )
    aggregate_tracker = await storage.get_aggregate_tracker(release.tracker_name)
    assert aggregate_tracker is not None and aggregate_tracker.id is not None
    runtime_source = storage._select_runtime_source(aggregate_tracker)
    assert runtime_source is not None and runtime_source.id is not None
    await storage.save_source_observations(
        aggregate_tracker.id,
        runtime_source,
        [release],
        observed_at=release.published_at,
    )
    identity_key = storage.release_identity_key_for_source(
        release,
        source_type=runtime_source.source_type,
    )
    source_history_id = await storage.get_source_release_history_id(runtime_source.id, identity_key)
    assert source_history_id is not None
    await storage.upsert_tracker_release_history(
        aggregate_tracker.id,
        release,
        primary_source_release_history_id=source_history_id,
        source_type=runtime_source.source_type,
    )
    await storage.refresh_tracker_current_releases(aggregate_tracker.id, [release])


async def _materialize_aggregate_truth_and_projection(
    storage: SQLiteStorage,
    aggregate_tracker: AggregateTracker,
    releases_by_source_key: dict[str, list[Release]],
    *,
    projection_releases: list[Release] | None = None,
):
    assert aggregate_tracker.id is not None

    source_by_key = {source.source_key: source for source in aggregate_tracker.sources}
    releases_by_identity: dict[str, dict[str, Release]] = {}
    source_history_ids_by_identity: dict[str, dict[str, int]] = {}

    for source_key, releases in releases_by_source_key.items():
        source = source_by_key[source_key]
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
            releases_by_identity.setdefault(identity_key, {})[source_key] = release
            source_history_ids_by_identity.setdefault(identity_key, {})[
                source_key
            ] = source_history_id

    default_projection_releases: list[Release] = []
    for identity_key, source_history_ids in source_history_ids_by_identity.items():
        primary_source_key = aggregate_tracker.primary_changelog_source_key
        if primary_source_key not in source_history_ids:
            primary_source_key = min(
                source_history_ids,
                key=lambda key: source_by_key[key].source_rank,
            )
        primary_source = source_by_key[primary_source_key]
        primary_release = releases_by_identity[identity_key][primary_source_key]
        await storage.upsert_tracker_release_history(
            aggregate_tracker.id,
            primary_release,
            primary_source_release_history_id=source_history_ids[primary_source_key],
            supporting_source_release_history_ids=[
                source_history_id
                for source_key, source_history_id in source_history_ids.items()
                if source_key != primary_source_key
            ],
            source_type=primary_source.source_type,
        )
        default_projection_releases.append(primary_release)

    await storage.refresh_tracker_current_releases(
        aggregate_tracker.id,
        projection_releases if projection_releases is not None else default_projection_releases,
    )



async def _fetch_release_surface_counts(
    storage: SQLiteStorage, tracker_name: str
) -> dict[str, int]:
    async with aiosqlite.connect(storage.db_path) as db:
        aggregate_tracker_row = await (
            await db.execute(
                "SELECT id FROM aggregate_trackers WHERE name = ?",
                (tracker_name,),
            )
        ).fetchone()
        assert aggregate_tracker_row is not None
        aggregate_tracker_id = aggregate_tracker_row[0]

        source_release_history_row = await (
            await db.execute(
                """
                SELECT COUNT(*)
                FROM source_release_history srh
                JOIN aggregate_tracker_sources ats ON ats.id = srh.tracker_source_id
                WHERE ats.aggregate_tracker_id = ?
                """,
                (aggregate_tracker_id,),
            )
        ).fetchone()
        tracker_release_history_row = await (
            await db.execute(
                "SELECT COUNT(*) FROM tracker_release_history WHERE aggregate_tracker_id = ?",
                (aggregate_tracker_id,),
            )
        ).fetchone()
        current_release_row = await (
            await db.execute(
                "SELECT COUNT(*) FROM tracker_current_releases WHERE aggregate_tracker_id = ?",
                (aggregate_tracker_id,),
            )
        ).fetchone()
        assert source_release_history_row is not None
        assert tracker_release_history_row is not None
        assert current_release_row is not None

        source_release_history_count = source_release_history_row[0]
        tracker_release_history_count = tracker_release_history_row[0]
        current_release_count = current_release_row[0]

    return {
        "source_release_history": source_release_history_count,
        "tracker_release_history": tracker_release_history_count,
        "tracker_current_releases": current_release_count,
    }


def test_container_and_helm_channel_type_filters_are_ignored_for_stored_releases():
    container_release = Release(
        tracker_name="nginx",
        tracker_type="container",
        version="1.2.0",
        name="1.2.0",
        tag_name="1.2.0",
        url="https://example.com/nginx:1.2.0",
        published_at=datetime.now(timezone.utc),
        prerelease=False,
    )
    helm_release = Release(
        tracker_name="chart",
        tracker_type="helm",
        version="1.2.0",
        name="1.2.0",
        tag_name="1.2.0",
        url="https://example.com/charts/app-1.2.0.tgz",
        published_at=datetime.now(timezone.utc),
        prerelease=False,
    )
    prerelease_channel = ReleaseChannel(
        release_channel_key="canary",
        name="canary",
        type="prerelease",
    )

    assert (
        SQLiteStorage._release_matches_channel(
            container_release,
            prerelease_channel,
            channel_source_type="container",
        )
        is True
    )
    assert (
        SQLiteStorage._release_matches_channel(
            helm_release,
            prerelease_channel,
            channel_source_type="helm",
        )
        is True
    )


@pytest.mark.asyncio
async def test_releases_list(authed_client, storage):
    """测试获取版本列表"""

    # 准备数据
    release = Release(
        tracker_name="test-tracker",
        version="v1.0.0",
        name="Release v1.0.0",
        tag_name="v1.0.0",
        channel_name="stable",
        url="http://example.com/v1.0.0",
        published_at=datetime.now(),
        prerelease=False,
    )
    await _seed_runtime_release(storage, release)

    # 测试获取列表
    response = authed_client.get("/api/releases")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["version"] == "v1.0.0"


@pytest.mark.asyncio
async def test_releases_stats(authed_client, storage):
    """测试获取统计信息"""

    # 确保有数据
    release = Release(
        tracker_name="stats-tracker",
        version="v2.0.0",
        name="Release v2.0.0",
        tag_name="v2.0.0",
        channel_name="stable",
        url="http://example.com/v2.0.0",
        published_at=datetime.now(),
        prerelease=False,
    )
    await _seed_runtime_release(storage, release)

    response = authed_client.get("/api/stats")
    assert response.status_code == 200
    stats = response.json()

    # 检查基本字段结构
    assert "total_releases" in stats
    assert "recent_releases" in stats
    assert "daily_stats" in stats
    assert stats["total_releases"] >= 1


@pytest.mark.asyncio
async def test_releases_stats_groups_naive_published_at_in_system_timezone(authed_client, storage):
    await storage.set_setting("system.timezone", "Asia/Shanghai")
    release = Release(
        tracker_name="naive-timezone-stats-tracker",
        version="v2.2.0",
        name="Release v2.2.0",
        tag_name="v2.2.0",
        channel_name="stable",
        url="http://example.com/v2.2.0",
        published_at=datetime(2026, 5, 6, 18, 6, 37),
        prerelease=False,
    )
    await _seed_runtime_release(storage, release)

    response = authed_client.get("/api/stats")

    assert response.status_code == 200, response.text
    stats = response.json()
    channels_by_date = {
        item["date"]: item["channels"]
        for item in stats["daily_stats"]
        if item["channels"]
    }
    assert channels_by_date["2026-05-06"] == {"stable": 1}
    assert "2026-05-07" not in channels_by_date


@pytest.mark.asyncio
async def test_releases_stats_supports_timezone_aware_created_at(authed_client, storage):
    release = Release(
        tracker_name="aware-stats-tracker",
        version="v2.1.0",
        name="Release v2.1.0",
        tag_name="v2.1.0",
        channel_name="stable",
        url="http://example.com/v2.1.0",
        published_at=datetime.now(timezone.utc),
        prerelease=False,
    )
    await _seed_runtime_release(storage, release)

    async with aiosqlite.connect(storage.db_path) as db:
        await db.execute(
            """
            UPDATE canonical_releases
            SET created_at = ?
            WHERE aggregate_tracker_id = (
                SELECT id FROM aggregate_trackers WHERE name = ?
            )
            """,
            (datetime.now(timezone.utc).isoformat(), "aware-stats-tracker"),
        )
        await db.commit()

    response = authed_client.get("/api/stats")

    assert response.status_code == 200, response.text
    stats = response.json()
    assert stats["total_releases"] >= 1


@pytest.mark.asyncio
async def test_releases_stats_preserve_tracker_defined_channel_categories_when_history_rows_lack_channel_name(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="stats-channel-categories",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/stats-channel-categories"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="repo-stable",
                            name="stable",
                            type="release",
                        ),
                        ReleaseChannel(
                            release_channel_key="repo-beta",
                            name="beta",
                            type="prerelease",
                            include_pattern=r"beta",
                        ),
                        ReleaseChannel(
                            release_channel_key="repo-canary",
                            name="canary",
                            type="prerelease",
                            include_pattern=r"rc",
                        ),
                    ],
                )
            ],
        )
    )

    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "repo": [
                Release(
                    tracker_name="stats-channel-categories",
                    tracker_type="github",
                    version="1.0.0",
                    name="Release 1.0.0",
                    tag_name="v1.0.0",
                    url="http://example.com/releases/v1.0.0",
                    published_at=datetime(2025, 5, 1, 12, 0, 0),
                    prerelease=False,
                ),
                Release(
                    tracker_name="stats-channel-categories",
                    tracker_type="github",
                    version="2.0.0-beta.1",
                    name="Release 2.0.0-beta.1",
                    tag_name="v2.0.0-beta.1",
                    url="http://example.com/releases/v2.0.0-beta.1",
                    published_at=datetime(2025, 5, 2, 12, 0, 0),
                    prerelease=True,
                ),
                Release(
                    tracker_name="stats-channel-categories",
                    tracker_type="github",
                    version="3.0.0-rc.1",
                    name="Release 3.0.0-rc.1",
                    tag_name="v3.0.0-rc.1",
                    url="http://example.com/releases/v3.0.0-rc.1",
                    published_at=datetime(2025, 5, 3, 12, 0, 0),
                    prerelease=True,
                ),
            ]
        },
    )

    response = authed_client.get("/api/stats")

    assert response.status_code == 200, response.text
    stats = response.json()
    assert stats["channel_stats"]["stable"] == 1
    assert stats["channel_stats"]["beta"] == 1
    assert stats["channel_stats"]["canary"] == 1
    assert "prerelease" not in stats["channel_stats"]
    assert stats["release_type_stats"]["stable"] == 1
    assert stats["release_type_stats"]["prerelease"] == 2


@pytest.mark.asyncio
async def test_cleanup_release_history_keeps_latest_per_authoritative_channel_and_current_projection(
    storage,
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="cleanup-channel-retention",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/cleanup-channel-retention"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="repo-stable",
                            name="stable",
                            type="release",
                        ),
                        ReleaseChannel(
                            release_channel_key="repo-beta",
                            name="beta",
                            type="prerelease",
                            include_pattern=r"beta",
                        ),
                    ],
                )
            ],
        )
    )
    releases = [
        Release(
            tracker_name="cleanup-channel-retention",
            tracker_type="github",
            version="1.0.0",
            name="Release 1.0.0",
            tag_name="v1.0.0",
            url="http://example.com/releases/v1.0.0",
            published_at=datetime(2025, 5, 1, 12, 0, 0),
            prerelease=False,
        ),
        Release(
            tracker_name="cleanup-channel-retention",
            tracker_type="github",
            version="2.0.0",
            name="Release 2.0.0",
            tag_name="v2.0.0",
            url="http://example.com/releases/v2.0.0",
            published_at=datetime(2025, 5, 2, 12, 0, 0),
            prerelease=False,
        ),
        Release(
            tracker_name="cleanup-channel-retention",
            tracker_type="github",
            version="3.0.0-beta.1",
            name="Release 3.0.0-beta.1",
            tag_name="v3.0.0-beta.1",
            url="http://example.com/releases/v3.0.0-beta.1",
            published_at=datetime(2025, 5, 3, 12, 0, 0),
            prerelease=True,
        ),
        Release(
            tracker_name="cleanup-channel-retention",
            tracker_type="github",
            version="4.0.0-beta.1",
            name="Release 4.0.0-beta.1",
            tag_name="v4.0.0-beta.1",
            url="http://example.com/releases/v4.0.0-beta.1",
            published_at=datetime(2025, 5, 4, 12, 0, 0),
            prerelease=True,
        ),
    ]
    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {"repo": releases},
        projection_releases=[releases[0]],
    )

    result = await storage.cleanup_release_history(retention_count=1)

    assert result["tracker_release_history_deleted"] == 1
    remaining_releases = await storage.get_tracker_release_history_releases(aggregate_tracker.id)
    assert {release.tag_name for release in remaining_releases} == {
        "v1.0.0",
        "v2.0.0",
        "v4.0.0-beta.1",
    }
    current_releases = await storage.get_tracker_current_releases(aggregate_tracker.id)
    assert [release.tag_name for release in current_releases] == ["v1.0.0"]
    latest_release = await storage.get_latest_release("cleanup-channel-retention")
    assert latest_release is not None
    assert latest_release.tag_name == "v1.0.0"


@pytest.mark.asyncio
async def test_cleanup_release_history_falls_back_to_global_retention_and_deletes_orphan_sources(
    storage,
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="cleanup-global-retention",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/cleanup-global-retention"},
                )
            ],
        )
    )
    releases = [
        Release(
            tracker_name="cleanup-global-retention",
            tracker_type="github",
            version=f"{version}.0.0",
            name=f"Release {version}.0.0",
            tag_name=f"v{version}.0.0",
            url=f"http://example.com/releases/v{version}.0.0",
            published_at=datetime(2025, 5, version, 12, 0, 0),
            prerelease=False,
        )
        for version in range(1, 5)
    ]
    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {"repo": releases},
        projection_releases=[releases[2], releases[3]],
    )

    before_counts = await _fetch_release_surface_counts(storage, "cleanup-global-retention")
    result = await storage.cleanup_release_history(retention_count=2)

    assert before_counts["source_release_history"] == 4
    assert result["tracker_release_history_deleted"] == 2
    assert result["source_release_history_deleted"] == 2
    assert result["source_release_run_observations_deleted"] == 2
    assert result["wal_checkpoint_performed"] is True
    after_counts = await _fetch_release_surface_counts(storage, "cleanup-global-retention")
    assert after_counts == {
        "source_release_history": 2,
        "tracker_release_history": 2,
        "tracker_current_releases": 2,
    }
    remaining_releases = await storage.get_tracker_release_history_releases(aggregate_tracker.id)
    assert [release.tag_name for release in remaining_releases] == ["v4.0.0", "v3.0.0"]


@pytest.mark.asyncio
async def test_latest_releases(authed_client, storage):
    """测试获取最新版本"""

    # 插入数据
    release = Release(
        tracker_name="latest-tracker",
        version="v3.0.0",
        name="Release v3.0.0",
        tag_name="v3.0.0",
        channel_name="stable",
        url="http://example.com/v3.0.0",
        published_at=datetime.now(),
        prerelease=False,
    )
    await _seed_runtime_release(storage, release)

    response = authed_client.get("/api/releases/latest")
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)
    # 之前插入了数据，应该能查到
    should_have_data = len(items) > 0
    assert should_have_data


@pytest.mark.asyncio
async def test_current_release_helpers_return_truth_linked_projection_rows(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="projection-current-helpers",
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
    release_timestamp = datetime(2025, 1, 10, 12, 0, 0)
    repo_release = Release(
        tracker_name="projection-current-helpers",
        tracker_type="github",
        version="1.2.3",
        name="Release 1.2.3",
        tag_name="v1.2.3",
        url="http://example.com/releases/v1.2.3",
        published_at=release_timestamp,
        prerelease=False,
    )
    image_release = Release(
        tracker_name="projection-current-helpers",
        tracker_type="container",
        version="1.2.3",
        name="Image 1.2.3",
        tag_name="1.2.3",
        url="http://example.com/images/1.2.3",
        published_at=release_timestamp,
        prerelease=False,
        commit_sha=None,
    )

    await storage.save_source_observations(
        aggregate_tracker.id,
        repo_source,
        [repo_release],
        observed_at=release_timestamp,
    )
    await storage.save_source_observations(
        aggregate_tracker.id,
        image_source,
        [image_release],
        observed_at=release_timestamp,
    )

    repo_identity_key = storage.release_identity_key_for_source(repo_release, source_type="github")
    repo_source_history_id = await storage.get_source_release_history_id(
        repo_source.id, repo_identity_key
    )
    image_source_history_id = await storage.get_source_release_history_id(
        image_source.id, repo_identity_key
    )

    assert repo_source_history_id is not None
    assert image_source_history_id is not None

    tracker_release_history_id, _ = await storage.upsert_tracker_release_history(
        aggregate_tracker.id,
        repo_release,
        primary_source_release_history_id=repo_source_history_id,
        supporting_source_release_history_ids=[image_source_history_id],
        source_type="github",
    )
    await storage.refresh_tracker_current_releases(aggregate_tracker.id, [repo_release])

    current_rows = await storage.get_tracker_current_release_rows("projection-current-helpers")
    latest_summary = await storage.get_tracker_latest_current_release_summary(
        "projection-current-helpers"
    )

    assert len(current_rows) == 1
    assert current_rows[0]["tracker_release_history_id"] == tracker_release_history_id
    assert current_rows[0]["primary_source"] == {
        "source_key": "repo",
        "source_type": "github",
        "source_release_history_id": repo_source_history_id,
    }
    assert latest_summary is not None
    assert latest_summary["tracker_release_history_id"] == tracker_release_history_id
    assert latest_summary["primary_source"] == current_rows[0]["primary_source"]
    assert latest_summary["version"] == "1.2.3"


@pytest.mark.asyncio
async def test_tracker_current_endpoint_exposes_projection_rows_with_truth_contributions(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-releases",
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

    release_timestamp = datetime(2025, 1, 1, 12, 0, 0)
    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "repo": [
                Release(
                    tracker_name="aggregate-releases",
                    tracker_type="github",
                    version="1.2.3",
                    name="Release 1.2.3",
                    tag_name="v1.2.3",
                    url="http://example.com/releases/v1.2.3",
                    published_at=release_timestamp,
                    body="repo changelog",
                    prerelease=False,
                )
            ],
            "image": [
                Release(
                    tracker_name="aggregate-releases",
                    tracker_type="container",
                    version="1.2.3",
                    name="Image 1.2.3",
                    tag_name="1.2.3",
                    url="http://example.com/images/1.2.3",
                    published_at=release_timestamp,
                    body="image metadata",
                    prerelease=False,
                )
            ],
        },
    )

    response = authed_client.get("/api/trackers/aggregate-releases/current")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tracker"]["name"] == "aggregate-releases"
    assert body["latest_release"]["version"] == "1.2.3"
    assert len(body["matrix"]["rows"]) == 1
    assert body["matrix"]["rows"][0]["version"] == "1.2.3"
    assert body["matrix"]["rows"][0]["primary_source"]["source_key"] == "repo"
    assert {item["source_key"] for item in body["matrix"]["rows"][0]["source_contributions"]} == {
        "repo",
        "image",
    }


@pytest.mark.asyncio
async def test_latest_current_summary_prefers_newer_folded_stable_version_over_older_numeric_tag(
    storage,
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="folded-latest-summary",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/folded-latest-summary"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="repo-stable",
                            name="stable",
                            type="release",
                        )
                    ],
                ),
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=1,
                    source_config={"image": "owner/folded-latest-summary", "registry": "ghcr.io"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="image-stable",
                            name="stable",
                            type="release",
                        )
                    ],
                ),
            ],
        )
    )
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="folded-latest-summary",
            type="github",
            enabled=True,
            repo="owner/folded-latest-summary",
            interval=60,
            version_sort_mode="published_at",
            channels=[Channel(name="stable", type="release", exclude_pattern=".*rc.*")],
        )
    )

    github_release = Release(
        tracker_name="folded-latest-summary",
        tracker_type="github",
        version="v2.5.3",
        name="Release v2.5.3",
        tag_name="v2.5.3",
        url="https://example.com/releases/v2.5.3",
        published_at=datetime(2026, 4, 20, 19, 35, 6),
        prerelease=False,
    )
    docker_alias_release = Release(
        tracker_name="folded-latest-summary",
        tracker_type="container",
        version="2.5.3",
        name="2.5.3",
        tag_name="latest",
        url="https://ghcr.io/owner/folded-latest-summary:latest",
        published_at=datetime(2026, 4, 22, 7, 30, 24),
        prerelease=False,
        commit_sha="sha256:digest-253",
    )
    older_docker_release = Release(
        tracker_name="folded-latest-summary",
        tracker_type="container",
        version="2.5.2",
        name="2.5.2",
        tag_name="2.5.2",
        url="https://ghcr.io/owner/folded-latest-summary:2.5.2",
        published_at=datetime(2026, 4, 22, 7, 30, 23),
        prerelease=False,
        commit_sha="sha256:digest-252",
    )

    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "repo": [github_release],
            "image": [docker_alias_release, older_docker_release],
        },
        projection_releases=[docker_alias_release, older_docker_release, github_release],
    )

    latest_summary = await storage.get_tracker_latest_current_release_summary(
        "folded-latest-summary"
    )

    assert latest_summary is not None
    assert latest_summary["version"] == "2.5.3"
    assert latest_summary["release"].version == "2.5.3"


@pytest.mark.asyncio
async def test_docker_folded_alias_identity_prefers_concrete_tag_for_display(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="openbao-display",
            primary_changelog_source_key="image",
            sources=[
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=0,
                    source_config={"image": "openbao/openbao", "registry": "ghcr.io"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="image-stable",
                            name="stable",
                            type="release",
                        )
                    ],
                )
            ],
        )
    )
    image_source = aggregate_tracker.sources[0]
    assert aggregate_tracker.id is not None
    assert image_source.id is not None

    digest = "sha256:fdc6da21ca6963560c32336fd7feb9cf2d5e52668f1a1647205a4b41171f0806"
    releases = [
        Release(
            tracker_name="openbao-display",
            tracker_type="container",
            version="2.5.3",
            name="latest",
            tag_name="latest",
            url="https://ghcr.io/openbao/openbao:latest",
            published_at=datetime(2026, 4, 22, 7, 29, 57),
            prerelease=False,
            commit_sha=digest,
        ),
        Release(
            tracker_name="openbao-display",
            tracker_type="container",
            version="2.5.3",
            name="2.5.3",
            tag_name="2.5.3",
            url="https://ghcr.io/openbao/openbao:2.5.3",
            published_at=datetime(2026, 4, 22, 7, 29, 58),
            prerelease=False,
            commit_sha=digest,
        ),
        Release(
            tracker_name="openbao-display",
            tracker_type="container",
            version="2.5.3",
            name="2.5",
            tag_name="2.5",
            url="https://ghcr.io/openbao/openbao:2.5",
            published_at=datetime(2026, 4, 22, 7, 29, 59),
            prerelease=False,
            commit_sha=digest,
        ),
    ]

    await storage.save_source_observations(
        aggregate_tracker.id,
        image_source,
        releases,
        observed_at=datetime(2026, 4, 22, 7, 30, 0),
    )

    source_history_releases = await storage.get_source_release_history_releases_by_source(
        image_source.id
    )
    assert len(source_history_releases) == 1
    assert source_history_releases[0].version == "2.5.3"
    assert source_history_releases[0].tag_name == "2.5.3"
    assert source_history_releases[0].url == "https://ghcr.io/openbao/openbao:2.5.3"

    deduped_releases = storage.dedupe_releases_by_immutable_identity(releases)
    assert len(deduped_releases) == 1
    assert deduped_releases[0].tag_name == "2.5.3"

    identity_key = storage.release_identity_key_for_source(
        deduped_releases[0],
        source_type=image_source.source_type,
    )
    source_history_id = await storage.get_source_release_history_id(image_source.id, identity_key)
    assert source_history_id is not None
    await storage.upsert_tracker_release_history(
        aggregate_tracker.id,
        deduped_releases[0],
        primary_source_release_history_id=source_history_id,
        source_type=image_source.source_type,
    )
    await storage.refresh_tracker_current_releases(aggregate_tracker.id, deduped_releases)

    current_releases = await storage.get_tracker_current_releases(aggregate_tracker.id)
    assert len(current_releases) == 1
    assert current_releases[0].version == "2.5.3"
    assert current_releases[0].tag_name == "2.5.3"
    assert current_releases[0].url == "https://ghcr.io/openbao/openbao:2.5.3"


@pytest.mark.asyncio
async def test_docker_folded_alias_identity_prefers_plain_tag_over_build_suffix(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="image-build-suffix-display",
            primary_changelog_source_key="image",
            sources=[
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=0,
                    source_config={"image": "library/demo", "registry": "docker.io"},
                )
            ],
        )
    )
    image_source = aggregate_tracker.sources[0]
    assert aggregate_tracker.id is not None
    assert image_source.id is not None

    digest = "sha256:fdc6da21ca6963560c32336fd7feb9cf2d5e52668f1a1647205a4b41171f0806"
    releases = [
        Release(
            tracker_name="image-build-suffix-display",
            tracker_type="container",
            version=tag,
            name=tag,
            tag_name=tag,
            url=f"https://docker.io/library/demo:{tag}",
            published_at=datetime(2026, 4, 22, 7, index, 0),
            prerelease=False,
            commit_sha=digest,
        )
        for index, tag in enumerate(
            ["latest", "1.0", "1", "1.0.0", "1.0.0-20260420-d8a86b"],
            start=1,
        )
    ]

    await storage.save_source_observations(
        aggregate_tracker.id,
        image_source,
        releases,
        observed_at=datetime(2026, 4, 22, 7, 30, 0),
    )

    source_history_releases = await storage.get_source_release_history_releases_by_source(
        image_source.id
    )
    assert len(source_history_releases) == 1
    assert source_history_releases[0].version == "1.0.0"
    assert source_history_releases[0].tag_name == "1.0.0"
    assert source_history_releases[0].url == "https://docker.io/library/demo:1.0.0"

    deduped_releases = storage.dedupe_releases_by_immutable_identity(releases)
    assert len(deduped_releases) == 1
    assert deduped_releases[0].version == "1.0.0"
    assert deduped_releases[0].tag_name == "1.0.0"


@pytest.mark.asyncio
async def test_docker_different_tags_with_same_digest_share_one_immutable_identity(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="docker-digest-aliases",
            primary_changelog_source_key="image",
            sources=[
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=0,
                    source_config={"image": "library/debian", "registry": "docker.io"},
                )
            ],
        )
    )
    image_source = aggregate_tracker.sources[0]
    assert aggregate_tracker.id is not None
    assert image_source.id is not None

    digest = "sha256:fdc6da21ca6963560c32336fd7feb9cf2d5e52668f1a1647205a4b41171f0806"
    releases = [
        Release(
            tracker_name="docker-digest-aliases",
            tracker_type="container",
            version=tag,
            name=tag,
            tag_name=tag,
            url=f"https://docker.io/library/debian:{tag}",
            published_at=datetime(2026, 4, 22, 7, index, 0),
            prerelease=False,
            commit_sha=digest,
        )
        for index, tag in enumerate(["latest", "alpine", "trixie"], start=1)
    ]

    await storage.save_source_observations(
        aggregate_tracker.id,
        image_source,
        releases,
        observed_at=datetime(2026, 4, 22, 7, 30, 0),
    )

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        history_rows = await (
            await db.execute(
                "SELECT identity_key, immutable_key, digest FROM source_release_history WHERE tracker_source_id = ?",
                (image_source.id,),
            )
        ).fetchall()

    assert len(history_rows) == 1
    assert history_rows[0]["identity_key"] == digest
    assert history_rows[0]["immutable_key"] == digest
    assert history_rows[0]["digest"] == digest
    assert len(storage.dedupe_releases_by_immutable_identity(releases)) == 1


@pytest.mark.asyncio
async def test_tracker_release_history_upsert_replaces_stale_primary_source_link(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="primary-link-refresh",
            primary_changelog_source_key="new-image",
            sources=[
                TrackerSource(
                    source_key="old-image",
                    source_type="container",
                    source_rank=0,
                    source_config={"image": "old/project", "registry": "ghcr.io"},
                ),
                TrackerSource(
                    source_key="new-image",
                    source_type="container",
                    source_rank=1,
                    source_config={"image": "new/project", "registry": "ghcr.io"},
                ),
            ],
        )
    )
    old_source = next(
        source for source in aggregate_tracker.sources if source.source_key == "old-image"
    )
    new_source = next(
        source for source in aggregate_tracker.sources if source.source_key == "new-image"
    )
    assert aggregate_tracker.id is not None
    assert old_source.id is not None
    assert new_source.id is not None

    digest = "sha256:fdc6da21ca6963560c32336fd7feb9cf2d5e52668f1a1647205a4b41171f0806"
    old_release = Release(
        tracker_name="primary-link-refresh",
        tracker_type="container",
        version="2.5.3",
        name="2.5",
        tag_name="2.5",
        url="https://ghcr.io/old/project:2.5",
        published_at=datetime(2026, 4, 22, 7, 29, 59),
        prerelease=False,
        commit_sha=digest,
    )
    new_release = Release(
        tracker_name="primary-link-refresh",
        tracker_type="container",
        version="2.5.3",
        name="2.5.3",
        tag_name="2.5.3",
        url="https://ghcr.io/new/project:2.5.3",
        published_at=datetime(2026, 4, 22, 7, 30, 0),
        prerelease=False,
        commit_sha=digest,
    )

    await storage.save_source_observations(
        aggregate_tracker.id,
        old_source,
        [old_release],
        observed_at=datetime(2026, 4, 22, 7, 30, 1),
    )
    await storage.save_source_observations(
        aggregate_tracker.id,
        new_source,
        [new_release],
        observed_at=datetime(2026, 4, 22, 7, 30, 2),
    )

    old_identity = storage.release_identity_key_for_source(old_release, source_type="container")
    new_identity = storage.release_identity_key_for_source(new_release, source_type="container")
    assert old_identity == new_identity
    old_history_id = await storage.get_source_release_history_id(old_source.id, old_identity)
    new_history_id = await storage.get_source_release_history_id(new_source.id, new_identity)
    assert old_history_id is not None
    assert new_history_id is not None

    tracker_release_history_id, _ = await storage.upsert_tracker_release_history(
        aggregate_tracker.id,
        old_release,
        primary_source_release_history_id=old_history_id,
        source_type="container",
    )
    await storage.upsert_tracker_release_history(
        aggregate_tracker.id,
        new_release,
        primary_source_release_history_id=new_history_id,
        source_type="container",
    )

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        tracker_row = await (
            await db.execute(
                "SELECT primary_source_release_history_id FROM tracker_release_history WHERE id = ?",
                (tracker_release_history_id,),
            )
        ).fetchone()
        link_rows = await (
            await db.execute(
                """
                SELECT source_release_history_id, contribution_kind
                FROM tracker_release_history_sources
                WHERE tracker_release_history_id = ?
                ORDER BY source_release_history_id
                """,
                (tracker_release_history_id,),
            )
        ).fetchall()

    assert tracker_row is not None
    assert tracker_row["primary_source_release_history_id"] == new_history_id
    assert {row["source_release_history_id"]: row["contribution_kind"] for row in link_rows} == {
        old_history_id: "supporting",
        new_history_id: "primary",
    }


@pytest.mark.asyncio
async def test_tracker_current_endpoint_rejects_unknown_channel_query(authed_client, storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="current-channel-contract",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/current-channel-contract"},
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
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="current-channel-contract",
            type="github",
            enabled=True,
            repo="owner/current-channel-contract",
            interval=60,
            channels=[Channel(name="stable", type="release", exclude_pattern=".*rc.*")],
        )
    )

    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "repo": [
                Release(
                    tracker_name="current-channel-contract",
                    tracker_type="github",
                    version="1.0.0",
                    name="Release 1.0.0",
                    tag_name="v1.0.0",
                    url="http://example.com/releases/v1.0.0",
                    published_at=datetime(2025, 1, 2, 12, 0, 0),
                    prerelease=False,
                )
            ]
        },
    )

    invalid_channel_response = authed_client.get(
        "/api/trackers/current-channel-contract/current?channel=missing"
    )
    legacy_name_response = authed_client.get(
        "/api/trackers/current-channel-contract/current?channel=stable"
    )
    valid_key_response = authed_client.get(
        "/api/trackers/current-channel-contract/current?channel=repo-stable"
    )

    assert invalid_channel_response.status_code == 400, invalid_channel_response.text
    assert legacy_name_response.status_code == 400, legacy_name_response.text
    assert valid_key_response.status_code == 200, valid_key_response.text


@pytest.mark.asyncio
async def test_tracker_current_endpoint_preserves_channel_relationship_when_disabled_channel_precedes_enabled_one(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="current-channel-relationship",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/current-channel-relationship"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="repo-beta",
                            name="beta",
                            type="prerelease",
                            enabled=False,
                        ),
                        ReleaseChannel(
                            release_channel_key="repo-stable",
                            name="stable",
                            type="release",
                        ),
                    ],
                )
            ],
        )
    )
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="current-channel-relationship",
            type="github",
            enabled=True,
            repo="owner/current-channel-relationship",
            interval=60,
            channels=[
                Channel(name="beta", type="prerelease", enabled=False),
                Channel(name="stable", type="release"),
            ],
        )
    )

    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "repo": [
                Release(
                    tracker_name="current-channel-relationship",
                    tracker_type="github",
                    version="1.0.0",
                    name="Release 1.0.0",
                    tag_name="v1.0.0",
                    url="http://example.com/releases/v1.0.0",
                    published_at=datetime(2025, 1, 3, 12, 0, 0),
                    prerelease=False,
                )
            ]
        },
    )

    response = authed_client.get("/api/trackers/current-channel-relationship/current")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["matrix"]["columns"][0]["channel_key"] == "beta"
    assert body["matrix"]["columns"][1]["channel_key"] == "stable"
    assert body["matrix"]["rows"][0]["channel_keys"] == ["stable"]
    assert body["matrix"]["rows"][0]["matched_channel_count"] == 1
    assert body["matrix"]["rows"][0]["cells"]["stable"] == {
        "channel_key": "stable",
        "channel_type": "release",
        "selected": True,
    }
    assert "beta" not in body["matrix"]["rows"][0]["cells"]


@pytest.mark.asyncio
async def test_tracker_current_endpoint_aligns_helm_with_repo_and_container_by_app_version(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-helm-endpoint",
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
                TrackerSource(
                    source_key="helm",
                    source_type="helm",
                    source_rank=2,
                    source_config={"repo": "https://charts.example.com", "chart": "demo"},
                ),
            ],
        )
    )

    release_timestamp = datetime(2025, 4, 1, 12, 0, 0)

    helm_tracker = HelmTracker(
        name="aggregate-helm-endpoint",
        repo="https://charts.example.com",
        chart="demo",
    )
    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "repo": [
                Release(
                    tracker_name="aggregate-helm-endpoint",
                    tracker_type="github",
                    version="1.2.3",
                    name="Release 1.2.3",
                    tag_name="v1.2.3",
                    url="http://example.com/releases/v1.2.3",
                    published_at=release_timestamp,
                    prerelease=False,
                )
            ],
            "image": [
                Release(
                    tracker_name="aggregate-helm-endpoint",
                    tracker_type="container",
                    version="1.2.3",
                    name="Image 1.2.3",
                    tag_name="1.2.3",
                    url="http://example.com/images/1.2.3",
                    published_at=release_timestamp,
                    prerelease=False,
                )
            ],
            "helm": [
                helm_tracker._parse_chart_version(
                    {
                        "version": "1.2.3-chart.1",
                        "appVersion": "1.2.3",
                        "created": release_timestamp.isoformat(),
                    }
                ),
                helm_tracker._parse_chart_version(
                    {
                        "version": "1.2.3-chart.2",
                        "appVersion": "1.2.3",
                        "created": (release_timestamp + timedelta(minutes=1)).isoformat(),
                    }
                ),
            ],
        },
    )

    current_response = authed_client.get("/api/trackers/aggregate-helm-endpoint/current")

    assert current_response.status_code == 200, current_response.text

    current_rows = current_response.json()["matrix"]["rows"]
    assert len(current_rows) == 3
    repo_row = next(
        row
        for row in current_rows
        if row["primary_source"] is not None and row["primary_source"]["source_key"] == "repo"
    )
    assert repo_row["version"] == "1.2.3"
    assert {item["source_key"] for item in repo_row["source_contributions"]} == {"repo", "image"}

    helm_rows = [
        row
        for row in current_rows
        if row["primary_source"] is not None and row["primary_source"]["source_key"] == "helm"
    ]
    assert len(helm_rows) == 2
    assert {row["version"] for row in helm_rows} == {"1.2.3"}
    assert {
        contribution["app_version"]
        for row in helm_rows
        for contribution in row["source_contributions"]
    } == {"1.2.3"}
    assert {
        contribution["chart_version"]
        for row in helm_rows
        for contribution in row["source_contributions"]
    } == {"1.2.3-chart.1", "1.2.3-chart.2"}


@pytest.mark.asyncio
async def test_release_history_and_latest_current_endpoints_have_distinct_semantics(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-history",
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
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="aggregate-history",
            type="github",
            enabled=True,
            repo="owner/project",
            interval=60,
        )
    )

    release_timestamp = datetime(2025, 3, 1, 12, 0, 0)
    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "repo": [
                Release(
                    tracker_name="aggregate-history",
                    tracker_type="github",
                    version="2.0.0",
                    name="Release 2.0.0",
                    tag_name="v2.0.0",
                    url="http://example.com/releases/v2.0.0",
                    published_at=release_timestamp,
                    body="canonical body",
                    prerelease=False,
                )
            ]
        },
    )


    releases_response = authed_client.get("/api/releases")
    latest_response = authed_client.get("/api/releases/latest")
    tracker_current_response = authed_client.get("/api/trackers/aggregate-history/current")
    stats_response = authed_client.get("/api/stats")

    assert releases_response.status_code == 200, releases_response.text
    assert latest_response.status_code == 200, latest_response.text
    assert tracker_current_response.status_code == 200, tracker_current_response.text
    assert stats_response.status_code == 200, stats_response.text

    releases = releases_response.json()["items"]
    latest_releases = latest_response.json()
    tracker_current = tracker_current_response.json()
    stats = stats_response.json()

    assert {(item["tracker_name"], item["version"]) for item in releases} == {
        ("aggregate-history", "2.0.0")
    }
    aggregate_release = next(
        item for item in releases if item["tracker_name"] == "aggregate-history"
    )
    assert aggregate_release["body"] == "canonical body"
    assert aggregate_release["tag_name"] == "v2.0.0"

    assert {(item["tracker_name"], item["version"]) for item in latest_releases} == {
        ("aggregate-history", "2.0.0")
    }
    assert [item["version"] for item in tracker_current["matrix"]["rows"]] == ["2.0.0"]
    assert stats["total_releases"] == 1
    assert stats["total_trackers"] == 1


@pytest.mark.asyncio
async def test_history_endpoint_keeps_truth_rows_while_current_endpoints_use_projection_winner(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-mixed-history",
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
                TrackerSource(
                    source_key="helm",
                    source_type="helm",
                    source_rank=2,
                    source_config={"repo": "https://charts.example.com", "chart": "demo"},
                ),
            ],
        )
    )

    repo_timestamp = datetime(2025, 4, 1, 12, 0, 0)
    image_timestamp = datetime(2025, 4, 2, 12, 0, 0)
    helm_timestamp = datetime(2025, 4, 3, 12, 0, 0)
    repo_release = Release(
        tracker_name="aggregate-mixed-history",
        tracker_type="github",
        version="2.0.0",
        name="Release 2.0.0",
        tag_name="v2.0.0",
        url="http://example.com/releases/v2.0.0",
        published_at=repo_timestamp,
        prerelease=False,
    )
    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "repo": [repo_release],
            "image": [
                Release(
                    tracker_name="aggregate-mixed-history",
                    tracker_type="container",
                    version="3.0.0",
                    name="Image 3.0.0",
                    tag_name="3.0.0",
                    url="http://example.com/images/3.0.0",
                    published_at=image_timestamp,
                    prerelease=False,
                )
            ],
            "helm": [
                Release(
                    tracker_name="aggregate-mixed-history",
                    tracker_type="helm",
                    version="3.1.0",
                    app_version="3.1.0",
                    chart_version="3.1.0-chart.1",
                    name="Chart 3.1.0",
                    tag_name="3.1.0-chart.1",
                    url="http://example.com/charts/3.1.0-chart.1",
                    published_at=helm_timestamp,
                    prerelease=False,
                )
            ],
        },
        projection_releases=[repo_release],
    )

    releases_response = authed_client.get("/api/releases?tracker=aggregate-mixed-history")
    tracker_history_response = authed_client.get(
        "/api/trackers/aggregate-mixed-history/releases/history"
    )
    tracker_current_response = authed_client.get("/api/trackers/aggregate-mixed-history/current")
    latest_response = authed_client.get("/api/releases/latest")

    assert releases_response.status_code == 200, releases_response.text
    assert tracker_history_response.status_code == 200, tracker_history_response.text
    assert tracker_current_response.status_code == 200, tracker_current_response.text
    assert latest_response.status_code == 200, latest_response.text

    releases = releases_response.json()["items"]
    tracker_history = tracker_history_response.json()["items"]
    tracker_current = tracker_current_response.json()["matrix"]["rows"]
    latest_releases = [
        item for item in latest_response.json() if item["tracker_name"] == "aggregate-mixed-history"
    ]

    assert [item["version"] for item in releases] == ["3.1.0", "3.0.0", "2.0.0"]
    assert [item["version"] for item in tracker_history] == ["3.1.0", "3.0.0", "2.0.0"]
    assert [item["version"] for item in tracker_current] == ["2.0.0"]
    assert [item["version"] for item in latest_releases] == ["2.0.0"]


@pytest.mark.asyncio
async def test_excluded_releases_are_hidden_from_tracker_version_views_but_kept_in_global_history(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="excluded-version-views",
            primary_changelog_source_key="image",
            sources=[
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=0,
                    source_config={"image": "jenkins/inbound-agent", "registry": "docker.io"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="image-stable",
                            name="stable",
                            type="release",
                            include_pattern=r".*",
                            exclude_pattern=r"^.*-.*$",
                        )
                    ],
                )
            ],
        )
    )

    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="excluded-version-views",
            type="container",
            enabled=True,
            image="jenkins/inbound-agent",
            registry="docker.io",
            interval=60,
        )
    )

    excluded_release = Release(
        tracker_name="excluded-version-views",
        tracker_type="container",
        version="3256.3258.v858f3c9a_f69d-1",
        name="3256.3258.v858f3c9a_f69d-1",
        tag_name="3256.3258.v858f3c9a_f69d-1",
        url="http://example.com/3256.3258.v858f3c9a_f69d-1",
        published_at=datetime(2026, 4, 28, 23, 1, 19),
        prerelease=False,
        commit_sha="sha256:latest",
    )

    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {"image": [excluded_release]},
    )

    releases_response = authed_client.get("/api/releases?tracker=excluded-version-views")
    tracker_history_response = authed_client.get(
        "/api/trackers/excluded-version-views/releases/history"
    )
    tracker_current_response = authed_client.get("/api/trackers/excluded-version-views/current")
    latest_response = authed_client.get("/api/releases/latest")

    assert releases_response.status_code == 200, releases_response.text
    assert tracker_history_response.status_code == 200, tracker_history_response.text
    assert tracker_current_response.status_code == 200, tracker_current_response.text
    assert latest_response.status_code == 200, latest_response.text

    releases = releases_response.json()["items"]
    tracker_history = tracker_history_response.json()["items"]
    tracker_current = tracker_current_response.json()
    latest_releases = [
        item for item in latest_response.json() if item["tracker_name"] == "excluded-version-views"
    ]

    assert [item["version"] for item in releases] == ["3256.3258.v858f3c9a_f69d-1"]
    assert [item["version"] for item in tracker_history] == []
    assert tracker_current["latest_release"] is None
    assert tracker_current["status"]["last_version"] is None
    assert tracker_current["matrix"]["rows"] == []
    assert latest_releases == []


@pytest.mark.asyncio
async def test_history_endpoint_keeps_container_truth_rows_for_container_only_tracker(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-container-only-history",
            primary_changelog_source_key="image",
            sources=[
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=0,
                    source_config={"image": "owner/project", "registry": "ghcr.io"},
                ),
                TrackerSource(
                    source_key="helm",
                    source_type="helm",
                    source_rank=1,
                    source_config={"repo": "https://charts.example.com", "chart": "demo"},
                ),
            ],
        )
    )

    image_timestamp = datetime(2025, 5, 1, 12, 0, 0)
    helm_timestamp = datetime(2025, 5, 2, 12, 0, 0)
    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "image": [
                Release(
                    tracker_name="aggregate-container-only-history",
                    tracker_type="container",
                    version="4.0.0",
                    name="Image 4.0.0",
                    tag_name="4.0.0",
                    url="http://example.com/images/4.0.0",
                    published_at=image_timestamp,
                    prerelease=False,
                )
            ],
            "helm": [
                Release(
                    tracker_name="aggregate-container-only-history",
                    tracker_type="helm",
                    version="4.1.0",
                    app_version="4.1.0",
                    chart_version="4.1.0-chart.2",
                    name="Chart 4.1.0",
                    tag_name="4.1.0-chart.2",
                    url="http://example.com/charts/4.1.0-chart.2",
                    published_at=helm_timestamp,
                    prerelease=False,
                )
            ],
        },
    )

    releases_response = authed_client.get("/api/releases?tracker=aggregate-container-only-history")

    assert releases_response.status_code == 200, releases_response.text
    releases = releases_response.json()["items"]

    assert {item["version"] for item in releases} == {"4.0.0", "4.1.0"}


@pytest.mark.asyncio
async def test_releases_include_history_query_param_fails_with_migration_guidance(
    authed_client, storage
):
    release = Release(
        tracker_name="removed-mode-tracker",
        version="v1.0.0",
        name="Release v1.0.0",
        tag_name="v1.0.0",
        channel_name="stable",
        url="http://example.com/v1.0.0",
        published_at=datetime.now(),
        prerelease=False,
    )
    await _seed_runtime_release(storage, release)

    true_response = authed_client.get("/api/releases?include_history=true")
    false_response = authed_client.get("/api/releases?include_history=false")

    assert true_response.status_code == 400, true_response.text
    assert false_response.status_code == 400, false_response.text
    assert "/api/trackers/{tracker_name}/current" in true_response.json()["detail"]
    assert "/api/trackers/{tracker_name}/current" in false_response.json()["detail"]


@pytest.mark.asyncio
async def test_releases_history_orders_by_append_created_at_and_paginates_after_filters(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="append-ordered-history",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/append-ordered-history"},
                )
            ],
        )
    )
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="append-ordered-history",
            type="github",
            enabled=True,
            repo="owner/append-ordered-history",
            interval=60,
        )
    )

    first_insert = Release(
        tracker_name="append-ordered-history",
        tracker_type="github",
        version="3.0.0",
        name="Release 3.0.0",
        tag_name="v3.0.0",
        url="http://example.com/releases/v3.0.0",
        published_at=datetime(2025, 1, 30, 12, 0, 0),
        prerelease=False,
    )
    second_insert = Release(
        tracker_name="append-ordered-history",
        tracker_type="github",
        version="1.0.0",
        name="Release 1.0.0",
        tag_name="v1.0.0",
        url="http://example.com/releases/v1.0.0",
        published_at=datetime(2025, 1, 1, 12, 0, 0),
        prerelease=False,
    )
    third_insert = Release(
        tracker_name="append-ordered-history",
        tracker_type="github",
        version="2.0.0-alpha.1",
        name="Release 2.0.0-alpha.1",
        tag_name="v2.0.0-alpha.1",
        url="http://example.com/releases/v2.0.0-alpha.1",
        published_at=datetime(2025, 1, 15, 12, 0, 0),
        prerelease=True,
    )

    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "repo": [first_insert, second_insert, third_insert],
        },
    )

    page_response = authed_client.get("/api/releases?tracker=append-ordered-history&skip=0&limit=2")
    filtered_response = authed_client.get(
        "/api/releases?tracker=append-ordered-history&prerelease=false&skip=0&limit=1"
    )

    assert page_response.status_code == 200, page_response.text
    assert filtered_response.status_code == 200, filtered_response.text

    page_payload = page_response.json()
    filtered_payload = filtered_response.json()

    assert page_payload["total"] == 3
    assert [item["version"] for item in page_payload["items"]] == ["2.0.0-alpha.1", "1.0.0"]

    assert filtered_payload["total"] == 2
    assert [item["version"] for item in filtered_payload["items"]] == ["1.0.0"]


@pytest.mark.asyncio
async def test_releases_history_canonical_channel_selector_requires_tracker_and_valid_tracker_channel(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="history-channel-contract",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/history-channel-contract"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="repo-stable",
                            name="stable",
                            type="release",
                        ),
                        ReleaseChannel(
                            release_channel_key="repo-prerelease",
                            name="prerelease",
                            type="prerelease",
                        ),
                    ],
                )
            ],
        )
    )
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="history-channel-contract",
            type="github",
            enabled=True,
            repo="owner/history-channel-contract",
            interval=60,
            channels=[
                Channel(name="stable", type="release"),
                Channel(name="prerelease", type="prerelease"),
            ],
        )
    )

    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "repo": [
                Release(
                    tracker_name="history-channel-contract",
                    tracker_type="github",
                    version="1.0.0",
                    name="Release 1.0.0",
                    tag_name="v1.0.0",
                    url="http://example.com/releases/v1.0.0",
                    published_at=datetime(2025, 2, 1, 12, 0, 0),
                    prerelease=False,
                ),
                Release(
                    tracker_name="history-channel-contract",
                    tracker_type="github",
                    version="2.0.0-alpha.1",
                    name="Release 2.0.0-alpha.1",
                    tag_name="v2.0.0-alpha.1",
                    url="http://example.com/releases/v2.0.0-alpha.1",
                    published_at=datetime(2025, 2, 2, 12, 0, 0),
                    prerelease=True,
                ),
            ]
        },
    )

    missing_tracker_response = authed_client.get("/api/releases?channel=stable")
    invalid_channel_response = authed_client.get(
        "/api/releases?tracker=history-channel-contract&channel=missing"
    )
    legacy_name_response = authed_client.get(
        "/api/releases?tracker=history-channel-contract&channel=stable"
    )
    stable_response = authed_client.get(
        "/api/releases?tracker=history-channel-contract&channel=repo-stable"
    )
    prerelease_response = authed_client.get(
        "/api/releases?tracker=history-channel-contract&channel=repo-prerelease"
    )

    assert missing_tracker_response.status_code == 400, missing_tracker_response.text
    assert invalid_channel_response.status_code == 400, invalid_channel_response.text
    assert legacy_name_response.status_code == 400, legacy_name_response.text
    assert stable_response.status_code == 200, stable_response.text
    assert prerelease_response.status_code == 200, prerelease_response.text
    assert [item["version"] for item in stable_response.json()["items"]] == ["1.0.0"]
    assert [item["version"] for item in prerelease_response.json()["items"]] == ["2.0.0-alpha.1"]


@pytest.mark.asyncio
async def test_latest_releases_preserve_projected_row_channel_name(authed_client, storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="latest-channel-name",
            primary_changelog_source_key="container",
            sources=[
                TrackerSource(
                    source_key="container",
                    source_type="container",
                    source_rank=0,
                    source_config={"image": "fawney19/aether", "registry": "ghcr.io"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="container-stable",
                            name="stable",
                            exclude_pattern=".*rc.*",
                        ),
                        ReleaseChannel(
                            release_channel_key="container-prerelease",
                            name="prerelease",
                            include_pattern=".*rc.*",
                        ),
                    ],
                )
            ],
        )
    )
    release = Release(
        tracker_name="latest-channel-name",
        tracker_type="container",
        version="0.7.0-rc22",
        name="0.7.0-rc22",
        tag_name="0.7.0-rc22",
        url="http://example.com/aether:0.7.0-rc22",
        published_at=datetime(2026, 5, 1, 12, 0, 0),
        prerelease=False,
        channel_name="prerelease",
    )
    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {"container": [release]},
    )

    response = authed_client.get("/api/releases/latest?tracker=latest-channel-name")

    assert response.status_code == 200, response.text
    item = response.json()[0]
    assert item["version"] == "0.7.0-rc22"
    assert item["channel_name"] == "prerelease"


@pytest.mark.asyncio
async def test_release_history_channel_type_uses_stored_channel_name_before_inference(authed_client, storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aether-channel-type",
            primary_changelog_source_key="container",
            sources=[
                TrackerSource(
                    source_key="container",
                    source_type="container",
                    source_rank=0,
                    source_config={"image": "fawney19/aether", "registry": "reg.example.com"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="container-stable",
                            name="stable",
                            type="release",
                        ),
                        ReleaseChannel(
                            release_channel_key="container-canary",
                            name="canary",
                            type="prerelease",
                            include_pattern=".*rc.*",
                        ),
                    ],
                )
            ],
        )
    )

    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "container": [
                Release(
                    tracker_name="aether-channel-type",
                    tracker_type="container",
                    version="0.7.0-rc22",
                    name="0.7.0-rc22",
                    tag_name="0.7.0-rc22",
                    url="http://example.com/aether:0.7.0-rc22",
                    published_at=datetime(2026, 5, 1, 12, 0, 0),
                    prerelease=False,
                    channel_name="canary",
                ),
            ]
        },
    )

    response = authed_client.get("/api/releases?tracker=aether-channel-type")

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["version"] == "0.7.0-rc22"
    assert item["channel_name"] == "canary"
    assert item["channel_type"] == "prerelease"


@pytest.mark.asyncio
async def test_releases_redesign_only_response_shape(authed_client, storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="release-redesign-shape",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/release-redesign-shape"},
                )
            ],
        )
    )
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="release-redesign-shape",
            type="github",
            enabled=True,
            repo="owner/release-redesign-shape",
            interval=60,
        )
    )

    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "repo": [
                Release(
                    tracker_name="release-redesign-shape",
                    tracker_type="github",
                    version="1.0.0",
                    name="Release 1.0.0",
                    tag_name="v1.0.0",
                    url="http://example.com/releases/v1.0.0",
                    published_at=datetime(2025, 2, 5, 12, 0, 0),
                    prerelease=False,
                )
            ]
        },
    )

    history_response = authed_client.get("/api/releases?tracker=release-redesign-shape")
    latest_response = authed_client.get("/api/releases/latest?tracker=release-redesign-shape")
    current_response = authed_client.get("/api/trackers/release-redesign-shape/current")

    assert history_response.status_code == 200, history_response.text
    assert latest_response.status_code == 200, latest_response.text
    assert current_response.status_code == 200, current_response.text

    history_item = history_response.json()["items"][0]
    latest_item = latest_response.json()[0]
    tracker_payload = current_response.json()["tracker"]

    assert "tracker_channels" not in history_item
    assert "primary_changelog_channel_key" not in history_item
    assert "tracker_channels" not in latest_item
    assert "primary_changelog_channel_key" not in latest_item
    assert "tracker_channels" not in tracker_payload
    assert "primary_changelog_channel_key" not in tracker_payload


@pytest.mark.asyncio
async def test_releases_history_and_latest_emit_channel_name_when_available(authed_client, storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="release-channel-name-shape",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/release-channel-name-shape"},
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
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="release-channel-name-shape",
            type="github",
            enabled=True,
            repo="owner/release-channel-name-shape",
            interval=60,
            channels=[Channel(name="stable", type="release")],
        )
    )

    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "repo": [
                Release(
                    tracker_name="release-channel-name-shape",
                    tracker_type="github",
                    version="1.0.0",
                    name="Release 1.0.0",
                    tag_name="v1.0.0",
                    channel_name="stable",
                    url="http://example.com/releases/v1.0.0",
                    published_at=datetime(2025, 2, 5, 12, 0, 0),
                    prerelease=False,
                )
            ]
        },
    )

    history_response = authed_client.get("/api/releases?tracker=release-channel-name-shape")
    latest_response = authed_client.get("/api/releases/latest?tracker=release-channel-name-shape")

    assert history_response.status_code == 200, history_response.text
    assert latest_response.status_code == 200, latest_response.text
    assert history_response.json()["items"][0]["channel_name"] == "stable"
    assert latest_response.json()[0]["channel_name"] == "stable"


@pytest.mark.asyncio
async def test_releases_history_infers_channel_name_for_non_winning_duplicate_identity_rows(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="history-channel-inference",
            primary_changelog_source_key="github",
            sources=[
                TrackerSource(
                    source_key="github",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/history-channel-inference"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="github-stable",
                            name="stable",
                            type="release",
                        )
                    ],
                ),
                TrackerSource(
                    source_key="ghcr",
                    source_type="container",
                    source_rank=1,
                    source_config={
                        "image": "owner/history-channel-inference",
                        "registry": "ghcr.io",
                    },
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="ghcr-stable",
                            name="stable",
                            type="release",
                        )
                    ],
                ),
            ],
        )
    )
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="history-channel-inference",
            type="github",
            enabled=True,
            repo="owner/history-channel-inference",
            interval=60,
            channels=[Channel(name="stable", type="release")],
        )
    )

    github_release = Release(
        tracker_name="history-channel-inference",
        tracker_type="github",
        version="v4.2.1",
        name="v4.2.1",
        tag_name="v4.2.1",
        url="https://example.com/releases/v4.2.1",
        published_at=datetime(2026, 4, 9, 14, 26, 40),
        prerelease=False,
    )
    docker_release = Release(
        tracker_name="history-channel-inference",
        tracker_type="container",
        version="v4.2.1",
        name="latest",
        tag_name="latest",
        url="https://ghcr.io/owner/history-channel-inference:latest",
        published_at=datetime(2026, 4, 22, 16, 39, 32),
        prerelease=False,
        commit_sha="sha256:history-inference",
    )

    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "github": [github_release],
            "ghcr": [docker_release],
        },
    )

    history_response = authed_client.get("/api/releases?tracker=history-channel-inference")

    assert history_response.status_code == 200, history_response.text
    items = history_response.json()["items"]
    assert len(items) == 2
    assert {item["tag_name"]: item["channel_name"] for item in items} == {
        "latest": "stable",
        "v4.2.1": "stable",
    }


@pytest.mark.asyncio
async def test_releases_reject_legacy_selector_shape(authed_client, storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="release-selector-shape",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/release-selector-shape"},
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
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="release-selector-shape",
            type="github",
            enabled=True,
            repo="owner/release-selector-shape",
            interval=60,
            channels=[Channel(name="stable", type="release")],
        )
    )

    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "repo": [
                Release(
                    tracker_name="release-selector-shape",
                    tracker_type="github",
                    version="1.0.0",
                    name="Release 1.0.0",
                    tag_name="v1.0.0",
                    url="http://example.com/releases/v1.0.0",
                    published_at=datetime(2025, 2, 6, 12, 0, 0),
                    prerelease=False,
                )
            ]
        },
    )

    legacy_history_selector = authed_client.get(
        "/api/releases?tracker=release-selector-shape&channel=stable"
    )
    canonical_history_selector = authed_client.get(
        "/api/releases?tracker=release-selector-shape&channel=repo-stable"
    )
    legacy_latest_selector = authed_client.get(
        "/api/releases/latest?tracker=release-selector-shape&channel=stable"
    )
    canonical_latest_selector = authed_client.get(
        "/api/releases/latest?tracker=release-selector-shape&channel=repo-stable"
    )
    legacy_current_selector = authed_client.get(
        "/api/trackers/release-selector-shape/current?channel=stable"
    )
    canonical_current_selector = authed_client.get(
        "/api/trackers/release-selector-shape/current?channel=repo-stable"
    )

    assert legacy_history_selector.status_code == 400, legacy_history_selector.text
    assert canonical_history_selector.status_code == 200, canonical_history_selector.text
    assert legacy_latest_selector.status_code == 400, legacy_latest_selector.text
    assert canonical_latest_selector.status_code == 200, canonical_latest_selector.text
    assert legacy_current_selector.status_code == 400, legacy_current_selector.text
    assert canonical_current_selector.status_code == 200, canonical_current_selector.text


@pytest.mark.asyncio
async def test_releases_history_rowset_is_projection_rebuild_invariant_without_current_annotations(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="projection-rebuild-history",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/projection-rebuild-history"},
                )
            ],
        )
    )
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="projection-rebuild-history",
            type="github",
            enabled=True,
            repo="owner/projection-rebuild-history",
            interval=60,
            channels=[Channel(name="stable", type="release")],
        )
    )

    release_a = Release(
        tracker_name="projection-rebuild-history",
        tracker_type="github",
        version="1.0.0",
        name="Release 1.0.0",
        tag_name="v1.0.0",
        url="http://example.com/releases/v1.0.0",
        published_at=datetime(2025, 3, 1, 12, 0, 0),
        prerelease=False,
    )
    release_b = Release(
        tracker_name="projection-rebuild-history",
        tracker_type="github",
        version="2.0.0",
        name="Release 2.0.0",
        tag_name="v2.0.0",
        url="http://example.com/releases/v2.0.0",
        published_at=datetime(2025, 3, 2, 12, 0, 0),
        prerelease=False,
    )

    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {"repo": [release_a, release_b]},
        projection_releases=[release_a],
    )

    before_response = authed_client.get("/api/releases?tracker=projection-rebuild-history")
    assert before_response.status_code == 200, before_response.text
    before_items = before_response.json()["items"]
    before_versions = [item["version"] for item in before_items]
    before_total = before_response.json()["total"]
    assert all("is_current" not in item for item in before_items)
    assert all("current_channel_keys" not in item for item in before_items)
    assert all("projected_at" not in item for item in before_items)

    async with aiosqlite.connect(storage.db_path) as db:
        await db.execute(
            "DELETE FROM tracker_current_releases WHERE aggregate_tracker_id = ?",
            (aggregate_tracker.id,),
        )
        await db.commit()

    after_response = authed_client.get("/api/releases?tracker=projection-rebuild-history")
    assert after_response.status_code == 200, after_response.text
    after_payload = after_response.json()
    after_items = after_payload["items"]

    assert after_payload["total"] == before_total
    assert [item["version"] for item in after_items] == before_versions
    assert [item["tracker_release_history_id"] for item in after_items] == [
        item["tracker_release_history_id"] for item in before_items
    ]
    assert all("is_current" not in item for item in after_items)
    assert all("current_channel_keys" not in item for item in after_items)
    assert all("projected_at" not in item for item in after_items)


@pytest.mark.asyncio
async def test_releases_latest_reject_legacy_channel_name_selector_and_apply_canonical_channel_selector(
    authed_client, storage
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="latest-query-contract",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/latest-query-contract"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="repo-stable",
                            name="stable",
                            type="release",
                        ),
                        ReleaseChannel(
                            release_channel_key="repo-prerelease",
                            name="prerelease",
                            type="prerelease",
                        ),
                    ],
                )
            ],
        )
    )
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="latest-query-contract",
            type="github",
            enabled=True,
            repo="owner/latest-query-contract",
            interval=60,
            channels=[
                Channel(name="stable", type="release"),
                Channel(name="prerelease", type="prerelease"),
            ],
        )
    )

    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "repo": [
                Release(
                    tracker_name="latest-query-contract",
                    tracker_type="github",
                    version="1.0.0",
                    name="Release 1.0.0",
                    tag_name="v1.0.0",
                    url="http://example.com/releases/v1.0.0",
                    published_at=datetime(2025, 4, 1, 12, 0, 0),
                    prerelease=False,
                ),
                Release(
                    tracker_name="latest-query-contract",
                    tracker_type="github",
                    version="2.0.0-alpha.1",
                    name="Alpha 2.0.0-alpha.1",
                    tag_name="v2.0.0-alpha.1",
                    url="http://example.com/releases/v2.0.0-alpha.1",
                    published_at=datetime(2025, 4, 2, 12, 0, 0),
                    prerelease=True,
                ),
            ]
        },
    )

    removed_mode_response = authed_client.get("/api/releases/latest?include_history=true")
    missing_tracker_response = authed_client.get("/api/releases/latest?channel=stable")
    invalid_channel_response = authed_client.get(
        "/api/releases/latest?tracker=latest-query-contract&channel=missing"
    )
    legacy_name_response = authed_client.get(
        "/api/releases/latest?tracker=latest-query-contract&channel=prerelease"
    )
    filtered_response = authed_client.get(
        "/api/releases/latest?tracker=latest-query-contract&channel=repo-prerelease&search=alpha&prerelease=true&limit=1"
    )

    assert removed_mode_response.status_code == 400, removed_mode_response.text
    assert missing_tracker_response.status_code == 400, missing_tracker_response.text
    assert invalid_channel_response.status_code == 400, invalid_channel_response.text
    assert legacy_name_response.status_code == 400, legacy_name_response.text
    assert filtered_response.status_code == 200, filtered_response.text
    payload = filtered_response.json()
    assert len(payload) == 1
    assert payload[0]["tracker_name"] == "latest-query-contract"
    assert payload[0]["version"] == "2.0.0-alpha.1"
    assert payload[0]["prerelease"] is True




@pytest.mark.asyncio
async def test_get_latest_release_for_channels_uses_full_history(storage):
    tracker_name = "full-history-tracker"
    await storage.set_setting("version_sort_mode", "semver")

    oldest_best = Release(
        tracker_name=tracker_name,
        tracker_type="github",
        version="999.0.0",
        name="Release 999.0.0",
        tag_name="v999.0.0",
        channel_name="stable",
        url="http://example.com/v999.0.0",
        published_at=datetime(2023, 1, 1, 0, 0, 0),
        prerelease=False,
    )
    await _seed_runtime_release(storage, oldest_best)

    start = datetime(2024, 1, 1, 0, 0, 0)
    for version_number in range(1, 102):
        await _seed_runtime_release(
            storage,
            Release(
                tracker_name=tracker_name,
                tracker_type="github",
                version=f"{version_number}.0.0",
                name=f"Release {version_number}.0.0",
                tag_name=f"v{version_number}.0.0",
                channel_name="stable",
                url=f"http://example.com/v{version_number}.0.0",
                published_at=start + timedelta(minutes=version_number),
                prerelease=False,
            ),
        )

    best_release = await storage.get_latest_release_for_channels(
        tracker_name,
        [Channel(name="stable", type="release")],
    )

    assert best_release is not None
    assert best_release.tag_name == "v101.0.0"
    assert best_release.version == "101.0.0"


def test_select_best_releases_by_channel_uses_semver_before_publish_time():
    releases = [
        Release(
            tracker_name="python",
            tracker_type="github",
            version="3.12.1",
            name="Release 3.12.1",
            tag_name="3.12.1",
            channel_name="stable",
            url="http://example.com/3.12.1",
            published_at=datetime(2024, 1, 10, 0, 0, 0),
            prerelease=False,
        ),
        Release(
            tracker_name="python",
            tracker_type="github",
            version="3.13.2",
            name="Release 3.13.2",
            tag_name="3.13.2",
            channel_name="stable",
            url="http://example.com/3.13.2",
            published_at=datetime(2024, 1, 11, 0, 0, 0),
            prerelease=False,
        ),
        Release(
            tracker_name="python",
            tracker_type="github",
            version="3.12.2",
            name="Release 3.12.2",
            tag_name="3.12.2",
            channel_name="stable",
            url="http://example.com/3.12.2",
            published_at=datetime(2024, 2, 10, 0, 0, 0),
            prerelease=False,
        ),
        Release(
            tracker_name="python",
            tracker_type="github",
            version="4.0.0-alpha.1",
            name="Release 4.0.0-alpha.1",
            tag_name="4.0.0-alpha.1",
            channel_name="prerelease",
            url="http://example.com/4.0.0-alpha.1",
            published_at=datetime(2024, 2, 11, 0, 0, 0),
            prerelease=True,
        ),
    ]

    winners = SQLiteStorage.select_best_releases_by_channel(
        releases,
        [
            Channel(name="stable", type="release"),
            Channel(name="prerelease", type="prerelease"),
        ],
        sort_mode="semver",
    )

    assert winners["stable"].version == "3.13.2"
    assert winners["prerelease"].version == "4.0.0-alpha.1"


def test_select_best_releases_by_channel_normalizes_version_prefix_before_semver_compare():
    releases = [
        Release(
            tracker_name="authentik",
            tracker_type="github",
            version="version/2026.2.1",
            name="Release version/2026.2.1",
            tag_name="version/2026.2.1",
            channel_name="stable",
            url="http://example.com/version/2026.2.1",
            published_at=datetime(2026, 2, 20, 0, 0, 0),
            prerelease=False,
        ),
        Release(
            tracker_name="authentik",
            tracker_type="github",
            version="version/2026.2.2",
            name="Release version/2026.2.2",
            tag_name="version/2026.2.2",
            channel_name="stable",
            url="http://example.com/version/2026.2.2",
            published_at=datetime(2026, 2, 10, 0, 0, 0),
            prerelease=False,
        ),
    ]

    winners = SQLiteStorage.select_best_releases_by_channel(
        releases,
        [Channel(name="stable", type="release")],
        sort_mode="semver",
    )

    assert winners["stable"].version == "version/2026.2.2"


def test_select_best_releases_by_channel_prefers_semver_over_published_at_even_when_mode_is_published_at():
    releases = [
        Release(
            tracker_name="aether",
            tracker_type="container",
            version="0.1.6",
            name="0.1.6",
            tag_name="0.1.6",
            channel_name="stable",
            url="http://example.com/0.1.6",
            published_at=datetime(2026, 4, 22, 7, 30, 24),
            prerelease=False,
            commit_sha="sha256:016",
        ),
        Release(
            tracker_name="aether",
            tracker_type="container",
            version="0.1.7",
            name="latest",
            tag_name="latest",
            channel_name="stable",
            url="http://example.com/latest",
            published_at=datetime(2026, 4, 22, 7, 29, 59),
            prerelease=False,
            commit_sha="sha256:017",
        ),
    ]

    winners = SQLiteStorage.select_best_releases_by_channel(
        releases,
        [Channel(name="stable", type="release")],
        sort_mode="published_at",
        use_immutable_identity=True,
    )

    assert winners["stable"].version == "0.1.7"


@pytest.mark.asyncio
async def test_latest_current_summary_prefers_highest_stable_semver_before_published_at_for_multi_source_tracker(
    storage,
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aether-summary-order",
            primary_changelog_source_key="channel-1",
            sources=[
                TrackerSource(
                    source_key="channel-1",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/aether-summary-order"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="channel-1-0-stable",
                            name="stable",
                            type="release",
                        )
                    ],
                ),
                TrackerSource(
                    source_key="channel-2",
                    source_type="container",
                    source_rank=1,
                    source_config={"image": "owner/aether-summary-order", "registry": "ghcr.io"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="channel-2-0-stable",
                            name="stable",
                            type="release",
                            exclude_pattern=".*rc.*",
                        ),
                        ReleaseChannel(
                            release_channel_key="channel-2-1-prerelease",
                            name="canary",
                            type="prerelease",
                            include_pattern=".*rc.*",
                        ),
                    ],
                ),
                TrackerSource(
                    source_key="channel-3",
                    source_type="container",
                    source_rank=2,
                    source_config={
                        "image": "owner/aether-summary-order-hub",
                        "registry": "ghcr.io",
                    },
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="channel-3-0-stable",
                            name="stable",
                            type="release",
                        )
                    ],
                ),
            ],
        )
    )
    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="aether-summary-order",
            type="github",
            enabled=True,
            repo="owner/aether-summary-order",
            interval=60,
            version_sort_mode="published_at",
            channels=[Channel(name="stable", type="release", exclude_pattern=".*rc.*")],
        )
    )

    github_release = Release(
        tracker_name="aether-summary-order",
        tracker_type="github",
        version="proxy-v0.3.2",
        name="proxy-v0.3.2",
        tag_name="proxy-v0.3.2",
        url="https://example.com/proxy-v0.3.2",
        published_at=datetime(2026, 4, 18, 17, 55, 16),
        prerelease=False,
    )
    docker_canary = Release(
        tracker_name="aether-summary-order",
        tracker_type="container",
        version="0.7.0-rc1",
        name="0.7.0-rc1",
        tag_name="0.7.0-rc1",
        url="https://example.com/0.7.0-rc1",
        published_at=datetime(2026, 4, 22, 5, 17, 29),
        prerelease=True,
        commit_sha="sha256:rc1",
    )
    docker_stable_old = Release(
        tracker_name="aether-summary-order",
        tracker_type="container",
        version="0.1.6",
        name="0.1.6",
        tag_name="0.1.6",
        url="https://example.com/0.1.6",
        published_at=datetime(2026, 4, 22, 5, 15, 50),
        prerelease=False,
        commit_sha="sha256:016",
    )
    docker_stable_new = Release(
        tracker_name="aether-summary-order",
        tracker_type="container",
        version="0.1.7",
        name="latest",
        tag_name="latest",
        url="https://example.com/latest",
        published_at=datetime(2026, 4, 22, 5, 15, 43),
        prerelease=False,
        commit_sha="sha256:017",
    )
    second_docker_old = Release(
        tracker_name="aether-summary-order",
        tracker_type="container",
        version="0.6.3",
        name="latest",
        tag_name="latest",
        url="https://example.com/0.6.3-latest",
        published_at=datetime(2026, 4, 22, 5, 15, 39),
        prerelease=False,
        commit_sha="sha256:063",
    )

    await _materialize_aggregate_truth_and_projection(
        storage,
        aggregate_tracker,
        {
            "channel-1": [github_release],
            "channel-2": [docker_canary, second_docker_old],
            "channel-3": [docker_stable_old, docker_stable_new],
        },
        projection_releases=[
            docker_canary,
            docker_stable_old,
            docker_stable_new,
            second_docker_old,
            github_release,
        ],
    )

    latest_summary = await storage.get_tracker_latest_current_release_summary(
        "aether-summary-order"
    )

    assert latest_summary is not None
    assert latest_summary["version"] == "0.6.3"
    assert latest_summary["release"].version == "0.6.3"


def test_select_best_releases_for_tracker_channel_keeps_duplicate_names_distinct_when_ownership_differs():
    repo_channel = TrackerSource(
        source_key="repo",
        source_type="github",
        source_rank=0,
        source_config={"repo": "owner/python"},
        release_channels=[
            ReleaseChannel(
                release_channel_key="repo-stable",
                name="stable",
                type="release",
            )
        ],
    )
    image_channel = TrackerSource(
        source_key="image",
        source_type="container",
        source_rank=1,
        source_config={"image": "ghcr.io/owner/python", "registry": "ghcr.io"},
        release_channels=[
            ReleaseChannel(
                release_channel_key="image-stable",
                name="stable",
                type="prerelease",
            )
        ],
    )

    repo_releases = [
        Release(
            tracker_name="python",
            tracker_type="github",
            version="3.13.2",
            name="Release 3.13.2",
            tag_name="3.13.2",
            channel_name="stable",
            url="http://example.com/3.13.2",
            published_at=datetime(2024, 1, 11, 0, 0, 0),
            prerelease=False,
        )
    ]
    image_releases = [
        Release(
            tracker_name="python",
            tracker_type="container",
            version="4.0.0-beta.1",
            name="Image 4.0.0-beta.1",
            tag_name="4.0.0-beta.1",
            channel_name="stable",
            url="http://example.com/4.0.0-beta.1",
            published_at=datetime(2024, 2, 11, 0, 0, 0),
            prerelease=True,
        )
    ]

    winners = {
        **SQLiteStorage.select_best_releases_for_tracker_channel(
            repo_releases,
            repo_channel,
            sort_mode="semver",
        ),
        **SQLiteStorage.select_best_releases_for_tracker_channel(
            image_releases,
            image_channel,
            sort_mode="semver",
        ),
    }

    assert set(winners) == {"repo-stable", "image-stable"}
    assert {winner.version for winner in winners.values()} == {"3.13.2", "4.0.0-beta.1"}
    assert {winner.channel_name for winner in winners.values()} == {"stable"}


def test_select_best_releases_by_channel_uses_runtime_channel_names_as_selection_keys():
    releases = [
        Release(
            tracker_name="python",
            tracker_type="github",
            version="3.13.2",
            name="Release 3.13.2",
            tag_name="3.13.2",
            channel_name="stable",
            url="http://example.com/3.13.2",
            published_at=datetime(2024, 1, 11, 0, 0, 0),
            prerelease=False,
        )
    ]

    winners = SQLiteStorage.select_best_releases_by_channel(
        releases,
        [Channel(name="stable", type="release")],
        sort_mode="semver",
    )

    assert set(winners) == {"stable"}
    assert winners["stable"].version == "3.13.2"


def test_select_best_releases_by_channel_excludes_docker_alias_when_folded_version_matches_exclude():
    releases = [
        Release(
            tracker_name="jenkins-agent",
            tracker_type="container",
            version="3256.3258.v858f3c9a_f69d-1",
            name="latest",
            tag_name="latest",
            url="http://example.com/latest",
            published_at=datetime(2026, 4, 28, 23, 1, 19),
            prerelease=False,
            commit_sha="sha256:latest",
        ),
        Release(
            tracker_name="jenkins-agent",
            tracker_type="container",
            version="3256.3258.v858f3c9a_f69d",
            name="trixie",
            tag_name="trixie",
            url="http://example.com/trixie",
            published_at=datetime(2026, 4, 28, 23, 1, 20),
            prerelease=False,
            commit_sha="sha256:trixie",
        ),
    ]

    winners = SQLiteStorage.select_best_releases_by_channel(
        releases,
        [
            Channel(
                name="stable",
                type="release",
                include_pattern=r"(latest|trixie)",
                exclude_pattern=r"^.*-.*$",
            )
        ],
        sort_mode="published_at",
        use_immutable_identity=True,
    )

    assert winners["stable"].tag_name == "trixie"
    assert winners["stable"].version == "3256.3258.v858f3c9a_f69d"


@pytest.mark.asyncio
async def test_get_latest_release_for_channels_stays_scoped_to_requested_tracker(storage):
    await storage.set_setting("version_sort_mode", "semver")
    requested_tracker = "legacy-scope-a"
    other_tracker = "legacy-scope-b"

    await _seed_runtime_release(
        storage,
        Release(
            tracker_name=requested_tracker,
            tracker_type="github",
            version="1.2.3",
            name="Release 1.2.3",
            tag_name="v1.2.3",
            channel_name="stable",
            url="http://example.com/a/v1.2.3",
            published_at=datetime(2024, 2, 1, 12, 0, 0),
            prerelease=False,
        ),
    )
    await _seed_runtime_release(
        storage,
        Release(
            tracker_name=other_tracker,
            tracker_type="github",
            version="9.9.9",
            name="Release 9.9.9",
            tag_name="v9.9.9",
            channel_name="stable",
            url="http://example.com/b/v9.9.9",
            published_at=datetime(2024, 2, 2, 12, 0, 0),
            prerelease=False,
        ),
    )

    winner = await storage.get_latest_release_for_channels(
        requested_tracker,
        [Channel(name="stable", type="release")],
    )

    assert winner is not None
    assert winner.tracker_name == requested_tracker
    assert winner.version == "1.2.3"
    assert winner.channel_name == "stable"


@pytest.mark.asyncio
async def test_releases_list_includes_archived_channel_state_for_republished_winner(storage):
    tracker_name = "history-channel-api"
    original_release = Release(
        tracker_name=tracker_name,
        tracker_type="github",
        version="1.0.0",
        name="Release 1.0.0",
        tag_name="v1.0.0",
        channel_name="stable",
        url="http://example.com/v1.0.0",
        published_at=datetime(2024, 7, 1, 12, 0, 0),
        prerelease=False,
        body="original body",
        commit_sha="abc123",
    )
    await _seed_runtime_release(storage, original_release)

    republished_release = original_release.model_copy(
        update={
            "channel_name": "renamed-stable",
            "published_at": datetime(2024, 7, 2, 12, 0, 0),
            "body": "republished body",
            "commit_sha": "def456",
        }
    )
    await _seed_runtime_release(storage, republished_release)

    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None and aggregate_tracker.id is not None
    tracker_history = await storage.get_tracker_release_history_releases(aggregate_tracker.id)

    assert [release.channel_name for release in tracker_history] == ["renamed-stable", "stable"]
    assert [release.commit_sha for release in tracker_history] == ["def456", "abc123"]






@pytest.mark.asyncio
async def test_get_latest_release_for_channels_prefers_current_winner_over_archived_state(storage):
    tracker_name = "current-winner-over-history"
    await storage.set_setting("version_sort_mode", "published_at")

    original_release = Release(
        tracker_name=tracker_name,
        tracker_type="github",
        version="1.0.0",
        name="Release 1.0.0",
        tag_name="v1.0.0",
        channel_name="stable",
        url="http://example.com/v1.0.0",
        published_at=datetime(2024, 9, 1, 12, 0, 0),
        prerelease=False,
        body="before republish",
        commit_sha="oldsha",
    )
    republished_release = original_release.model_copy(
        update={
            "published_at": datetime(2024, 9, 2, 12, 0, 0),
            "body": "after republish",
            "commit_sha": "newsha",
        }
    )
    await _seed_runtime_release(storage, republished_release)

    latest = await storage.get_latest_release_for_channels(
        tracker_name,
        [Channel(name="stable", type="release")],
    )

    assert latest is not None
    assert latest.commit_sha == "newsha"
    assert latest.body == "after republish"
    assert latest.published_at == datetime(2024, 9, 2, 12, 0, 0)


@pytest.mark.asyncio
async def test_get_releases_current_mode_does_not_fallback_to_canonical_when_projection_empty(
    storage,
):
    tracker_name = "current-mode-no-canonical-fallback"
    await storage.save_tracker_config(
        TrackerConfig(
            name=tracker_name,
            type="container",
            enabled=True,
            image=f"ghcr.io/acme/{tracker_name}",
            registry="ghcr.io",
            channels=[Channel(name="stable", type="release")],
        )
    )

    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None and aggregate_tracker.id is not None
    runtime_source = storage._select_runtime_source(aggregate_tracker)
    assert runtime_source is not None

    await storage.save_source_observations(
        aggregate_tracker.id,
        runtime_source,
        [
            Release(
                tracker_name=tracker_name,
                tracker_type="container",
                version="1.0.0",
                name="1.0.0",
                tag_name="1.0.0",
                channel_name="stable",
                url=f"https://example.com/{tracker_name}/1.0.0",
                published_at=datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
                prerelease=False,
            )
        ],
    )

    canonical_rows = await storage.get_canonical_releases(tracker_name)
    assert len(canonical_rows) == 1

    async with aiosqlite.connect(storage.db_path) as db:
        await db.execute(
            "DELETE FROM tracker_current_releases WHERE aggregate_tracker_id = ?",
            (aggregate_tracker.id,),
        )
        await db.commit()

    current_releases = await storage.get_releases(
        tracker_name=tracker_name,
        limit=None,
        include_history=False,
    )
    history_releases = await storage.get_releases(
        tracker_name=tracker_name,
        limit=None,
        include_history=True,
    )

    assert current_releases == []
    assert [release.version for release in history_releases] == ["1.0.0"]
