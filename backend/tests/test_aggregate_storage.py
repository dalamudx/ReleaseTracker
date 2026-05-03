# pyright: reportCallIssue=false

from datetime import datetime
import json

import aiosqlite
import pytest

from releasetracker.config import TrackerConfig
from releasetracker.models import AggregateTracker, TrackerSource
from releasetracker.models import Release
from releasetracker.storage.sqlite import SQLiteStorage
from releasetracker.trackers.helm import HelmTracker


def test_tracker_source_validates_provider_specific_config_keys():
    with pytest.raises(ValueError, match="Unknown source_config keys for github: instance"):
        TrackerSource(
            source_key="upstream",
            source_type="github",
            source_config={"repo": "owner/repo", "instance": "https://github.com"},
        )


def test_aggregate_tracker_requires_at_least_one_source():
    with pytest.raises(ValueError, match="at least one tracker source"):
        AggregateTracker(name="empty-aggregate", primary_changelog_source_key=None, sources=[])


@pytest.mark.asyncio
async def test_create_and_load_aggregate_tracker_roundtrip(storage):
    created_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="  aggregate-alpha  ",
            enabled=True,
            description="aggregate tracker",
            primary_changelog_source_key="upstream-gh",
            sources=[
                TrackerSource(
                    source_key="upstream-gh",
                    source_type="github",
                    credential_name="github-token",
                    source_rank=10,
                    source_config={"repo": "owner/repo"},
                ),
                TrackerSource(
                    source_key="mirror-docker",
                    source_type="container",
                    enabled=False,
                    source_rank=20,
                    source_config={
                        "image": "library/nginx",
                        "registry": "registry-1.docker.io",
                    },
                ),
            ],
        )
    )

    assert created_tracker.id is not None
    assert created_tracker.name == "aggregate-alpha"
    assert created_tracker.primary_changelog_source_key == "upstream-gh"
    assert [source.source_key for source in created_tracker.sources] == [
        "upstream-gh",
        "mirror-docker",
    ]
    assert created_tracker.sources[0].aggregate_tracker_id == created_tracker.id
    assert created_tracker.sources[0].source_config == {"repo": "owner/repo"}
    assert created_tracker.sources[1].source_config == {
        "image": "library/nginx",
        "registry": "registry-1.docker.io",
    }

    loaded_tracker = await storage.get_aggregate_tracker("aggregate-alpha")

    assert loaded_tracker is not None
    assert loaded_tracker.model_dump(
        exclude={"created_at", "updated_at"}
    ) == created_tracker.model_dump(exclude={"created_at", "updated_at"})

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT id, primary_changelog_source_id FROM aggregate_trackers WHERE name = ?",
                ("aggregate-alpha",),
            )
        ).fetchone()
        source_rows = await (
            await db.execute(
                "SELECT source_key, source_config FROM aggregate_tracker_sources WHERE aggregate_tracker_id = ? ORDER BY source_rank ASC, id ASC",
                (created_tracker.id,),
            )
        ).fetchall()

    assert row is not None
    assert row["primary_changelog_source_id"] == created_tracker.sources[0].id
    assert [source_row["source_key"] for source_row in source_rows] == [
        "upstream-gh",
        "mirror-docker",
    ]


@pytest.mark.parametrize("source_type", ["github", "gitlab", "gitea"])
def test_release_identity_key_uses_commit_for_github_like_sources(source_type):
    original = Release(
        tracker_name=f"{source_type}-republish",
        tracker_type=source_type,
        version="v1.2.3",
        name="Release v1.2.3",
        tag_name="v1.2.3",
        url="https://example.com/release/v1.2.3",
        published_at=datetime.fromisoformat("2026-04-23T00:00:00+00:00"),
        prerelease=False,
        commit_sha="sha-old",
    )
    same_commit_new_note = original.model_copy(
        update={
            "published_at": datetime.fromisoformat("2026-04-24T00:00:00+00:00"),
            "body": "updated notes",
        }
    )
    new_commit = original.model_copy(update={"commit_sha": "sha-new"})

    assert SQLiteStorage.release_identity_key_for_source(
        original, source_type=source_type
    ) == SQLiteStorage.release_identity_key_for_source(
        same_commit_new_note, source_type=source_type
    )
    assert SQLiteStorage.release_identity_key_for_source(
        original, source_type=source_type
    ) != SQLiteStorage.release_identity_key_for_source(new_commit, source_type=source_type)


@pytest.mark.asyncio
async def test_save_source_observations_updates_github_metadata_without_new_history_identity(
    storage,
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="github-note-only-update",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/github-note-only-update"},
                )
            ],
        )
    )
    assert aggregate_tracker.id is not None
    repo_source = aggregate_tracker.sources[0]

    original = Release(
        tracker_name="github-note-only-update",
        tracker_type="github",
        version="v1.2.3",
        name="Release v1.2.3",
        tag_name="v1.2.3",
        url="https://example.com/release/v1.2.3",
        published_at=datetime.fromisoformat("2026-04-23T00:00:00+00:00"),
        prerelease=False,
        body="original notes",
        commit_sha="sha-old",
    )
    updated = original.model_copy(
        update={
            "published_at": datetime.fromisoformat("2026-04-24T00:00:00+00:00"),
            "body": "updated notes",
        }
    )

    await storage.save_source_observations(aggregate_tracker.id, repo_source, [original])
    await storage.save_source_observations(aggregate_tracker.id, repo_source, [updated])

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        history_rows = list(
            await (
                await db.execute(
                    "SELECT version, published_at, body, commit_sha FROM source_release_history WHERE tracker_source_id = ?",
                    (repo_source.id,),
                )
            ).fetchall()
        )

    assert len(history_rows) == 1
    assert history_rows[0]["version"] == "v1.2.3"
    assert history_rows[0]["commit_sha"] == "sha-old"
    assert history_rows[0]["body"] == "updated notes"
    assert history_rows[0]["published_at"] == "2026-04-23T00:00:00+00:00"


