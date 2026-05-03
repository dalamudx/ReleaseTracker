import pytest
import asyncio
import aiosqlite
from datetime import datetime, timedelta, timezone
from typing import Literal, cast
from db_helpers import initialize_storage_with_schema

from releasetracker.config import (
    Channel,
    ExecutorConfig,
    ExecutorServiceBinding,
    RuntimeConnectionConfig,
    TrackerConfig,
)
from releasetracker.models import (
    AggregateTracker,
    Credential,
    Release,
    ReleaseChannel,
    TrackerSource,
    TrackerStatus,
)
from releasetracker.scheduler import ReleaseScheduler
from releasetracker.services.system_keys import SystemKeyManager
from releasetracker.storage.sqlite import SQLiteStorage
from releasetracker.trackers import DockerTracker, GitHubTracker
from releasetracker.trackers.base import BaseTracker


async def _close_storage(storage: SQLiteStorage) -> None:
    await storage.close()
    await asyncio.sleep(0)


async def _create_test_storage(db_path) -> SQLiteStorage:
    key_manager = SystemKeyManager(db_path.parent / f"{db_path.stem}-system-secrets.json")
    await key_manager.initialize()
    return SQLiteStorage(str(db_path), system_key_manager=key_manager)


class FakeTracker(BaseTracker):
    def __init__(self, name: str, releases: list[Release], channels: list[Channel]):
        super().__init__(name, channels=channels)
        self._releases = releases

    async def fetch_latest(self, fallback_tags: bool = False) -> Release | None:
        return self._releases[0] if self._releases else None

    async def fetch_all(self, limit: int = 10, fallback_tags: bool = False) -> list[Release]:
        return list(self._releases)[:limit]


class FallbackTracker(BaseTracker):
    def __init__(self, name: str, latest_release: Release, channels: list[Channel]):
        super().__init__(name, channels=channels)
        self._latest_release = latest_release
        self.fetch_all_calls = 0
        self.fetch_latest_calls = 0
        self.fetch_latest_fallback_tags: list[bool] = []

    async def fetch_latest(self, fallback_tags: bool = False) -> Release | None:
        self.fetch_latest_calls += 1
        self.fetch_latest_fallback_tags.append(fallback_tags)
        return self._latest_release

    async def fetch_all(self, limit: int = 10, fallback_tags: bool = False) -> list[Release]:
        self.fetch_all_calls += 1
        raise RuntimeError("fetch_all unavailable")


class FailingTracker(BaseTracker):
    def __init__(self, name: str, message: str):
        super().__init__(name)
        self.message = message

    async def fetch_latest(self, fallback_tags: bool = False) -> Release | None:
        raise RuntimeError(self.message)

    async def fetch_all(self, limit: int = 10, fallback_tags: bool = False) -> list[Release]:
        raise RuntimeError(self.message)


class BlockingTracker(BaseTracker):
    def __init__(self, name: str, tracker_type: str, gate: asyncio.Event, counters: dict[str, int]):
        super().__init__(name)
        self.tracker_type = tracker_type
        self._gate = gate
        self._counters = counters

    async def fetch_latest(self, fallback_tags: bool = False) -> Release | None:
        return None

    async def fetch_all(self, limit: int = 10, fallback_tags: bool = False) -> list[Release]:
        self._counters["active"] += 1
        self._counters["max_seen"] = max(self._counters["max_seen"], self._counters["active"])
        try:
            await self._gate.wait()
            return []
        finally:
            self._counters["active"] -= 1


def make_release(
    tracker_name: str,
    version: str,
    published_at: datetime,
    prerelease: bool = False,
) -> Release:
    return Release(
        tracker_name=tracker_name,
        tracker_type="github",
        name=f"Release {version}",
        tag_name=version,
        version=version,
        published_at=published_at,
        url=f"http://example.com/{version}",
        prerelease=prerelease,
    )


def make_config(
    name: str,
    channels: list[Channel],
    fetch_limit: int = 10,
    version_sort_mode: Literal["published_at", "semver"] = "semver",
) -> TrackerConfig:
    return TrackerConfig(
        name=name,
        type="github",
        repo="owner/repo",
        enabled=True,
        channels=channels,
        fetch_limit=fetch_limit,
        version_sort_mode=version_sort_mode,
    )


async def _set_primary_source_release_channels(
    storage: SQLiteStorage, tracker_name: str, channels: list[Channel]
) -> None:
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None
    assert aggregate_tracker.sources
    primary_source = aggregate_tracker.sources[0]
    primary_source = primary_source.model_copy(
        update={
            "release_channels": [
                ReleaseChannel(
                    release_channel_key=f"{primary_source.source_key}-{channel.name}",
                    name=cast(Literal["stable", "prerelease", "beta", "canary"], channel.name),
                    type=cast(Literal["release", "prerelease"] | None, channel.type),
                    include_pattern=channel.include_pattern,
                    exclude_pattern=channel.exclude_pattern,
                    enabled=channel.enabled,
                )
                for channel in channels
            ]
        }
    )
    aggregate_tracker = aggregate_tracker.model_copy(
        update={"sources": [primary_source] + aggregate_tracker.sources[1:]}
    )
    await storage.update_aggregate_tracker(aggregate_tracker)


async def _create_immediate_executor_for_tracker(
    storage: SQLiteStorage,
    *,
    tracker_name: str,
    executor_name: str,
) -> int:
    runtime_connection_id = await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name=f"{executor_name}-runtime",
            type="docker",
            enabled=True,
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={"token": "runtime-secret"},
            description="scheduler-test-runtime",
        )
    )
    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None
    assert aggregate_tracker.sources
    primary_source = aggregate_tracker.sources[0]
    assert primary_source.id is not None

    return await storage.save_executor_config(
        ExecutorConfig(
            name=executor_name,
            runtime_type="docker",
            runtime_connection_id=runtime_connection_id,
            tracker_name=tracker_name,
            tracker_source_id=primary_source.id,
            channel_name="stable",
            enabled=True,
            image_selection_mode="replace_tag_on_current_image",
            update_mode="immediate",
            target_ref={"mode": "container", "container_id": f"{executor_name}-container"},
        )
    )


@pytest.mark.asyncio
async def test_projection_winner_change_enqueues_executor_trigger_work(storage, monkeypatch):
    tracker_name = "projection-trigger-changed"
    channels = [Channel(name="stable", type="release")]
    await storage.save_tracker_config(
        make_config(tracker_name, channels, version_sort_mode="semver")
    )
    await _set_primary_source_release_channels(storage, tracker_name, channels)
    executor_id = await _create_immediate_executor_for_tracker(
        storage,
        tracker_name=tracker_name,
        executor_name="projection-trigger-changed-executor",
    )

    release_state = {
        "releases": [
            make_release(
                tracker_name,
                "1.0.0",
                datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
        ]
    }

    scheduler = ReleaseScheduler(storage)

    async def _fake_create_tracker(_config: TrackerConfig):
        return FakeTracker(tracker_name, release_state["releases"], channels)

    monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

    await scheduler._check_tracker(tracker_name)

    first_desired_state = await storage.get_executor_desired_state(executor_id)
    assert first_desired_state is not None
    first_revision = first_desired_state.desired_state_revision
    first_updated_at = first_desired_state.updated_at
    assert first_desired_state.pending is True
    assert first_desired_state.desired_target["current_version"] == "1.0.0"
    assert first_desired_state.desired_target["previous_version"] is None
    assert first_desired_state.desired_target["current_identity_key"] == "1.0.0"

    release_state["releases"] = [
        make_release(
            tracker_name,
            "1.1.0",
            datetime(2024, 2, 1, tzinfo=timezone.utc),
        )
    ]
    await scheduler._check_tracker(tracker_name)

    desired_state = await storage.get_executor_desired_state(executor_id)
    assert desired_state is not None
    assert desired_state.pending is True
    assert desired_state.desired_target["previous_version"] == "1.0.0"
    assert desired_state.desired_target["current_version"] == "1.1.0"
    assert desired_state.desired_target["current_identity_key"] == "1.1.0"
    assert desired_state.desired_state_revision != first_revision
    assert desired_state.updated_at >= first_updated_at

    async with aiosqlite.connect(storage.db_path) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM executor_desired_state WHERE executor_id = ?",
                (executor_id,),
            )
        ).fetchone()
    assert row is not None
    assert row[0] == 1