@pytest.mark.asyncio
async def test_docker_source_history_refreshes_synthetic_published_at_for_existing_tags(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="jenkins-agent-refresh",
            primary_changelog_source_key="container",
            sources=[
                TrackerSource(
                    source_key="container",
                    source_type="container",
                    source_rank=0,
                    source_config={
                        "image": "jenkins/inbound-agent",
                        "registry": "registry-1.docker.io",
                    },
                )
            ],
        )
    )
    assert aggregate_tracker.id is not None
    docker_source = aggregate_tracker.sources[0]

    async def persist_fetch(releases: list[Release]) -> None:
        await storage.save_source_observations(aggregate_tracker.id, docker_source, releases)
        for release in releases:
            source_history_id = await storage.get_source_release_history_id(
                docker_source.id,
                storage.release_identity_key_for_source(release, source_type="container"),
            )
            assert source_history_id is not None
            await storage.upsert_tracker_release_history(
                aggregate_tracker.id,
                release,
                primary_source_release_history_id=source_history_id,
                source_type="container",
            )

    def docker_release(tag: str, published_at: str) -> Release:
        return Release(
            tracker_name="jenkins-agent-refresh",
            tracker_type="container",
            version=tag,
            name=tag,
            tag_name=tag,
            url=f"https://registry-1.docker.io/jenkins/inbound-agent:{tag}",
            published_at=datetime.fromisoformat(published_at),
            prerelease=False,
        )

    await persist_fetch(
        [
            docker_release("3355.v388858a_47b_33-20", "2026-05-06T01:21:48"),
            docker_release("3355.v388858a_47b_33-19", "2026-05-06T01:21:47"),
            docker_release("3355.v388858a_47b_33-2", "2026-05-06T01:21:46"),
        ]
    )
    await persist_fetch(
        [
            docker_release("3355.v388858a_47b_33-20", "2026-05-06T01:45:40"),
            docker_release("3355.v388858a_47b_33-19", "2026-05-06T01:45:39"),
            docker_release("3355.v388858a_47b_33-18", "2026-05-06T01:45:38"),
        ]
    )

    history_releases = await storage.get_tracker_release_history_releases(aggregate_tracker.id)
    await storage.refresh_tracker_current_releases(
        aggregate_tracker.id,
        storage.dedupe_releases_by_immutable_identity(history_releases),
    )
    current_releases = await storage.get_tracker_current_releases(aggregate_tracker.id)
    latest_release = max(
        current_releases,
        key=lambda release: storage._release_order_key(release, "published_at"),
    )

    assert latest_release.tag_name == "3355.v388858a_47b_33-20"
    assert latest_release.published_at == datetime.fromisoformat("2026-05-06T01:45:40")


@pytest.mark.asyncio
async def test_save_source_observations_creates_new_github_history_identity_when_commit_changes(
    storage,
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="github-commit-republish",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/github-commit-republish"},
                )
            ],
        )
    )
    assert aggregate_tracker.id is not None
    repo_source = aggregate_tracker.sources[0]

    original = Release(
        tracker_name="github-commit-republish",
        tracker_type="github",
        version="v1.2.3",
        name="Release v1.2.3",
        tag_name="v1.2.3",
        url="https://example.com/release/v1.2.3",
        published_at=datetime.fromisoformat("2026-04-23T00:00:00+00:00"),
        prerelease=False,
        commit_sha="sha-old",
    )
    republished = original.model_copy(
        update={
            "published_at": datetime.fromisoformat("2026-04-24T00:00:00+00:00"),
            "body": "republished notes",
            "commit_sha": "sha-new",
        }
    )

    await storage.save_source_observations(aggregate_tracker.id, repo_source, [original])
    await storage.save_source_observations(aggregate_tracker.id, repo_source, [republished])

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        history_rows = list(
            await (
                await db.execute(
                    "SELECT identity_key, commit_sha FROM source_release_history WHERE tracker_source_id = ? ORDER BY id ASC",
                    (repo_source.id,),
                )
            ).fetchall()
        )

    assert len(history_rows) == 2
    assert {row["commit_sha"] for row in history_rows} == {"sha-old", "sha-new"}


def test_helm_release_identity_key_uses_index_digest():
    tracker = HelmTracker(
        name="helm-digest-identity",
        repo="https://charts.example.com",
        chart="demo",
    )

    original = tracker._parse_chart_version(
        {
            "version": "1.2.3-chart.1",
            "appVersion": "1.2.3",
            "created": "2026-04-23T00:00:00Z",
            "digest": "sha256:" + "1" * 64,
        }
    )
    republished = tracker._parse_chart_version(
        {
            "version": "1.2.3-chart.1",
            "appVersion": "1.2.3",
            "created": "2026-04-24T00:00:00Z",
            "digest": "sha256:" + "2" * 64,
        }
    )
    note_only = tracker._parse_chart_version(
        {
            "version": "1.2.3-chart.1",
            "appVersion": "1.2.3",
            "created": "2026-04-25T00:00:00Z",
            "digest": "sha256:" + "1" * 64,
        }
    )

    assert SQLiteStorage.release_identity_key_for_source(
        original, source_type="helm"
    ) != SQLiteStorage.release_identity_key_for_source(republished, source_type="helm")
    assert SQLiteStorage.release_identity_key_for_source(
        original, source_type="helm"
    ) == SQLiteStorage.release_identity_key_for_source(note_only, source_type="helm")


def test_helm_chart_version_aliases_share_index_digest_identity():
    tracker = HelmTracker(
        name="helm-chart-alias-identity",
        repo="https://charts.example.com",
        chart="demo",
    )
    digest = "sha256:" + "a" * 64

    first_chart_alias = tracker._parse_chart_version(
        {
            "version": "1.2.3-chart.1",
            "appVersion": "1.2.3",
            "created": "2026-04-23T00:00:00Z",
            "digest": digest,
        }
    )
    second_chart_alias = tracker._parse_chart_version(
        {
            "version": "1.2.3-chart.2",
            "appVersion": "1.2.3",
            "created": "2026-04-24T00:00:00Z",
            "digest": digest,
        }
    )

    assert (
        SQLiteStorage.release_identity_key_for_source(first_chart_alias, source_type="helm")
        == digest
    )
    assert SQLiteStorage.release_identity_key_for_source(
        first_chart_alias, source_type="helm"
    ) == SQLiteStorage.release_identity_key_for_source(second_chart_alias, source_type="helm")


def test_git_release_tag_aliases_share_commit_identity():
    commit_oid = "8f0f9e1c2d3a4b5c6d7e8f90123456789abcdef0"
    release_tag = Release(
        tracker_name="git-alias-identity",
        tracker_type="github",
        version="v1.2.3",
        name="Release v1.2.3",
        tag_name="v1.2.3",
        url="https://example.com/releases/v1.2.3",
        published_at=datetime.fromisoformat("2026-04-23T00:00:00+00:00"),
        prerelease=False,
        commit_sha=commit_oid,
    )
    moving_alias = release_tag.model_copy(
        update={
            "version": "stable",
            "name": "Stable",
            "tag_name": "stable",
            "url": "https://example.com/releases/stable",
        }
    )

    assert (
        SQLiteStorage.release_identity_key_for_source(release_tag, source_type="github")
        == commit_oid
    )
    assert SQLiteStorage.release_identity_key_for_source(
        release_tag, source_type="github"
    ) == SQLiteStorage.release_identity_key_for_source(moving_alias, source_type="github")


@pytest.mark.asyncio
async def test_helm_chart_version_aliases_persist_as_one_history_identity(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="helm-persisted-alias-identity",
            primary_changelog_source_key="helm",
            sources=[
                TrackerSource(
                    source_key="helm",
                    source_type="helm",
                    source_config={"repo": "https://charts.example.com", "chart": "demo"},
                )
            ],
        )
    )
    helm_source = aggregate_tracker.sources[0]
    helm_tracker = HelmTracker(
        name="helm-persisted-alias-identity",
        repo="https://charts.example.com",
        chart="demo",
    )
    digest = "sha256:" + "b" * 64
    releases = [
        helm_tracker._parse_chart_version(
            {
                "version": chart_version,
                "appVersion": "1.2.3",
                "created": f"2026-04-2{index}T00:00:00Z",
                "digest": digest,
            }
        )
        for index, chart_version in enumerate(["1.2.3-chart.1", "1.2.3-chart.2"], start=3)
    ]

    await storage.save_source_observations(
        aggregate_tracker.id,
        helm_source,
        releases,
    )

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        history_rows: list[aiosqlite.Row] = list(
            await (
                await db.execute(
                    "SELECT identity_key, immutable_key, digest FROM source_release_history WHERE tracker_source_id = ?",
                    (helm_source.id,),
                )
            ).fetchall()
        )

    assert len(history_rows) == 1
    assert history_rows[0]["identity_key"] == digest
    assert history_rows[0]["immutable_key"] == digest
    assert history_rows[0]["digest"] == digest