@pytest.mark.asyncio
async def test_projection_change_enqueues_grouped_executor_for_non_primary_service_binding(storage):
    channels = [Channel(name="stable", type="release")]
    await storage.save_tracker_config(make_config("compose-api-trigger", channels))
    await storage.save_tracker_config(make_config("compose-worker-trigger", channels))
    await _set_primary_source_release_channels(storage, "compose-api-trigger", channels)
    await _set_primary_source_release_channels(storage, "compose-worker-trigger", channels)

    api_tracker = await storage.get_aggregate_tracker("compose-api-trigger")
    worker_tracker = await storage.get_aggregate_tracker("compose-worker-trigger")
    assert api_tracker is not None and api_tracker.sources and api_tracker.sources[0].id is not None
    assert (
        worker_tracker is not None
        and worker_tracker.sources
        and worker_tracker.sources[0].id is not None
    )

    runtime_connection_id = await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name="compose-trigger-runtime",
            type="docker",
            enabled=True,
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={},
        )
    )
    executor_id = await storage.save_executor_config(
        ExecutorConfig(
            name="compose-trigger-executor",
            runtime_type="docker",
            runtime_connection_id=runtime_connection_id,
            tracker_name="compose-api-trigger",
            tracker_source_id=api_tracker.sources[0].id,
            channel_name="stable",
            enabled=True,
            update_mode="immediate",
            target_ref={
                "mode": "docker_compose",
                "project": "release-stack",
                "working_dir": "/srv/release-stack",
                "config_files": ["compose.yml"],
                "services": [
                    {"service": "api", "image": "ghcr.io/acme/api:1.0.0"},
                    {"service": "worker", "image": "ghcr.io/acme/worker:1.0.0"},
                ],
            },
            service_bindings=[
                ExecutorServiceBinding(
                    service="api",
                    tracker_source_id=api_tracker.sources[0].id,
                    channel_name="stable",
                ),
                ExecutorServiceBinding(
                    service="worker",
                    tracker_source_id=worker_tracker.sources[0].id,
                    channel_name="stable",
                ),
            ],
        )
    )

    scheduler = ReleaseScheduler(storage)
    queued_count = await scheduler._emit_executor_trigger_work_for_projection_change(
        tracker_name="compose-worker-trigger",
        previous_version="1.0.0",
        current_version="2.0.0",
        previous_identity_key="1.0.0",
        current_identity_key="2.0.0",
    )

    desired_state = await storage.get_executor_desired_state(executor_id)
    assert queued_count == 1
    assert desired_state is not None
    assert desired_state.pending is True
    assert desired_state.desired_target["tracker_name"] == "compose-worker-trigger"