@pytest.mark.asyncio
async def test_git_release_aliases_persist_as_one_history_identity(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="git-persisted-alias-identity",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_config={"repo": "owner/git-persisted-alias-identity"},
                )
            ],
        )
    )
    repo_source = aggregate_tracker.sources[0]
    commit_oid = "8f0f9e1c2d3a4b5c6d7e8f90123456789abcdef0"
    releases = [
        Release(
            tracker_name="git-persisted-alias-identity",
            tracker_type="github",
            version=version,
            name=name,
            tag_name=version,
            url=f"https://example.com/releases/{version}",
            published_at=datetime.fromisoformat("2026-04-23T00:00:00+00:00"),
            prerelease=False,
            commit_sha=commit_oid,
        )
        for version, name in [("v1.2.3", "Release v1.2.3"), ("stable", "Stable")]
    ]

    await storage.save_source_observations(
        aggregate_tracker.id,
        repo_source,
        releases,
    )

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        history_rows: list[aiosqlite.Row] = list(
            await (
                await db.execute(
                    "SELECT identity_key, immutable_key, commit_sha FROM source_release_history WHERE tracker_source_id = ?",
                    (repo_source.id,),
                )
            ).fetchall()
        )

    assert len(history_rows) == 1
    assert history_rows[0]["identity_key"] == commit_oid
    assert history_rows[0]["immutable_key"] == commit_oid
    assert history_rows[0]["commit_sha"] == commit_oid


@pytest.mark.asyncio
async def test_update_aggregate_tracker_updates_sources_and_primary_reference(storage):
    created_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-beta",
            primary_changelog_source_key="github-primary",
            sources=[
                TrackerSource(
                    source_key="github-primary",
                    source_type="github",
                    source_rank=10,
                    source_config={"repo": "owner/repo"},
                ),
                TrackerSource(
                    source_key="gitlab-secondary",
                    source_type="gitlab",
                    source_rank=20,
                    source_config={
                        "project": "group/project",
                        "instance": "https://gitlab.example",
                    },
                ),
            ],
        )
    )

    updated_tracker = await storage.update_aggregate_tracker(
        AggregateTracker(
            id=created_tracker.id,
            name="aggregate-beta",
            enabled=False,
            description="updated aggregate",
            primary_changelog_source_key="docker-primary",
            created_at=created_tracker.created_at,
            sources=[
                TrackerSource(
                    source_key="github-primary",
                    source_type="github",
                    credential_name="github-token",
                    source_rank=5,
                    source_config={"repo": "owner/renamed-repo"},
                ),
                TrackerSource(
                    source_key="docker-primary",
                    source_type="container",
                    source_rank=15,
                    source_config={"image": "owner/app", "registry": "ghcr.io"},
                ),
            ],
        )
    )

    assert updated_tracker.id == created_tracker.id
    assert updated_tracker.enabled is False
    assert updated_tracker.description == "updated aggregate"
    assert updated_tracker.primary_changelog_source_key == "docker-primary"
    assert [source.source_key for source in updated_tracker.sources] == [
        "github-primary",
        "docker-primary",
    ]
    assert updated_tracker.sources[0].source_config == {"repo": "owner/renamed-repo"}
    assert updated_tracker.sources[1].source_config == {"image": "owner/app", "registry": "ghcr.io"}

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        source_rows = await (
            await db.execute(
                "SELECT source_key FROM aggregate_tracker_sources WHERE aggregate_tracker_id = ? ORDER BY source_rank ASC, id ASC",
                (created_tracker.id,),
            )
        ).fetchall()
        tracker_row = await (
            await db.execute(
                "SELECT primary_changelog_source_id FROM aggregate_trackers WHERE id = ?",
                (created_tracker.id,),
            )
        ).fetchone()

    assert [source_row["source_key"] for source_row in source_rows] == [
        "github-primary",
        "docker-primary",
    ]
    assert tracker_row is not None
    assert tracker_row["primary_changelog_source_id"] == updated_tracker.sources[1].id


@pytest.mark.asyncio
async def test_update_aggregate_tracker_removes_source_dependents_and_rebuilds_canonical_state(
    storage,
):
    created_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-cleanup",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/repo"},
                ),
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=1,
                    source_config={"image": "owner/repo", "registry": "ghcr.io"},
                ),
            ],
        )
    )
    repo_source = next(source for source in created_tracker.sources if source.source_key == "repo")
    image_source = next(
        source for source in created_tracker.sources if source.source_key == "image"
    )
    release_timestamp = datetime(2025, 1, 1, 12, 0, 0)

    await storage.save_source_observations(
        created_tracker.id,
        repo_source,
        [
            Release(
                tracker_name="aggregate-cleanup",
                tracker_type="github",
                version="1.0.0",
                name="Repo 1.0.0",
                tag_name="v1.0.0",
                url="http://example.com/repo/v1.0.0",
                published_at=release_timestamp,
                body="repo body",
                prerelease=False,
            )
        ],
        observed_at=release_timestamp,
    )
    await storage.save_source_observations(
        created_tracker.id,
        image_source,
        [
            Release(
                tracker_name="aggregate-cleanup",
                tracker_type="container",
                version="1.0.0",
                name="Image 1.0.0",
                tag_name="1.0.0",
                url="http://example.com/image/1.0.0",
                published_at=release_timestamp,
                body="image body",
                prerelease=False,
            ),
            Release(
                tracker_name="aggregate-cleanup",
                tracker_type="container",
                version="2.0.0",
                name="Image 2.0.0",
                tag_name="2.0.0",
                url="http://example.com/image/2.0.0",
                published_at=release_timestamp,
                body="image only body",
                prerelease=False,
            ),
        ],
        observed_at=release_timestamp,
    )

    async with aiosqlite.connect(storage.db_path) as db:
        await db.execute(
            "INSERT INTO runtime_connections (name, type, enabled, config, secrets, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "cleanup-runtime",
                "docker",
                1,
                "{}",
                None,
                None,
                release_timestamp.isoformat(),
                release_timestamp.isoformat(),
            ),
        )
        runtime_row = await (
            await db.execute(
                "SELECT id FROM runtime_connections WHERE name = ?", ("cleanup-runtime",)
            )
        ).fetchone()
        assert runtime_row is not None
        await db.execute(
            "INSERT INTO executors (name, runtime_type, runtime_connection_id, tracker_name, tracker_source_id, channel_name, enabled, image_selection_mode, update_mode, target_ref, maintenance_window, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "cleanup-executor",
                "docker",
                runtime_row[0],
                "aggregate-cleanup",
                image_source.id,
                None,
                1,
                "replace_tag_on_current_image",
                "manual",
                json.dumps({"container_name": "demo", "container_id": "abc"}),
                None,
                None,
                release_timestamp.isoformat(),
                release_timestamp.isoformat(),
            ),
        )
        await db.commit()

    await storage.update_aggregate_tracker(
        AggregateTracker(
            id=created_tracker.id,
            name="aggregate-cleanup",
            primary_changelog_source_key="repo",
            created_at=created_tracker.created_at,
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/repo"},
                )
            ],
        )
    )

    canonical_releases = await storage.get_canonical_releases("aggregate-cleanup")

    assert [release.canonical_key for release in canonical_releases] == ["1.0.0"]
    assert canonical_releases[0].body == "repo body"
    assert len(canonical_releases[0].observations) == 1

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        removed_source_rows = await (
            await db.execute(
                "SELECT COUNT(*) AS count FROM aggregate_tracker_sources WHERE source_key = 'image'"
            )
        ).fetchone()
        removed_observations = await (
            await db.execute(
                "SELECT COUNT(*) AS count FROM source_release_observations WHERE tracker_source_id = ?",
                (image_source.id,),
            )
        ).fetchone()
        orphaned_provenance = await (
            await db.execute(
                "SELECT COUNT(*) AS count FROM canonical_release_observations WHERE source_release_observation_id IN (SELECT id FROM source_release_observations WHERE tracker_source_id = ?)",
                (image_source.id,),
            )
        ).fetchone()
        executor_row = await (
            await db.execute(
                "SELECT tracker_source_id FROM executors WHERE name = ?",
                ("cleanup-executor",),
            )
        ).fetchone()

    assert removed_source_rows is not None and removed_source_rows["count"] == 0
    assert removed_observations is not None and removed_observations["count"] == 0
    assert orphaned_provenance is not None and orphaned_provenance["count"] == 0
    assert executor_row is not None and executor_row["tracker_source_id"] is None


@pytest.mark.asyncio
async def test_delete_aggregate_tracker_removes_sources(storage):
    await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-gamma",
            primary_changelog_source_key="helm-primary",
            sources=[
                TrackerSource(
                    source_key="helm-primary",
                    source_type="helm",
                    source_config={"repo": "https://charts.example.com", "chart": "demo"},
                )
            ],
        )
    )

    await storage.delete_aggregate_tracker("aggregate-gamma")

    assert await storage.get_aggregate_tracker("aggregate-gamma") is None

    async with aiosqlite.connect(storage.db_path) as db:
        tracker_count = await (
            await db.execute(
                "SELECT COUNT(*) FROM aggregate_trackers WHERE name = ?", ("aggregate-gamma",)
            )
        ).fetchone()
        source_count = await (
            await db.execute("SELECT COUNT(*) FROM aggregate_tracker_sources")
        ).fetchone()

    assert tracker_count is not None
    assert source_count is not None
    assert tracker_count[0] == 0
    assert source_count[0] == 0



@pytest.mark.asyncio
async def test_get_tracker_config_prefers_aggregate_source_fields_over_stale_legacy_row(storage):
    await storage.save_tracker_config(
        TrackerConfig(
            name="aggregate-runtime-authority",
            type="github",
            enabled=True,
            repo="owner/original-repo",
            interval=90,
        )
    )

    aggregate_tracker = await storage.get_aggregate_tracker("aggregate-runtime-authority")
    assert aggregate_tracker is not None

    await storage.update_aggregate_tracker(
        AggregateTracker(
            id=aggregate_tracker.id,
            name="aggregate-runtime-authority",
            enabled=True,
            primary_changelog_source_key="docker-origin",
            created_at=aggregate_tracker.created_at,
            sources=[
                TrackerSource(
                    source_key="docker-origin",
                    source_type="container",
                    source_config={
                        "image": "ghcr.io/acme/runtime-authority",
                        "registry": "ghcr.io",
                    },
                ),
                TrackerSource(
                    source_key="repo-not-primary",
                    source_type="github",
                    source_config={"repo": "owner/new-repo"},
                ),
            ],
        )
    )

    config = await storage.get_tracker_config("aggregate-runtime-authority")

    assert config is not None
    assert config.type == "container"
    assert config.image == "ghcr.io/acme/runtime-authority"
    assert config.registry == "ghcr.io"
    assert config.repo is None
    assert config.interval == 90