@pytest.mark.asyncio
async def test_projection_winner_unchanged_emits_no_executor_trigger_work(storage, monkeypatch):
    tracker_name = "projection-trigger-unchanged"
    channels = [Channel(name="stable", type="release")]
    await storage.save_tracker_config(
        make_config(tracker_name, channels, version_sort_mode="semver")
    )
    await _set_primary_source_release_channels(storage, tracker_name, channels)
    executor_id = await _create_immediate_executor_for_tracker(
        storage,
        tracker_name=tracker_name,
        executor_name="projection-trigger-unchanged-executor",
    )

    stable_release = make_release(
        tracker_name,
        "1.0.0",
        datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    scheduler = ReleaseScheduler(storage)

    async def _fake_create_tracker(_config: TrackerConfig):
        return FakeTracker(tracker_name, [stable_release], channels)

    monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

    await scheduler._check_tracker(tracker_name)
    initial_desired_state = await storage.get_executor_desired_state(executor_id)
    assert initial_desired_state is not None

    enqueue_calls = 0
    original_enqueue_executor_projection_trigger_work = (
        storage.enqueue_executor_projection_trigger_work
    )

    async def _tracking_enqueue_executor_projection_trigger_work(
        *,
        executor_id: int,
        tracker_name: str,
        previous_version: str | None,
        current_version: str,
        previous_identity_key: str | None = None,
        current_identity_key: str | None = None,
    ):
        nonlocal enqueue_calls
        enqueue_calls += 1
        return await original_enqueue_executor_projection_trigger_work(
            executor_id=executor_id,
            tracker_name=tracker_name,
            previous_version=previous_version,
            current_version=current_version,
            previous_identity_key=previous_identity_key,
            current_identity_key=current_identity_key,
        )

    monkeypatch.setattr(
        storage,
        "enqueue_executor_projection_trigger_work",
        _tracking_enqueue_executor_projection_trigger_work,
    )

    await scheduler._check_tracker(tracker_name)

    unchanged_desired_state = await storage.get_executor_desired_state(executor_id)
    assert unchanged_desired_state is not None
    assert enqueue_calls == 0
    assert (
        unchanged_desired_state.desired_state_revision
        == initial_desired_state.desired_state_revision
    )
    assert unchanged_desired_state.updated_at == initial_desired_state.updated_at


@pytest.mark.asyncio
async def test_projection_trigger_enqueue_happens_after_refresh_commit(storage, monkeypatch):
    tracker_name = "projection-trigger-commit-order"
    channels = [Channel(name="stable", type="release")]
    await storage.save_tracker_config(
        make_config(tracker_name, channels, version_sort_mode="semver")
    )
    await _set_primary_source_release_channels(storage, tracker_name, channels)
    await _create_immediate_executor_for_tracker(
        storage,
        tracker_name=tracker_name,
        executor_name="projection-trigger-commit-order-executor",
    )

    scheduler = ReleaseScheduler(storage)

    async def _fake_create_tracker(_config: TrackerConfig):
        return FakeTracker(
            tracker_name,
            [
                make_release(
                    tracker_name,
                    "2.0.0",
                    datetime(2024, 3, 1, tzinfo=timezone.utc),
                )
            ],
            channels,
        )

    monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

    aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
    assert aggregate_tracker is not None and aggregate_tracker.id is not None

    events: list[str] = []
    projection_versions_observed_during_enqueue: list[list[str]] = []

    original_refresh_tracker_current_releases = storage.refresh_tracker_current_releases
    original_enqueue_executor_projection_trigger_work = (
        storage.enqueue_executor_projection_trigger_work
    )

    async def _tracking_refresh_tracker_current_releases(*args, **kwargs):
        events.append("refresh:start")
        result = await original_refresh_tracker_current_releases(*args, **kwargs)
        events.append("refresh:committed")
        return result

    async def _tracking_enqueue_executor_projection_trigger_work(
        *,
        executor_id: int,
        tracker_name: str,
        previous_version: str | None,
        current_version: str,
        previous_identity_key: str | None = None,
        current_identity_key: str | None = None,
    ):
        projection_versions = await storage.get_tracker_current_releases(aggregate_tracker.id)
        projection_versions_observed_during_enqueue.append(
            [release.version for release in projection_versions]
        )
        events.append("enqueue")
        return await original_enqueue_executor_projection_trigger_work(
            executor_id=executor_id,
            tracker_name=tracker_name,
            previous_version=previous_version,
            current_version=current_version,
            previous_identity_key=previous_identity_key,
            current_identity_key=current_identity_key,
        )

    monkeypatch.setattr(
        storage,
        "refresh_tracker_current_releases",
        _tracking_refresh_tracker_current_releases,
    )
    monkeypatch.setattr(
        storage,
        "enqueue_executor_projection_trigger_work",
        _tracking_enqueue_executor_projection_trigger_work,
    )

    await scheduler._check_tracker(tracker_name)

    assert events.index("refresh:committed") < events.index("enqueue")
    assert projection_versions_observed_during_enqueue
    assert "2.0.0" in projection_versions_observed_during_enqueue[0]


@pytest.mark.asyncio
async def test_create_tracker_dispatches_using_single_tracker_type_field(storage):
    scheduler = ReleaseScheduler(storage)
    channels = [Channel(name="stable", type="release")]

    github_tracker = await scheduler._create_tracker(
        TrackerConfig(
            name="legacy-github",
            type="github",
            repo="owner/repo",
            enabled=True,
            channels=channels,
        )
    )
    docker_tracker = await scheduler._create_tracker(
        TrackerConfig(
            name="legacy-docker",
            type="container",
            image="ghcr.io/acme/app",
            enabled=True,
            channels=channels,
        )
    )

    assert isinstance(github_tracker, GitHubTracker)
    assert github_tracker.repo == "owner/repo"
    assert isinstance(docker_tracker, DockerTracker)
    assert docker_tracker.image == "ghcr.io/acme/app"


@pytest.mark.asyncio
async def test_create_container_tracker_uses_registry_username_and_password_secret(storage):
    await storage.create_credential(
        Credential(
            name="registry-pat",
            type="docker",
            secrets={"username": "registry-user", "password": "registry-pat-value"},
        )
    )
    scheduler = ReleaseScheduler(storage)

    tracker = await scheduler._create_tracker(
        TrackerConfig(
            name="registry-auth-tracker",
            type="container",
            image="owner/image",
            registry="registry-1.docker.io",
            credential_name="registry-pat",
        )
    )

    assert isinstance(tracker, DockerTracker)
    assert tracker.token == "registry-user:registry-pat-value"


@pytest.mark.asyncio
async def test_auto_check_persists_all_matching_releases_and_projects_channel_winners(
    storage, monkeypatch
):
    tracker_name = "semver-auto"
    channels = [
        Channel(name="stable", type="release"),
        Channel(name="prerelease", type="prerelease"),
    ]
    config = make_config(tracker_name, channels, fetch_limit=10, version_sort_mode="semver")
    await storage.save_tracker_config(config)
    await _set_primary_source_release_channels(storage, tracker_name, channels)

    releases = [
        make_release(
            tracker_name,
            "3.12.1",
            datetime(2024, 1, 10, tzinfo=timezone.utc),
        ),
        make_release(
            tracker_name,
            "3.13.2",
            datetime(2024, 1, 11, tzinfo=timezone.utc),
        ),
        make_release(
            tracker_name,
            "3.12.2",
            datetime(2024, 2, 10, tzinfo=timezone.utc),
        ),
        make_release(
            tracker_name,
            "4.0.0-alpha.1",
            datetime(2024, 2, 11, tzinfo=timezone.utc),
            prerelease=True,
        ),
    ]

    scheduler = ReleaseScheduler(storage)

    async def _fake_create_tracker(_config):
        return FakeTracker(tracker_name, releases, channels)

    monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

    status = await scheduler._check_tracker(tracker_name)

    history = await storage.get_releases(tracker_name, limit=100, include_history=True)
    current = await storage.get_releases(tracker_name, limit=100, include_history=False)

    assert status is not None
    assert status.last_version == "4.0.0-alpha.1"
    assert {release.version for release in history} == {
        "3.12.1",
        "3.13.2",
        "3.12.2",
        "4.0.0-alpha.1",
    }
    assert {release.version for release in current} == {"3.13.2", "4.0.0-alpha.1"}

    stable = [r for r in current if r.channel_name == "stable"]
    prerelease = [r for r in current if r.channel_name == "prerelease"]
    assert len(stable) == 1
    assert stable[0].version == "3.13.2"
    assert len(prerelease) == 1
    assert prerelease[0].version == "4.0.0-alpha.1"


@pytest.mark.asyncio
async def test_auto_check_stable_semver_beats_later_lower_branch(storage, monkeypatch):
    tracker_name = "semver-stable-priority"
    channels = [Channel(name="stable", type="release")]
    config = make_config(tracker_name, channels, fetch_limit=10, version_sort_mode="semver")
    await storage.save_tracker_config(config)
    await _set_primary_source_release_channels(storage, tracker_name, channels)

    releases = [
        make_release(
            tracker_name,
            "3.12.1",
            datetime(2024, 1, 10, tzinfo=timezone.utc),
        ),
        make_release(
            tracker_name,
            "3.13.2",
            datetime(2024, 1, 11, tzinfo=timezone.utc),
        ),
        make_release(
            tracker_name,
            "3.12.2",
            datetime(2024, 2, 10, tzinfo=timezone.utc),
        ),
    ]

    scheduler = ReleaseScheduler(storage)

    async def _fake_create_tracker(_config):
        return FakeTracker(tracker_name, releases, channels)

    monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

    status = await scheduler._check_tracker(tracker_name)
    persisted = await storage.get_releases(tracker_name, limit=100, include_history=False)

    assert status is not None
    assert status.last_version == "3.13.2"
    assert [release.version for release in persisted] == ["3.13.2"]
    assert persisted[0].channel_name == "stable"


@pytest.mark.asyncio
async def test_manual_check_respects_fetch_limit_as_candidate_depth(tmp_path, monkeypatch):
    tracker_name = "limit-manual"
    channels = [Channel(name="stable", type="release")]
    config = make_config(tracker_name, channels, fetch_limit=10, version_sort_mode="semver")

    db_path = tmp_path / "manual.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        await storage.save_tracker_config(config)
        await _set_primary_source_release_channels(storage, tracker_name, channels)

        releases = [
            make_release(
                tracker_name,
                f"1.0.{patch}",
                datetime(2024, 3, 1 + patch, tzinfo=timezone.utc),
            )
            for patch in range(10)
        ]

        scheduler = ReleaseScheduler(storage)

        async def _fake_create_tracker(_config):
            return FakeTracker(tracker_name, releases, channels)

        monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

        await scheduler.check_tracker_now_v2(tracker_name)

        persisted = await storage.get_releases(tracker_name, limit=100, include_history=False)
        assert len(persisted) == 1
        assert persisted[0].version == "1.0.9"
    finally:
        await _close_storage(storage)


@pytest.mark.asyncio
async def test_auto_check_main_table_growth_matches_current_channel_winners(storage, monkeypatch):
    tracker_name = "winner-growth"
    channels = [
        Channel(name="stable", type="release"),
        Channel(name="prerelease", type="prerelease"),
    ]
    config = make_config(tracker_name, channels, fetch_limit=10, version_sort_mode="semver")
    await storage.save_tracker_config(config)
    await _set_primary_source_release_channels(storage, tracker_name, channels)

    fetched_releases = [
        make_release(
            tracker_name,
            f"1.0.{patch}",
            datetime(2024, 5, patch + 1, tzinfo=timezone.utc),
        )
        for patch in range(5)
    ] + [
        make_release(
            tracker_name,
            f"2.0.0-beta.{patch}",
            datetime(2024, 5, patch + 6, tzinfo=timezone.utc),
            prerelease=True,
        )
        for patch in range(5)
    ]

    scheduler = ReleaseScheduler(storage)

    async def _fake_create_tracker(_config):
        return FakeTracker(tracker_name, fetched_releases, channels)

    monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

    await scheduler._check_tracker(tracker_name)

    history = await storage.get_releases(tracker_name, limit=100, include_history=True)
    current = await storage.get_releases(tracker_name, limit=100, include_history=False)

    assert len(history) == 10
    assert {release.version for release in history} == {
        *(f"1.0.{patch}" for patch in range(5)),
        *(f"2.0.0-beta.{patch}" for patch in range(5)),
    }
    assert len(current) == 2
    assert {release.version for release in current} == {"1.0.4", "2.0.0-beta.4"}


@pytest.mark.asyncio
async def test_auto_and_manual_checks_persist_identical_winners(tmp_path, monkeypatch):
    tracker_name = "parity"
    channels = [
        Channel(name="stable", type="release"),
        Channel(name="prerelease", type="prerelease"),
    ]
    config = make_config(tracker_name, channels, fetch_limit=10, version_sort_mode="semver")

    releases = [
        make_release(
            tracker_name,
            "3.12.1",
            datetime(2024, 1, 10, tzinfo=timezone.utc),
        ),
        make_release(
            tracker_name,
            "3.13.2",
            datetime(2024, 1, 11, tzinfo=timezone.utc),
        ),
        make_release(
            tracker_name,
            "3.12.2",
            datetime(2024, 2, 10, tzinfo=timezone.utc),
        ),
        make_release(
            tracker_name,
            "4.0.0-alpha.1",
            datetime(2024, 2, 11, tzinfo=timezone.utc),
            prerelease=True,
        ),
    ]

    auto_db = tmp_path / "auto.db"
    auto_storage = await _create_test_storage(auto_db)
    await initialize_storage_with_schema(auto_storage)
    manual_db = tmp_path / "manual.db"
    manual_storage = await _create_test_storage(manual_db)
    await initialize_storage_with_schema(manual_storage)
    try:
        await auto_storage.save_tracker_config(config)
        await _set_primary_source_release_channels(auto_storage, tracker_name, channels)

        auto_scheduler = ReleaseScheduler(auto_storage)

        async def _fake_auto_create_tracker(_config):
            return FakeTracker(tracker_name, releases, channels)

        monkeypatch.setattr(auto_scheduler, "_create_tracker", _fake_auto_create_tracker)
        await auto_scheduler._check_tracker(tracker_name)

        auto_persisted = await auto_storage.get_releases(
            tracker_name, limit=100, include_history=False
        )
        auto_versions = {release.version for release in auto_persisted}

        await manual_storage.save_tracker_config(config)
        await _set_primary_source_release_channels(manual_storage, tracker_name, channels)

        manual_scheduler = ReleaseScheduler(manual_storage)

        async def _fake_create_tracker(_config):
            return FakeTracker(tracker_name, releases, channels)

        monkeypatch.setattr(manual_scheduler, "_create_tracker", _fake_create_tracker)
        await manual_scheduler.check_tracker_now_v2(tracker_name)

        manual_persisted = await manual_storage.get_releases(
            tracker_name, limit=100, include_history=False
        )
        manual_versions = {release.version for release in manual_persisted}

        expected_versions = {"3.13.2", "4.0.0-alpha.1"}
        assert auto_versions == manual_versions
        assert auto_versions == expected_versions
    finally:
        await _close_storage(auto_storage)
        await _close_storage(manual_storage)


@pytest.mark.asyncio
async def test_manual_check_selects_independent_channel_winners_from_history(tmp_path, monkeypatch):
    tracker_name = "history-channel-winners"
    channels = [
        Channel(name="stable", type="release"),
        Channel(name="prerelease", type="prerelease"),
    ]
    config = make_config(tracker_name, channels, fetch_limit=10, version_sort_mode="semver")

    db_path = tmp_path / "history-channel-winners.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        await storage.save_tracker_config(config)
        await _set_primary_source_release_channels(storage, tracker_name, channels)
        aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
        assert aggregate_tracker is not None and aggregate_tracker.id is not None
        runtime_source = storage._select_runtime_source(aggregate_tracker)
        assert runtime_source is not None

        await storage.save_source_observations(
            aggregate_tracker.id,
            runtime_source,
            [
                make_release(
                    tracker_name,
                    "3.13.2",
                    datetime(2024, 1, 11, tzinfo=timezone.utc),
                ).model_copy(update={"channel_name": "stable"}),
                make_release(
                    tracker_name,
                    "4.0.0-alpha.1",
                    datetime(2024, 2, 11, tzinfo=timezone.utc),
                    prerelease=True,
                ).model_copy(update={"channel_name": "prerelease"}),
            ],
            observed_at=datetime(2024, 2, 11, tzinfo=timezone.utc),
        )

        current_releases = [
            make_release(
                tracker_name,
                "3.12.2",
                datetime(2024, 3, 1, tzinfo=timezone.utc),
            ),
            make_release(
                tracker_name,
                "4.0.0-alpha.0",
                datetime(2024, 3, 2, tzinfo=timezone.utc),
                prerelease=True,
            ),
        ]

        scheduler = ReleaseScheduler(storage)

        async def _fake_create_tracker(_config):
            return FakeTracker(tracker_name, current_releases, channels)

        monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

        status = await scheduler.check_tracker_now_v2(tracker_name)
        persisted = await storage.get_releases(tracker_name, limit=100, include_history=False)

        assert status.last_version == "4.0.0-alpha.1"
        assert {release.version for release in persisted} >= {
            "3.13.2",
            "4.0.0-alpha.1",
        }
    finally:
        await _close_storage(storage)


@pytest.mark.asyncio
async def test_auto_and_manual_checks_share_fallback_selection_pipeline(tmp_path, monkeypatch):
    tracker_name = "fallback-parity"
    channels = [Channel(name="stable", type="release")]
    config = make_config(tracker_name, channels, fetch_limit=5, version_sort_mode="semver")

    fallback_release = make_release(
        tracker_name,
        "2.4.0",
        datetime(2024, 4, 1, tzinfo=timezone.utc),
    )

    auto_db = tmp_path / "fallback-auto.db"
    auto_storage = await _create_test_storage(auto_db)
    await initialize_storage_with_schema(auto_storage)
    manual_db = tmp_path / "fallback-manual.db"
    manual_storage = await _create_test_storage(manual_db)
    await initialize_storage_with_schema(manual_storage)
    try:
        await auto_storage.save_tracker_config(config)
        await _set_primary_source_release_channels(auto_storage, tracker_name, channels)

        auto_scheduler = ReleaseScheduler(auto_storage)
        auto_tracker = FallbackTracker(tracker_name, fallback_release, channels)

        async def _fake_auto_create_tracker(_config):
            return auto_tracker

        monkeypatch.setattr(auto_scheduler, "_create_tracker", _fake_auto_create_tracker)

        auto_status = await auto_scheduler._check_tracker(tracker_name)
        auto_persisted = await auto_storage.get_releases(
            tracker_name, limit=100, include_history=False
        )
        assert auto_status is not None

        await manual_storage.save_tracker_config(config)
        await _set_primary_source_release_channels(manual_storage, tracker_name, channels)

        manual_scheduler = ReleaseScheduler(manual_storage)
        manual_tracker = FallbackTracker(
            tracker_name, fallback_release.model_copy(deep=True), channels
        )

        async def _fake_create_tracker(_config):
            return manual_tracker

        monkeypatch.setattr(manual_scheduler, "_create_tracker", _fake_create_tracker)

        manual_status = await manual_scheduler.check_tracker_now_v2(tracker_name)
        manual_persisted = await manual_storage.get_releases(
            tracker_name, limit=100, include_history=False
        )

        assert auto_tracker.fetch_all_calls == 1
        assert auto_tracker.fetch_latest_calls == 1
        assert auto_tracker.fetch_latest_fallback_tags == [False]
        assert manual_tracker.fetch_all_calls == 1
        assert manual_tracker.fetch_latest_calls == 1
        assert manual_tracker.fetch_latest_fallback_tags == [False]

        assert [release.version for release in auto_persisted] == ["2.4.0"]
        assert [release.version for release in manual_persisted] == ["2.4.0"]
        assert auto_persisted[0].channel_name == "stable"
        assert manual_persisted[0].channel_name == "stable"
        assert auto_status.last_version == manual_status.last_version == "2.4.0"
        assert auto_status.error is None
        assert manual_status.error is None
    finally:
        await _close_storage(auto_storage)
        await _close_storage(manual_storage)


@pytest.mark.asyncio
async def test_scheduler_passes_fallback_tags_to_fetch_latest_when_supported(tmp_path, monkeypatch):
    tracker_name = "gitlab-fallback-fetch-latest"
    channels = [Channel(name="stable", type="release")]
    config = TrackerConfig(
        name=tracker_name,
        type="gitlab",
        project="antora/antora",
        enabled=True,
        channels=channels,
        fallback_tags=True,
        fetch_limit=5,
    )
    db_path = tmp_path / "gitlab-fallback-fetch-latest.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        await storage.save_tracker_config(config)

        scheduler = ReleaseScheduler(storage)
        fallback_release = Release(
            tracker_name=tracker_name,
            tracker_type="gitlab",
            name="Release v3.1.0",
            tag_name="v3.1.0",
            version="v3.1.0",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            url="https://gitlab.com/antora/antora/-/tags/v3.1.0",
            prerelease=False,
        )
        tracker = FallbackTracker(tracker_name, fallback_release, channels)

        async def _fake_create_tracker(_config):
            return tracker

        monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

        status = await scheduler.check_tracker_now_v2(tracker_name)
        persisted = await storage.get_releases(tracker_name, limit=10, include_history=False)

        assert tracker.fetch_all_calls == 1
        assert tracker.fetch_latest_calls == 1
        assert tracker.fetch_latest_fallback_tags == [True]
        assert status.error is None
        assert [release.version for release in persisted] == ["v3.1.0"]
    finally:
        await _close_storage(storage)


@pytest.mark.asyncio
async def test_auto_check_uses_independent_regex_rules_per_enabled_channel(storage, monkeypatch):
    tracker_name = "independent-channel-regex"
    channels = [
        Channel(name="beta", type="release", include_pattern=r"arm64", enabled=False),
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
    ]
    config = make_config(tracker_name, channels, fetch_limit=10, version_sort_mode="semver")
    await storage.save_tracker_config(config)
    await _set_primary_source_release_channels(storage, tracker_name, channels)

    releases = [
        make_release(
            tracker_name,
            "v1.2.0",
            datetime(2024, 9, 1, tzinfo=timezone.utc),
        ),
        make_release(
            tracker_name,
            "v1.3.0-arm64",
            datetime(2024, 9, 2, tzinfo=timezone.utc),
        ),
        make_release(
            tracker_name,
            "v2.0.0-beta.1",
            datetime(2024, 9, 3, tzinfo=timezone.utc),
            prerelease=True,
        ),
        make_release(
            tracker_name,
            "v2.0.0-beta.2-arm64",
            datetime(2024, 9, 4, tzinfo=timezone.utc),
            prerelease=True,
        ),
        make_release(
            tracker_name,
            "v2.0.0-rc.1",
            datetime(2024, 9, 5, tzinfo=timezone.utc),
            prerelease=True,
        ),
    ]

    scheduler = ReleaseScheduler(storage)

    async def _fake_create_tracker(_config):
        return FakeTracker(tracker_name, releases, channels)

    monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

    status = await scheduler._check_tracker(tracker_name)
    persisted = await storage.get_releases(tracker_name, limit=100, include_history=False)

    assert status is not None
    assert status.last_version == "v2.0.0-beta.1"
    assert {release.channel_name for release in persisted} == {"stable", "prerelease"}
    assert {release.channel_name: release.version for release in persisted} == {
        "stable": "v1.2.0",
        "prerelease": "v2.0.0-beta.1",
    }


@pytest.mark.asyncio
async def test_create_tracker_does_not_copy_first_enabled_channel_regex_into_legacy_filter(storage):
    scheduler = ReleaseScheduler(storage)
    channels = [
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
        ),
    ]

    tracker = await scheduler._create_tracker(
        TrackerConfig(
            name="channel-owned-regex",
            type="container",
            image="ghcr.io/acme/app",
            enabled=True,
            channels=channels,
        )
    )

    assert tracker.config.get("filter", {}) == {}



@pytest.mark.asyncio
async def test_docker_digest_distinct_identities_not_collapsed_in_scheduler_truth_and_projection(
    tmp_path, monkeypatch
):
    tracker_name = "docker-digest-distinct"
    db_path = tmp_path / "docker-digest-distinct.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        await storage.save_tracker_config(
            TrackerConfig(
                name=tracker_name,
                type="container",
                image="ghcr.io/acme/app",
                registry="ghcr.io",
                enabled=True,
                channels=[],
                fetch_limit=10,
                version_sort_mode="published_at",
            )
        )

        scheduler = ReleaseScheduler(storage)
        digest_old = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
        digest_new = "sha256:2222222222222222222222222222222222222222222222222222222222222222"
        digest_releases = [
            Release(
                tracker_name=tracker_name,
                tracker_type="container",
                name="app:1.2.3",
                tag_name="1.2.3",
                version="1.2.3",
                published_at=datetime(2024, 9, 1, tzinfo=timezone.utc),
                url="http://example.com/app/1.2.3/old",
                prerelease=False,
                commit_sha=digest_old,
            ),
            Release(
                tracker_name=tracker_name,
                tracker_type="container",
                name="app:1.2.3",
                tag_name="1.2.3",
                version="1.2.3",
                published_at=datetime(2024, 9, 2, tzinfo=timezone.utc),
                url="http://example.com/app/1.2.3/new",
                prerelease=False,
                commit_sha=digest_new,
            ),
        ]

        async def _fake_create_tracker(_config):
            return FakeTracker(tracker_name, digest_releases, [])

        monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

        status = await scheduler._check_tracker(tracker_name)
        persisted_current = await storage.get_releases(
            tracker_name, limit=100, include_history=False
        )

        aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
        assert aggregate_tracker is not None
        runtime_source = storage._select_runtime_source(aggregate_tracker)
        assert runtime_source is not None and runtime_source.id is not None

        async with aiosqlite.connect(storage.db_path) as db:
            db.row_factory = aiosqlite.Row
            source_history_rows = await (
                await db.execute(
                    "SELECT identity_key, digest FROM source_release_history WHERE tracker_source_id = ? ORDER BY identity_key ASC",
                    (runtime_source.id,),
                )
            ).fetchall()
            source_history_rows = list(source_history_rows)
            tracker_history_rows = await (
                await db.execute(
                    "SELECT identity_key, digest FROM tracker_release_history WHERE aggregate_tracker_id = ? ORDER BY identity_key ASC",
                    (aggregate_tracker.id,),
                )
            ).fetchall()
            tracker_history_rows = list(tracker_history_rows)
            tracker_current_rows = await (
                await db.execute(
                    "SELECT identity_key, digest FROM tracker_current_releases WHERE aggregate_tracker_id = ? ORDER BY identity_key ASC",
                    (aggregate_tracker.id,),
                )
            ).fetchall()
            tracker_current_rows = list(tracker_current_rows)

        assert status is not None
        assert status.last_version == "1.2.3"
        assert len(source_history_rows) == 2
        assert len(tracker_history_rows) == 2
        assert len(tracker_current_rows) == 2
        assert {row["digest"] for row in source_history_rows} == {digest_old, digest_new}
        assert {row["digest"] for row in tracker_history_rows} == {digest_old, digest_new}
        assert {row["digest"] for row in tracker_current_rows} == {digest_old, digest_new}
        assert {release.commit_sha for release in persisted_current} == {digest_old, digest_new}
    finally:
        await _close_storage(storage)