@pytest.mark.asyncio
async def test_canonical_release_merges_same_version_observations_with_primary_source_body(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-merge",
            primary_changelog_source_key="repo-primary",
            sources=[
                TrackerSource(
                    source_key="docker-secondary",
                    source_type="container",
                    source_rank=10,
                    source_config={"image": "owner/app", "registry": "ghcr.io"},
                ),
                TrackerSource(
                    source_key="helm-secondary",
                    source_type="helm",
                    source_rank=20,
                    source_config={"repo": "https://charts.example.com", "chart": "app"},
                ),
                TrackerSource(
                    source_key="repo-primary",
                    source_type="github",
                    source_rank=30,
                    source_config={"repo": "owner/app"},
                ),
            ],
        )
    )

    created_at = "2024-03-01T00:00:00+00:00"

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        docker_observation_cursor = await db.execute(
            """
            INSERT INTO source_release_observations
            (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aggregate_tracker.sources[0].id,
                "docker-1.2.3",
                "Docker 1.2.3",
                "1.2.3",
                "1.2.3",
                created_at,
                "https://ghcr.io/owner/app:1.2.3",
                "https://ghcr.io/owner/app/changelog",
                0,
                "docker body",
                "sha-docker",
                "{}",
                created_at,
                created_at,
                created_at,
            ),
        )
        docker_observation_id = docker_observation_cursor.lastrowid

        helm_observation_cursor = await db.execute(
            """
            INSERT INTO source_release_observations
            (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aggregate_tracker.sources[1].id,
                "helm-1.2.3",
                "Helm 1.2.3",
                "app-1.2.3",
                "1.2.3",
                created_at,
                "https://charts.example.com/app-1.2.3.tgz",
                "https://charts.example.com/changelog",
                0,
                "helm body",
                "sha-helm",
                "{}",
                created_at,
                created_at,
                created_at,
            ),
        )
        helm_observation_id = helm_observation_cursor.lastrowid

        repo_observation_cursor = await db.execute(
            """
            INSERT INTO source_release_observations
            (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aggregate_tracker.sources[2].id,
                "repo-v1.2.3",
                "Repository 1.2.3",
                "v1.2.3",
                "1.2.3",
                created_at,
                "https://github.com/owner/app/releases/tag/v1.2.3",
                "https://github.com/owner/app/releases/tag/v1.2.3#notes",
                0,
                "repo body",
                "sha-repo",
                "{}",
                created_at,
                created_at,
                created_at,
            ),
        )
        repo_observation_id = repo_observation_cursor.lastrowid

        await storage._upsert_canonical_release_for_version(
            db, aggregate_tracker.id, "1.2.3", created_at
        )
        await db.commit()

        canonical_rows = list(
            await (
                await db.execute(
                    "SELECT * FROM canonical_releases WHERE aggregate_tracker_id = ?",
                    (aggregate_tracker.id,),
                )
            ).fetchall()
        )
        provenance_rows = list(
            await (
                await db.execute(
                    """
                    SELECT source_release_observation_id, contribution_kind
                    FROM canonical_release_observations
                    WHERE canonical_release_id = ?
                    ORDER BY source_release_observation_id ASC
                    """,
                    (canonical_rows[0]["id"],),
                )
            ).fetchall()
        )

    canonical_releases = await storage.get_canonical_releases("aggregate-merge")
    assert len(canonical_rows) == 1
    assert canonical_rows[0]["canonical_key"] == "1.2.3"
    assert canonical_rows[0]["version"] == "1.2.3"
    assert canonical_rows[0]["primary_observation_id"] == repo_observation_id
    assert canonical_rows[0]["tag_name"] == "v1.2.3"
    assert canonical_rows[0]["url"] == "https://github.com/owner/app/releases/tag/v1.2.3"
    assert (
        canonical_rows[0]["changelog_url"]
        == "https://github.com/owner/app/releases/tag/v1.2.3#notes"
    )
    assert canonical_rows[0]["body"] == "repo body"
    assert len(provenance_rows) == 3
    assert {row["source_release_observation_id"] for row in provenance_rows} == {
        docker_observation_id,
        helm_observation_id,
        repo_observation_id,
    }
    assert {row["contribution_kind"] for row in provenance_rows} == {"primary", "supporting"}

    assert len(canonical_releases) == 1
    assert canonical_releases[0].canonical_key == "1.2.3"
    assert canonical_releases[0].primary_observation_id == repo_observation_id
    assert canonical_releases[0].body == "repo body"
    assert (
        canonical_releases[0].changelog_url
        == "https://github.com/owner/app/releases/tag/v1.2.3#notes"
    )
    assert {
        observation.source_release_observation_id
        for observation in canonical_releases[0].observations
    } == {docker_observation_id, helm_observation_id, repo_observation_id}
    assert {
        observation.contribution_kind for observation in canonical_releases[0].observations
    } == {
        "primary",
        "supporting",
    }


@pytest.mark.asyncio
async def test_canonical_release_prefers_repo_notes_even_when_primary_source_is_non_repo(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-repo-preferred-notes",
            primary_changelog_source_key="helm-primary",
            sources=[
                TrackerSource(
                    source_key="helm-primary",
                    source_type="helm",
                    source_rank=10,
                    source_config={"repo": "https://charts.example.com", "chart": "app"},
                ),
                TrackerSource(
                    source_key="repo-secondary",
                    source_type="github",
                    source_rank=20,
                    source_config={"repo": "owner/app"},
                ),
            ],
        )
    )

    created_at = "2024-04-01T00:00:00+00:00"

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        helm_observation_cursor = await db.execute(
            """
            INSERT INTO source_release_observations
            (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aggregate_tracker.sources[0].id,
                "helm-1.2.3",
                "Helm 1.2.3",
                "app-1.2.3",
                "1.2.3",
                created_at,
                "https://charts.example.com/app-1.2.3.tgz",
                "https://charts.example.com/changelog",
                0,
                "helm body",
                "sha-helm",
                "{}",
                created_at,
                created_at,
                created_at,
            ),
        )
        helm_observation_id = helm_observation_cursor.lastrowid

        repo_observation_cursor = await db.execute(
            """
            INSERT INTO source_release_observations
            (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aggregate_tracker.sources[1].id,
                "repo-v1.2.3",
                "Repository 1.2.3",
                "v1.2.3",
                "1.2.3",
                created_at,
                "https://github.com/owner/app/releases/tag/v1.2.3",
                "https://github.com/owner/app/releases/tag/v1.2.3#notes",
                0,
                "repo body",
                "sha-repo",
                "{}",
                created_at,
                created_at,
                created_at,
            ),
        )
        repo_observation_id = repo_observation_cursor.lastrowid

        await storage._upsert_canonical_release_for_version(
            db, aggregate_tracker.id, "1.2.3", created_at
        )
        await db.commit()

    canonical_releases = await storage.get_canonical_releases("aggregate-repo-preferred-notes")

    assert len(canonical_releases) == 1
    assert canonical_releases[0].primary_observation_id == repo_observation_id
    assert canonical_releases[0].body == "repo body"
    assert (
        canonical_releases[0].changelog_url
        == "https://github.com/owner/app/releases/tag/v1.2.3#notes"
    )
    assert {
        observation.source_release_observation_id
        for observation in canonical_releases[0].observations
    } == {helm_observation_id, repo_observation_id}


@pytest.mark.asyncio
async def test_canonical_release_matches_prefixed_observation_versions_after_normalization(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-prefixed-version-match",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=10,
                    source_config={"repo": "goauthentik/authentik"},
                )
            ],
        )
    )

    created_at = "2026-02-20T00:00:00+00:00"

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        observation_cursor = await db.execute(
            """
            INSERT INTO source_release_observations
            (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aggregate_tracker.sources[0].id,
                "repo-version-2026.2.2",
                "authentik version/2026.2.2",
                "version/2026.2.2",
                "version/2026.2.2",
                created_at,
                "https://github.com/goauthentik/authentik/releases/tag/version/2026.2.2",
                None,
                0,
                "prefixed body",
                "sha-prefixed",
                "{}",
                created_at,
                created_at,
                created_at,
            ),
        )
        observation_id = observation_cursor.lastrowid

        await storage._upsert_canonical_release_for_version(
            db, aggregate_tracker.id, "version/2026.2.2", created_at
        )
        await db.commit()

    canonical_releases = await storage.get_canonical_releases("aggregate-prefixed-version-match")

    assert len(canonical_releases) == 1
    assert canonical_releases[0].canonical_key == "2026.2.2"
    assert canonical_releases[0].version == "2026.2.2"
    assert canonical_releases[0].primary_observation_id == observation_id
    assert canonical_releases[0].body == "prefixed body"