@pytest.mark.asyncio
async def test_local_rebuild_does_not_append_truth_or_create_bootstrap_runs(tmp_path, monkeypatch):
    tracker_name = "local-rebuild-no-truth-write"
    db_path = tmp_path / "local-rebuild-no-truth-write.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        aggregate_tracker = await storage.create_aggregate_tracker(
            AggregateTracker(
                name=tracker_name,
                primary_changelog_source_key="repo-primary",
                sources=[
                    TrackerSource(
                        source_key="repo-primary",
                        source_type="github",
                        source_rank=0,
                        source_config={"repo": "owner/rebuild-app"},
                        release_channels=[
                            ReleaseChannel(
                                release_channel_key="repo-primary-stable",
                                name="stable",
                                type="release",
                            )
                        ],
                    ),
                    TrackerSource(
                        source_key="image-disabled",
                        source_type="container",
                        enabled=False,
                        source_rank=10,
                        source_config={
                            "image": "ghcr.io/owner/rebuild-app",
                            "registry": "ghcr.io",
                        },
                    ),
                ],
            )
        )
        assert aggregate_tracker.id is not None

        repo_source = next(
            source for source in aggregate_tracker.sources if source.source_key == "repo-primary"
        )
        seeded_release = make_release(
            tracker_name,
            "2.0.0",
            datetime(2024, 10, 1, tzinfo=timezone.utc),
        ).model_copy(update={"channel_name": "stable"})
        await storage.save_source_observations(
            aggregate_tracker.id,
            repo_source,
            [seeded_release],
            observed_at=datetime(2024, 10, 1, tzinfo=timezone.utc),
        )
        assert repo_source.id is not None
        source_history_id = await storage.get_source_release_history_id(
            repo_source.id,
            storage.release_identity_key_for_source(
                seeded_release, source_type=repo_source.source_type
            ),
        )
        assert source_history_id is not None
        await storage.upsert_tracker_release_history(
            aggregate_tracker.id,
            seeded_release,
            primary_source_release_history_id=source_history_id,
            source_type=repo_source.source_type,
        )

        async with aiosqlite.connect(storage.db_path) as db:
            before_fetch_runs = await (
                await db.execute("SELECT COUNT(*) FROM source_fetch_runs")
            ).fetchone()
            before_source_history = await (
                await db.execute("SELECT COUNT(*) FROM source_release_history")
            ).fetchone()

        scheduler = ReleaseScheduler(storage)

        async def _fake_create_tracker(_config):
            return FakeTracker(
                tracker_name,
                [
                    make_release(
                        tracker_name,
                        "2.0.0",
                        datetime(2024, 10, 1, tzinfo=timezone.utc),
                    )
                ],
                [],
            )

        monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

        status = await scheduler.rebuild_tracker_views_from_storage(tracker_name)

        async with aiosqlite.connect(storage.db_path) as db:
            after_fetch_runs = await (
                await db.execute("SELECT COUNT(*) FROM source_fetch_runs")
            ).fetchone()
            after_source_history = await (
                await db.execute("SELECT COUNT(*) FROM source_release_history")
            ).fetchone()

        assert status.last_version == "2.0.0"
        assert before_fetch_runs is not None and after_fetch_runs is not None
        assert before_source_history is not None and after_source_history is not None
        assert after_fetch_runs[0] == before_fetch_runs[0]
        assert after_source_history[0] == before_source_history[0]
    finally:
        await _close_storage(storage)


@pytest.mark.asyncio
async def test_projection_rebuild_from_truth_is_invariant(tmp_path, monkeypatch):
    tracker_name = "projection-rebuild-invariant"
    db_path = tmp_path / "projection-rebuild-invariant.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        await storage.save_tracker_config(
            TrackerConfig(
                name=tracker_name,
                type="container",
                image="ghcr.io/acme/app",
                registry="ghcr.io",
                enabled=True,
                channels=[],
                fetch_limit=10,
                version_sort_mode="published_at",
            )
        )

        scheduler = ReleaseScheduler(storage)
        releases = [
            Release(
                tracker_name=tracker_name,
                tracker_type="container",
                name="app:1.2.3",
                tag_name="1.2.3",
                version="1.2.3",
                published_at=datetime(2024, 9, 1, tzinfo=timezone.utc),
                url="http://example.com/app/1.2.3/old",
                prerelease=False,
                commit_sha="sha256:1111111111111111111111111111111111111111111111111111111111111111",
            ),
            Release(
                tracker_name=tracker_name,
                tracker_type="container",
                name="app:1.2.3",
                tag_name="1.2.3",
                version="1.2.3",
                published_at=datetime(2024, 9, 2, tzinfo=timezone.utc),
                url="http://example.com/app/1.2.3/new",
                prerelease=False,
                commit_sha="sha256:2222222222222222222222222222222222222222222222222222222222222222",
            ),
        ]

        async def _fake_create_tracker(_config):
            return FakeTracker(tracker_name, releases, [])

        monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

        status = await scheduler._check_tracker(tracker_name)
        aggregate_tracker = await storage.get_aggregate_tracker(tracker_name)
        assert aggregate_tracker is not None and aggregate_tracker.id is not None

        history_releases = await storage.get_tracker_release_history_releases(aggregate_tracker.id)
        await storage.refresh_tracker_current_releases(
            aggregate_tracker.id, list(reversed(history_releases))
        )

        async with aiosqlite.connect(storage.db_path) as db:
            db.row_factory = aiosqlite.Row
            tracker_history_count = await (
                await db.execute(
                    "SELECT COUNT(*) AS count FROM tracker_release_history WHERE aggregate_tracker_id = ?",
                    (aggregate_tracker.id,),
                )
            ).fetchone()
            projection_rows = await (
                await db.execute(
                    "SELECT identity_key, tracker_release_history_id, digest FROM tracker_current_releases WHERE aggregate_tracker_id = ? ORDER BY identity_key ASC",
                    (aggregate_tracker.id,),
                )
            ).fetchall()
            projection_rows = list(projection_rows)

        assert status is not None
        assert status.last_version == "1.2.3"
        assert tracker_history_count is not None
        assert tracker_history_count["count"] == 2
        assert len(projection_rows) == 2
        assert len({row["tracker_release_history_id"] for row in projection_rows}) == 2
        assert {row["digest"] for row in projection_rows} == {
            "sha256:1111111111111111111111111111111111111111111111111111111111111111",
            "sha256:2222222222222222222222222222222222222222222222222222222222222222",
        }
    finally:
        await _close_storage(storage)