@pytest.mark.asyncio
async def test_canonical_release_merges_docker_alias_family_without_digest(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-docker-alias-family",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=10,
                    source_config={"repo": "owner/app"},
                ),
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=20,
                    source_config={"image": "owner/app", "registry": "ghcr.io"},
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
    release_timestamp = datetime(2025, 5, 1, 12, 0, 0)

    await storage.save_source_observations(
        aggregate_tracker.id,
        repo_source,
        [
            Release(
                tracker_name="aggregate-docker-alias-family",
                tracker_type="github",
                version="24.04.1",
                name="Release 24.04.1",
                tag_name="v24.04.1",
                url="http://example.com/releases/v24.04.1",
                published_at=release_timestamp,
                body="repo body",
                prerelease=False,
            )
        ],
        observed_at=release_timestamp,
    )
    await storage.save_source_observations(
        aggregate_tracker.id,
        image_source,
        [
            Release(
                tracker_name="aggregate-docker-alias-family",
                tracker_type="container",
                version="24.04.1",
                name="latest",
                tag_name="latest",
                url="http://example.com/images/latest",
                published_at=release_timestamp,
                prerelease=False,
                commit_sha=None,
            )
        ],
        observed_at=release_timestamp,
    )

    canonical_releases = await storage.get_canonical_releases("aggregate-docker-alias-family")
    source_observations = await storage.get_source_release_observations(
        "aggregate-docker-alias-family"
    )

    assert len(canonical_releases) == 1
    assert canonical_releases[0].version == "24.04.1"
    assert len(canonical_releases[0].observations) == 2

    docker_observation = next(
        observation
        for observation in source_observations
        if observation.tracker_source_id == image_source.id
    )
    assert docker_observation.tag_name == "latest"
    assert docker_observation.version == "24.04.1"
    assert docker_observation.commit_sha is None


@pytest.mark.asyncio
async def test_canonical_release_removes_stale_docker_alias_target_after_latest_retarget(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-docker-alias-retarget",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=10,
                    source_config={"repo": "owner/app"},
                ),
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=20,
                    source_config={"image": "owner/app", "registry": "ghcr.io"},
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
    first_seen_at = datetime(2025, 6, 1, 12, 0, 0)
    second_seen_at = datetime(2025, 6, 2, 12, 0, 0)

    await storage.save_source_observations(
        aggregate_tracker.id,
        repo_source,
        [
            Release(
                tracker_name="aggregate-docker-alias-retarget",
                tracker_type="github",
                version="24.10.1",
                name="Release 24.10.1",
                tag_name="v24.10.1",
                url="http://example.com/releases/v24.10.1",
                published_at=second_seen_at,
                body="repo body",
                prerelease=False,
            )
        ],
        observed_at=second_seen_at,
    )
    await storage.save_source_observations(
        aggregate_tracker.id,
        image_source,
        [
            Release(
                tracker_name="aggregate-docker-alias-retarget",
                tracker_type="container",
                version="24.04.1",
                name="latest",
                tag_name="latest",
                url="http://example.com/images/latest",
                published_at=first_seen_at,
                prerelease=False,
            )
        ],
        observed_at=first_seen_at,
    )
    await storage.save_source_observations(
        aggregate_tracker.id,
        image_source,
        [
            Release(
                tracker_name="aggregate-docker-alias-retarget",
                tracker_type="container",
                version="24.10.1",
                name="latest",
                tag_name="latest",
                url="http://example.com/images/latest",
                published_at=second_seen_at,
                prerelease=False,
            )
        ],
        observed_at=second_seen_at,
    )

    canonical_releases = await storage.get_canonical_releases("aggregate-docker-alias-retarget")
    source_observations = await storage.get_source_release_observations(
        "aggregate-docker-alias-retarget"
    )

    assert [release.version for release in canonical_releases] == ["24.10.1"]
    docker_observation = next(
        observation
        for observation in source_observations
        if observation.tracker_source_id == image_source.id
    )
    assert docker_observation.tag_name == "latest"
    assert docker_observation.version == "24.10.1"


@pytest.mark.asyncio
async def test_source_observation_refreshes_published_at_for_digestless_docker_alias(
    storage,
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-docker-stable-timestamp",
            primary_changelog_source_key="image",
            sources=[
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=10,
                    source_config={"image": "owner/app", "registry": "ghcr.io"},
                )
            ],
        )
    )
    image_source = aggregate_tracker.sources[0]
    first_seen_at = datetime(2025, 7, 1, 12, 0, 0)
    later_poll_at = datetime(2025, 7, 2, 12, 0, 0)

    await storage.save_source_observations(
        aggregate_tracker.id,
        image_source,
        [
            Release(
                tracker_name="aggregate-docker-stable-timestamp",
                tracker_type="container",
                version="24.10.1",
                name="latest",
                tag_name="latest",
                url="http://example.com/images/latest",
                published_at=first_seen_at,
                prerelease=False,
                commit_sha=None,
            )
        ],
        observed_at=first_seen_at,
    )
    await storage.save_source_observations(
        aggregate_tracker.id,
        image_source,
        [
            Release(
                tracker_name="aggregate-docker-stable-timestamp",
                tracker_type="container",
                version="24.10.1",
                name="latest",
                tag_name="latest",
                url="http://example.com/images/latest",
                published_at=later_poll_at,
                prerelease=False,
                commit_sha=None,
            )
        ],
        observed_at=later_poll_at,
    )

    source_observations = await storage.get_source_release_observations(
        "aggregate-docker-stable-timestamp"
    )

    assert len(source_observations) == 1
    assert source_observations[0].tag_name == "latest"
    assert source_observations[0].version == "24.10.1"
    assert source_observations[0].published_at == later_poll_at
    assert source_observations[0].observed_at == later_poll_at


@pytest.mark.asyncio
async def test_source_observation_preserves_published_at_for_unchanged_gitlab_tag_fallback(
    storage,
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-gitlab-stable-timestamp",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="gitlab",
                    source_rank=10,
                    source_config={
                        "project": "antora/antora",
                        "instance": "https://gitlab.com",
                    },
                )
            ],
        )
    )
    repo_source = aggregate_tracker.sources[0]
    first_seen_at = datetime(2025, 8, 1, 12, 0, 0)
    later_poll_at = datetime(2025, 8, 2, 12, 0, 0)

    await storage.save_source_observations(
        aggregate_tracker.id,
        repo_source,
        [
            Release(
                tracker_name="aggregate-gitlab-stable-timestamp",
                tracker_type="gitlab",
                version="v3.1.0",
                name="v3.1.0",
                tag_name="v3.1.0",
                url="https://gitlab.com/antora/antora/-/tags/v3.1.0",
                published_at=first_seen_at,
                prerelease=False,
                commit_sha="abc123",
            )
        ],
        observed_at=first_seen_at,
    )
    await storage.save_source_observations(
        aggregate_tracker.id,
        repo_source,
        [
            Release(
                tracker_name="aggregate-gitlab-stable-timestamp",
                tracker_type="gitlab",
                version="v3.1.0",
                name="v3.1.0",
                tag_name="v3.1.0",
                url="https://gitlab.com/antora/antora/-/tags/v3.1.0",
                published_at=later_poll_at,
                prerelease=False,
                commit_sha="abc123",
            )
        ],
        observed_at=later_poll_at,
    )

    source_observations = await storage.get_source_release_observations(
        "aggregate-gitlab-stable-timestamp"
    )

    assert len(source_observations) == 1
    assert source_observations[0].tag_name == "v3.1.0"
    assert source_observations[0].version == "v3.1.0"
    assert source_observations[0].commit_sha == "abc123"
    assert source_observations[0].published_at == first_seen_at
    assert source_observations[0].observed_at == later_poll_at