@pytest.mark.asyncio
async def test_auto_check_aggregate_tracker_keeps_successful_source_data_during_partial_failure(
    storage, monkeypatch
):
    tracker_name = "aggregate-auto"
    channels = [Channel(name="stable", type="release")]
    await storage.save_tracker_config(
        TrackerConfig(
            name=tracker_name,
            type="github",
            repo="owner/legacy",
            enabled=True,
            channels=channels,
            fetch_limit=10,
            version_sort_mode="semver",
        )
    )
    await storage.create_aggregate_tracker(
        AggregateTracker(
            name=tracker_name,
            primary_changelog_source_key="repo-primary",
            sources=[
                TrackerSource(
                    source_key="repo-primary",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/app"},
                ),
                TrackerSource(
                    source_key="image-fallback",
                    source_type="container",
                    source_rank=10,
                    source_config={"image": "ghcr.io/owner/app", "registry": "ghcr.io"},
                ),
            ],
        )
    )

    success_release = make_release(
        tracker_name,
        "1.2.3",
        datetime(2024, 7, 1, tzinfo=timezone.utc),
    )

    scheduler = ReleaseScheduler(storage)

    async def _fake_create_tracker(config: TrackerConfig):
        if config.type == "github" and config.repo == "owner/app":
            return FakeTracker(tracker_name, [success_release], channels)
        if config.type == "container" and config.image == "ghcr.io/owner/app":
            return FailingTracker(tracker_name, "docker registry unavailable")
        raise AssertionError(f"unexpected tracker config: {config}")

    monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

    status = await scheduler._check_tracker(tracker_name)
    observations = await storage.get_source_release_observations(tracker_name)
    canonical_releases = await storage.get_canonical_releases(tracker_name)
    stored_status = await storage.get_tracker_status(tracker_name)

    assert status is not None
    assert status.last_version == "1.2.3"
    assert status.error == (
        "部分来源检查失败: image-fallback: Fallback fetch_latest failed: docker registry unavailable"
    )
    assert stored_status is not None
    assert stored_status.last_version == "1.2.3"
    assert stored_status.error == status.error

    assert len(observations) == 1
    assert observations[0].version == "1.2.3"

    assert len(canonical_releases) == 1
    assert canonical_releases[0].version == "1.2.3"
    assert canonical_releases[0].observations


@pytest.mark.asyncio
async def test_manual_check_aggregate_tracker_runs_without_runtime_tracker_config(
    tmp_path, monkeypatch
):
    tracker_name = "aggregate-manual-only"
    db_path = tmp_path / "aggregate-manual-only.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        await storage.create_aggregate_tracker(
            AggregateTracker(
                name=tracker_name,
                primary_changelog_source_key="repo-primary",
                sources=[
                    TrackerSource(
                        source_key="repo-primary",
                        source_type="github",
                        source_rank=0,
                        source_config={"repo": "owner/manual-app"},
                    ),
                    TrackerSource(
                        source_key="repo-secondary",
                        source_type="github",
                        source_rank=10,
                        source_config={"repo": "owner/manual-app-mirror"},
                    ),
                ],
            )
        )

        scheduler = ReleaseScheduler(storage)

        async def _fake_create_tracker(config: TrackerConfig):
            if config.repo == "owner/manual-app":
                return FakeTracker(
                    tracker_name,
                    [
                        make_release(
                            tracker_name,
                            "2.0.0",
                            datetime(2024, 8, 1, tzinfo=timezone.utc),
                        )
                    ],
                    [],
                )
            if config.repo == "owner/manual-app-mirror":
                return FailingTracker(tracker_name, "mirror timeout")
            raise AssertionError(f"unexpected tracker config: {config}")

        monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

        status = await scheduler.check_tracker_now_v2(tracker_name)
        observations = await storage.get_source_release_observations(tracker_name)
        canonical_releases = await storage.get_canonical_releases(tracker_name)

        assert status.last_version == "2.0.0"
        assert status.type == "github"
        assert status.error == (
            "部分来源检查失败: repo-secondary: Fallback fetch_latest failed: mirror timeout"
        )
        assert len(observations) == 1
        assert observations[0].version == "2.0.0"
        assert len(canonical_releases) == 1
        assert canonical_releases[0].version == "2.0.0"
    finally:
        await _close_storage(storage)


@pytest.mark.asyncio
async def test_manual_check_aggregate_tracker_scopes_release_channels_to_owning_source(
    tmp_path, monkeypatch
):
    tracker_name = "aggregate-owner-scoped-channels"
    db_path = tmp_path / "aggregate-owner-scoped-channels.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        aggregate_tracker = await storage.create_aggregate_tracker(
            AggregateTracker(
                name=tracker_name,
                primary_changelog_source_key="repo-primary",
                sources=[
                    TrackerSource(
                        source_key="repo-primary",
                        source_type="github",
                        source_rank=0,
                        source_config={"repo": "owner/manual-app"},
                        release_channels=[
                            ReleaseChannel(
                                release_channel_key="repo-primary-stable",
                                name="stable",
                                type="release",
                            )
                        ],
                    ),
                    TrackerSource(
                        source_key="image-secondary",
                        source_type="container",
                        source_rank=10,
                        source_config={"image": "ghcr.io/owner/manual-app", "registry": "ghcr.io"},
                        release_channels=[
                            ReleaseChannel(
                                release_channel_key="image-secondary-stable",
                                name="stable",
                                type="prerelease",
                            )
                        ],
                    ),
                ],
            )
        )

        scheduler = ReleaseScheduler(storage)
        source_ids_by_key = {source.source_key: source.id for source in aggregate_tracker.sources}

        async def _fake_create_tracker(config: TrackerConfig):
            if config.repo == "owner/manual-app":
                assert [(channel.name, channel.type) for channel in config.channels] == [
                    ("stable", "release")
                ]
                return FakeTracker(
                    tracker_name,
                    [
                        make_release(
                            tracker_name,
                            "3.13.2",
                            datetime(2024, 8, 1, tzinfo=timezone.utc),
                        )
                    ],
                    config.channels,
                )
            if config.image == "ghcr.io/owner/manual-app":
                assert [(channel.name, channel.type) for channel in config.channels] == [
                    ("stable", "prerelease")
                ]
                return FakeTracker(
                    tracker_name,
                    [
                        make_release(
                            tracker_name,
                            "4.0.0-beta.1",
                            datetime(2024, 8, 2, tzinfo=timezone.utc),
                            prerelease=True,
                        )
                    ],
                    config.channels,
                )
            raise AssertionError(f"unexpected tracker config: {config}")

        monkeypatch.setattr(scheduler, "_create_tracker", _fake_create_tracker)

        status = await scheduler.check_tracker_now_v2(tracker_name)
        observations = await storage.get_source_release_observations(tracker_name)
        canonical_releases = await storage.get_canonical_releases(tracker_name)

        assert status.last_version == "3.13.2"
        assert {
            (observation.tracker_source_id, observation.version) for observation in observations
        } == {
            (source_ids_by_key["repo-primary"], "3.13.2"),
            (source_ids_by_key["image-secondary"], "4.0.0-beta.1"),
        }
        assert {release.version for release in canonical_releases} == {"3.13.2", "4.0.0-beta.1"}
    finally:
        await _close_storage(storage)


@pytest.mark.asyncio
async def test_aggregate_only_scheduler_path(tmp_path, monkeypatch):
    tracker_name = "aggregate-only-live-check"
    db_path = tmp_path / "aggregate-only-live-check.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        await storage.save_tracker_config(
            make_config(tracker_name, [Channel(name="stable", type="release")])
        )
        scheduler = ReleaseScheduler(storage)

        aggregate_calls: list[str] = []

        async def fail_legacy_path(*args, **kwargs):
            raise AssertionError("legacy scheduler path must not run for aggregate trackers")

        async def fake_aggregate_path(*args, **kwargs):
            aggregate_calls.append(kwargs.get("trigger_mode", ""))
            return {"releases": [], "latest_version": "1.2.3", "error": None}

        monkeypatch.setattr(scheduler, "_process_tracker_check", fail_legacy_path)
        monkeypatch.setattr(scheduler, "_process_aggregate_tracker_check", fake_aggregate_path)

        auto_status = await scheduler._check_tracker(tracker_name)
        await storage.update_tracker_status(
            TrackerStatus(
                name=tracker_name,
                type="github",
                enabled=True,
                last_check=datetime.now() - timedelta(seconds=31),
                last_version=auto_status.last_version if auto_status else None,
                error=None,
            )
        )
        manual_status = await scheduler.check_tracker_now_v2(tracker_name)

        assert auto_status is not None
        assert auto_status.error is None
        assert manual_status.error is None
        assert aggregate_calls == ["scheduled", "manual"]
    finally:
        await _close_storage(storage)


@pytest.mark.asyncio
async def test_fail_fast_legacy_only_tracker_state(tmp_path, monkeypatch):
    tracker_name = "legacy-only-live-check"
    db_path = tmp_path / "legacy-only-live-check.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        await storage.save_tracker_config(
            make_config(tracker_name, [Channel(name="stable", type="release")])
        )
        scheduler = ReleaseScheduler(storage)

        async def fake_missing_aggregate(name: str):
            if name == tracker_name:
                return None
            return await SQLiteStorage.get_aggregate_tracker(storage, name)

        async def fail_legacy_path(*args, **kwargs):
            raise AssertionError("legacy scheduler path must not run")

        monkeypatch.setattr(storage, "get_aggregate_tracker", fake_missing_aggregate)
        monkeypatch.setattr(scheduler, "_process_tracker_check", fail_legacy_path)

        auto_status = await scheduler._check_tracker(tracker_name)
        await storage.update_tracker_status(
            TrackerStatus(
                name=tracker_name,
                type="github",
                enabled=True,
                last_check=datetime.now() - timedelta(seconds=31),
                last_version=auto_status.last_version if auto_status else None,
                error=auto_status.error if auto_status else None,
            )
        )
        manual_status = await scheduler.check_tracker_now_v2(tracker_name)

        assert auto_status is not None
        assert "Legacy-only tracker state is not supported for live checks" in (
            auto_status.error or ""
        )
        assert "Legacy-only tracker state is not supported for live checks" in (
            manual_status.error or ""
        )
    finally:
        await _close_storage(storage)


@pytest.mark.asyncio
async def test_manual_check_skips_when_same_tracker_check_is_already_running(tmp_path, monkeypatch):
    tracker_name = "manual-check-inflight"
    db_path = tmp_path / "manual-check-inflight.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        await storage.save_tracker_config(
            make_config(tracker_name, [Channel(name="stable", type="release")])
        )

        scheduler = ReleaseScheduler(storage)
        gate = asyncio.Event()

        async def fake_process_aggregate_tracker_check(*args, **kwargs):
            await gate.wait()
            return {"releases": [], "latest_version": None, "error": None}

        monkeypatch.setattr(
            scheduler,
            "_process_aggregate_tracker_check",
            fake_process_aggregate_tracker_check,
        )

        first_task = asyncio.create_task(scheduler.check_tracker_now_v2(tracker_name))
        await asyncio.sleep(0.01)
        second_status = await scheduler.check_tracker_now_v2(tracker_name)
        gate.set()
        await first_task

        assert second_status.error == "检查进行中，跳过重复请求"
    finally:
        await _close_storage(storage)


@pytest.mark.asyncio
async def test_manual_check_skips_when_tracker_was_checked_recently(tmp_path, monkeypatch):
    tracker_name = "manual-check-cooldown"
    db_path = tmp_path / "manual-check-cooldown.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        await storage.save_tracker_config(
            make_config(tracker_name, [Channel(name="stable", type="release")])
        )
        await storage.update_tracker_status(
            TrackerStatus(
                name=tracker_name,
                type="github",
                enabled=True,
                last_check=datetime.now(),
                last_version="1.2.3",
                error=None,
            )
        )

        scheduler = ReleaseScheduler(storage)

        async def fail_process_tracker_check(*args, **kwargs):
            raise AssertionError("manual check should be skipped during cooldown")

        monkeypatch.setattr(scheduler, "_process_tracker_check", fail_process_tracker_check)

        status = await scheduler.check_tracker_now_v2(tracker_name)

        assert status.error == "最近已检查，跳过重复请求"
        assert status.last_version == "1.2.3"
    finally:
        await _close_storage(storage)


@pytest.mark.asyncio
async def test_scheduler_start_does_not_trigger_initial_check_all(tmp_path, monkeypatch):
    db_path = tmp_path / "scheduler-start.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        scheduler = ReleaseScheduler(storage)
        called = []

        async def fake_check_all():
            called.append(True)

        monkeypatch.setattr(scheduler, "check_all", fake_check_all)
        monkeypatch.setattr(scheduler.scheduler_host.scheduler, "start", lambda: None)

        await scheduler.start()
        await asyncio.sleep(0.01)

        assert called == []
    finally:
        await _close_storage(storage)


@pytest.mark.asyncio
async def test_scheduler_check_all_limits_tracker_concurrency(tmp_path):
    db_path = tmp_path / "scheduler-check-all.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        scheduler = ReleaseScheduler(storage)
        scheduler.trackers = cast(
            dict[str, BaseTracker], {f"tracker-{index}": object() for index in range(6)}
        )

        active = 0
        max_seen = 0

        async def fake_check_tracker(name: str):
            nonlocal active, max_seen
            active += 1
            max_seen = max(max_seen, active)
            await asyncio.sleep(0.01)
            active -= 1
            return name

        scheduler._check_tracker = fake_check_tracker  # type: ignore[method-assign]

        await scheduler.check_all()

        assert max_seen <= 3
    finally:
        await _close_storage(storage)


@pytest.mark.asyncio
async def test_fetch_tracker_releases_limits_same_provider_concurrency(tmp_path):
    db_path = tmp_path / "provider-limit.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        scheduler = ReleaseScheduler(storage)

        gate = asyncio.Event()
        counters = {"active": 0, "max_seen": 0}
        config = TrackerConfig(name="gh", type="github", repo="owner/repo")

        tasks = [
            scheduler._fetch_tracker_releases(
                f"gh-{index}",
                BlockingTracker(f"gh-{index}", "github", gate, counters),
                config,
                log_prefix="",
            )
            for index in range(4)
        ]

        batch = asyncio.gather(*tasks)
        await asyncio.sleep(0.02)
        assert counters["max_seen"] <= 2
        gate.set()
        await batch
    finally:
        await _close_storage(storage)


@pytest.mark.asyncio
async def test_fetch_tracker_releases_uses_separate_provider_buckets(tmp_path):
    db_path = tmp_path / "provider-buckets.db"
    storage = await _create_test_storage(db_path)
    await initialize_storage_with_schema(storage)
    try:
        scheduler = ReleaseScheduler(storage)

        gate = asyncio.Event()
        github_counters = {"active": 0, "max_seen": 0}
        gitlab_counters = {"active": 0, "max_seen": 0}

        github_tasks = [
            scheduler._fetch_tracker_releases(
                f"gh-{index}",
                BlockingTracker(f"gh-{index}", "github", gate, github_counters),
                TrackerConfig(name=f"gh-{index}", type="github", repo="owner/repo"),
                log_prefix="",
            )
            for index in range(2)
        ]
        gitlab_tasks = [
            scheduler._fetch_tracker_releases(
                f"gl-{index}",
                BlockingTracker(f"gl-{index}", "gitlab", gate, gitlab_counters),
                TrackerConfig(name=f"gl-{index}", type="gitlab", project="group/project"),
                log_prefix="",
            )
            for index in range(2)
        ]

        batch = asyncio.gather(*(github_tasks + gitlab_tasks))
        await asyncio.sleep(0.02)
        assert github_counters["max_seen"] == 2
        assert gitlab_counters["max_seen"] == 2
        gate.set()
        await batch
    finally:
        await _close_storage(storage)