@pytest.mark.asyncio
async def test_save_source_observations_prunes_stale_versions_after_exclude_rule_like_filtering(
    storage,
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-prune-stale-observations",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/app"},
                ),
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=1,
                    source_config={"image": "owner/app", "registry": "ghcr.io"},
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
    first_observed_at = datetime(2025, 9, 1, 12, 0, 0)
    second_observed_at = datetime(2025, 9, 2, 12, 0, 0)

    await storage.save_source_observations(
        aggregate_tracker.id,
        repo_source,
        [
            Release(
                tracker_name="aggregate-prune-stale-observations",
                tracker_type="github",
                version="2.0.0",
                name="Release 2.0.0",
                tag_name="v2.0.0",
                url="http://example.com/releases/v2.0.0",
                published_at=first_observed_at,
                prerelease=False,
            )
        ],
        observed_at=first_observed_at,
    )
    await storage.save_source_observations(
        aggregate_tracker.id,
        image_source,
        [
            Release(
                tracker_name="aggregate-prune-stale-observations",
                tracker_type="container",
                version="3.0.0",
                name="Image 3.0.0",
                tag_name="3.0.0",
                url="http://example.com/images/3.0.0",
                published_at=first_observed_at,
                prerelease=False,
            ),
            Release(
                tracker_name="aggregate-prune-stale-observations",
                tracker_type="container",
                version="3.1.0-riscv64",
                name="Image 3.1.0-riscv64",
                tag_name="3.1.0-riscv64",
                url="http://example.com/images/3.1.0-riscv64",
                published_at=first_observed_at,
                prerelease=False,
            ),
        ],
        observed_at=first_observed_at,
    )

    await storage.save_source_observations(
        aggregate_tracker.id,
        image_source,
        [
            Release(
                tracker_name="aggregate-prune-stale-observations",
                tracker_type="container",
                version="3.0.0",
                name="Image 3.0.0",
                tag_name="3.0.0",
                url="http://example.com/images/3.0.0",
                published_at=second_observed_at,
                prerelease=False,
            )
        ],
        observed_at=second_observed_at,
    )

    source_observations = await storage.get_source_release_observations(
        "aggregate-prune-stale-observations"
    )
    canonical_releases = await storage.get_canonical_releases("aggregate-prune-stale-observations")

    assert {observation.tag_name for observation in source_observations} == {"v2.0.0", "3.0.0"}
    assert all(observation.tag_name != "3.1.0-riscv64" for observation in source_observations)
    assert {release.version for release in canonical_releases} == {"2.0.0", "3.0.0"}


@pytest.mark.asyncio
async def test_canonical_release_uses_helm_app_version_and_keeps_chart_versions_as_metadata(
    storage,
):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-helm-app-version",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/app"},
                ),
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=1,
                    source_config={"image": "owner/app", "registry": "ghcr.io"},
                ),
                TrackerSource(
                    source_key="helm",
                    source_type="helm",
                    source_rank=2,
                    source_config={"repo": "https://charts.example.com", "chart": "app"},
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
    helm_source = next(
        source for source in aggregate_tracker.sources if source.source_key == "helm"
    )
    observed_at = "2025-03-01T00:00:00+00:00"

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT INTO source_release_observations
            (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repo_source.id,
                "repo-v1.2.3",
                "Repo 1.2.3",
                "v1.2.3",
                "1.2.3",
                observed_at,
                "https://github.com/owner/app/releases/tag/v1.2.3",
                None,
                0,
                "repo body",
                None,
                json.dumps({"source_type": "github"}),
                observed_at,
                observed_at,
                observed_at,
            ),
        )
        await db.execute(
            """
            INSERT INTO source_release_observations
            (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                image_source.id,
                "image-1.2.3",
                "Image 1.2.3",
                "1.2.3",
                "1.2.3",
                observed_at,
                "https://ghcr.io/owner/app:1.2.3",
                None,
                0,
                "image body",
                None,
                json.dumps({"source_type": "docker"}),
                observed_at,
                observed_at,
                observed_at,
            ),
        )
        await db.execute(
            """
            INSERT INTO source_release_observations
            (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                helm_source.id,
                "app-1.2.3-chart.1",
                "Helm 1.2.3 chart.1",
                "1.2.3-chart.1",
                "1.2.3",
                observed_at,
                "https://charts.example.com/app-1.2.3-chart.1.tgz",
                None,
                0,
                "helm chart.1 body",
                None,
                json.dumps(
                    {
                        "source_type": "helm",
                        "appVersion": "1.2.3",
                        "chartVersion": "1.2.3-chart.1",
                    }
                ),
                observed_at,
                observed_at,
                observed_at,
            ),
        )
        await db.execute(
            """
            INSERT INTO source_release_observations
            (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                helm_source.id,
                "app-1.2.3-chart.2",
                "Helm 1.2.3 chart.2",
                "1.2.3-chart.2",
                "1.2.3",
                observed_at,
                "https://charts.example.com/app-1.2.3-chart.2.tgz",
                None,
                0,
                "helm chart.2 body",
                None,
                json.dumps(
                    {
                        "source_type": "helm",
                        "appVersion": "1.2.3",
                        "chartVersion": "1.2.3-chart.2",
                    }
                ),
                observed_at,
                observed_at,
                observed_at,
            ),
        )

        await storage._upsert_canonical_release_for_version(
            db, aggregate_tracker.id, "1.2.3", observed_at
        )
        await db.commit()

    canonical_releases = await storage.get_canonical_releases("aggregate-helm-app-version")
    source_observations = await storage.get_source_release_observations(
        "aggregate-helm-app-version"
    )

    assert len(canonical_releases) == 1
    assert canonical_releases[0].canonical_key == "1.2.3"
    assert canonical_releases[0].version == "1.2.3"
    assert canonical_releases[0].tag_name == "v1.2.3"
    assert canonical_releases[0].body == "repo body"
    assert len(canonical_releases[0].observations) == 4

    helm_observations = [
        observation
        for observation in source_observations
        if observation.tracker_source_id == helm_source.id
    ]
    assert len(helm_observations) == 2
    assert {observation.version for observation in helm_observations} == {"1.2.3"}
    assert {observation.tag_name for observation in helm_observations} == {
        "1.2.3-chart.1",
        "1.2.3-chart.2",
    }
    assert {observation.source_release_key for observation in helm_observations} == {
        "app-1.2.3-chart.1",
        "app-1.2.3-chart.2",
    }
    assert {observation.app_version for observation in helm_observations} == {"1.2.3"}
    assert {observation.chart_version for observation in helm_observations} == {
        "1.2.3-chart.1",
        "1.2.3-chart.2",
    }


@pytest.mark.asyncio
async def test_save_source_observations_persists_helm_app_and_chart_version_metadata(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-helm-persistence",
            primary_changelog_source_key="helm",
            sources=[
                TrackerSource(
                    source_key="helm",
                    source_type="helm",
                    source_config={"repo": "https://charts.example.com", "chart": "demo"},
                )
            ],
        )
    )
    helm_source = aggregate_tracker.sources[0]
    helm_tracker = HelmTracker(
        name="aggregate-helm-persistence",
        repo="https://charts.example.com",
        chart="demo",
    )
    observed_at = datetime.fromisoformat("2025-04-01T12:00:00+00:00")

    await storage.save_source_observations(
        aggregate_tracker.id,
        helm_source,
        [
            helm_tracker._parse_chart_version(
                {
                    "version": "1.2.3-chart.7",
                    "appVersion": "1.2.3",
                    "created": observed_at.isoformat(),
                }
            )
        ],
        observed_at=observed_at,
    )

    observations = await storage.get_source_release_observations("aggregate-helm-persistence")
    canonical_releases = await storage.get_canonical_releases("aggregate-helm-persistence")

    assert len(observations) == 1
    assert observations[0].version == "1.2.3"
    assert observations[0].app_version == "1.2.3"
    assert observations[0].tag_name == "1.2.3-chart.7"
    assert observations[0].chart_version == "1.2.3-chart.7"
    assert observations[0].raw_payload["appVersion"] == "1.2.3"
    assert observations[0].raw_payload["chartVersion"] == "1.2.3-chart.7"

    assert len(canonical_releases) == 1
    assert canonical_releases[0].canonical_key == "1.2.3"
    assert canonical_releases[0].version == "1.2.3"
    assert canonical_releases[0].tag_name == "1.2.3-chart.7"


@pytest.mark.asyncio
async def test_backfill_existing_helm_observations_rebuilds_canonicals_idempotently(storage):
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-helm-backfill",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/app"},
                ),
                TrackerSource(
                    source_key="helm",
                    source_type="helm",
                    source_rank=1,
                    source_config={"repo": "https://charts.example.com", "chart": "app"},
                ),
            ],
        )
    )
    repo_source = next(
        source for source in aggregate_tracker.sources if source.source_key == "repo"
    )
    helm_source = next(
        source for source in aggregate_tracker.sources if source.source_key == "helm"
    )
    observed_at = "2025-04-02T00:00:00+00:00"

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        repo_cursor = await db.execute(
            """
            INSERT INTO source_release_observations
            (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repo_source.id,
                "repo-v1.2.3",
                "Repo 1.2.3",
                "v1.2.3",
                "1.2.3",
                observed_at,
                "https://github.com/owner/app/releases/tag/v1.2.3",
                None,
                0,
                "repo body",
                None,
                json.dumps({"source_type": "github"}),
                observed_at,
                observed_at,
                observed_at,
            ),
        )
        repo_observation_id = repo_cursor.lastrowid
        helm_cursor = await db.execute(
            """
            INSERT INTO source_release_observations
            (tracker_source_id, source_release_key, name, tag_name, version, published_at, url, changelog_url, prerelease, body, commit_sha, raw_payload, observed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                helm_source.id,
                "1.2.3-chart.1",
                "Helm 1.2.3 chart.1",
                "1.2.3-chart.1",
                "1.2.3-chart.1",
                observed_at,
                "https://charts.example.com/app-1.2.3-chart.1.tgz",
                None,
                0,
                "helm body",
                None,
                json.dumps(
                    {"source_type": "helm", "appVersion": "1.2.3", "version": "1.2.3-chart.1"}
                ),
                observed_at,
                observed_at,
                observed_at,
            ),
        )
        helm_observation_id = helm_cursor.lastrowid
        repo_canonical_cursor = await db.execute(
            """
            INSERT INTO canonical_releases
            (aggregate_tracker_id, canonical_key, version, name, tag_name, published_at, url, changelog_url, prerelease, body, primary_observation_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aggregate_tracker.id,
                "1.2.3",
                "1.2.3",
                "Repo 1.2.3",
                "v1.2.3",
                observed_at,
                "https://github.com/owner/app/releases/tag/v1.2.3",
                None,
                0,
                "repo body",
                repo_observation_id,
                observed_at,
                observed_at,
            ),
        )
        repo_canonical_id = repo_canonical_cursor.lastrowid
        helm_canonical_cursor = await db.execute(
            """
            INSERT INTO canonical_releases
            (aggregate_tracker_id, canonical_key, version, name, tag_name, published_at, url, changelog_url, prerelease, body, primary_observation_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aggregate_tracker.id,
                "1.2.3-chart.1",
                "1.2.3-chart.1",
                "Helm 1.2.3 chart.1",
                "1.2.3-chart.1",
                observed_at,
                "https://charts.example.com/app-1.2.3-chart.1.tgz",
                None,
                0,
                "helm body",
                helm_observation_id,
                observed_at,
                observed_at,
            ),
        )
        helm_canonical_id = helm_canonical_cursor.lastrowid
        await db.execute(
            "INSERT INTO canonical_release_observations (canonical_release_id, source_release_observation_id, contribution_kind, created_at) VALUES (?, ?, ?, ?)",
            (repo_canonical_id, repo_observation_id, "primary", observed_at),
        )
        await db.execute(
            "INSERT INTO canonical_release_observations (canonical_release_id, source_release_observation_id, contribution_kind, created_at) VALUES (?, ?, ?, ?)",
            (helm_canonical_id, helm_observation_id, "primary", observed_at),
        )
        await db.commit()

    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    await storage._backfill_existing_helm_observations_and_canonicals(db)
    await storage._backfill_existing_helm_observations_and_canonicals(db)
    await db.commit()

    source_observations = await storage.get_source_release_observations("aggregate-helm-backfill")
    canonical_releases = await storage.get_canonical_releases("aggregate-helm-backfill")

    assert len(source_observations) == 2
    repo_observation = next(
        observation
        for observation in source_observations
        if observation.tracker_source_id == repo_source.id
    )
    helm_observation = next(
        observation
        for observation in source_observations
        if observation.tracker_source_id == helm_source.id
    )
    assert repo_observation.version == "1.2.3"
    assert helm_observation.version == "1.2.3"
    assert helm_observation.tag_name == "1.2.3-chart.1"
    assert helm_observation.source_release_key == "1.2.3-chart.1"
    assert helm_observation.app_version == "1.2.3"
    assert helm_observation.chart_version == "1.2.3-chart.1"
    assert helm_observation.raw_payload["chartVersion"] == "1.2.3-chart.1"

    assert len(canonical_releases) == 1
    assert canonical_releases[0].canonical_key == "1.2.3"
    assert canonical_releases[0].primary_observation_id == repo_observation_id
    assert {
        observation.source_release_observation_id
        for observation in canonical_releases[0].observations
    } == {
        repo_observation_id,
        helm_observation_id,
    }
    assert {
        observation.contribution_kind for observation in canonical_releases[0].observations
    } == {
        "primary",
        "supporting",
    }

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        stale_canonical_rows = await (
            await db.execute(
                "SELECT COUNT(*) AS count FROM canonical_releases WHERE aggregate_tracker_id = ? AND canonical_key = ?",
                (aggregate_tracker.id, "1.2.3-chart.1"),
            )
        ).fetchone()
        provenance_rows = list(
            await (
                await db.execute(
                    "SELECT canonical_release_id, source_release_observation_id FROM canonical_release_observations ORDER BY source_release_observation_id ASC"
                )
            ).fetchall()
        )

    assert stale_canonical_rows is not None and stale_canonical_rows["count"] == 0
    assert len(provenance_rows) == 2
