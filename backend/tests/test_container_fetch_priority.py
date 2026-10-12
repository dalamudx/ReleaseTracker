from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.config import Channel
from releasetracker.scheduler import ReleaseScheduler
from releasetracker.storage.sqlite import SQLiteStorage
from releasetracker.trackers.docker import DockerTracker, _select_incremental_probe_window


def source_state(tracker):
    history = [tracker._tag_to_release(tag) for tag in ["1.0.0", "2.0.0", "dev"]]
    for index, release in enumerate(history):
        release.commit_sha = "sha256:" + str(index) * 64
    now = datetime.now(timezone.utc)
    storage = SimpleNamespace(
        get_source_alias_last_observed=AsyncMock(
            return_value={
                release.tag_name: now + timedelta(seconds=index)
                for index, release in enumerate(history)
            }
        ),
        get_source_alias_latest_digests=AsyncMock(
            return_value={release.tag_name: release.commit_sha for release in history}
        ),
        get_source_release_history_releases_by_source=AsyncMock(return_value=history),
        select_best_release=SQLiteStorage.select_best_release,
        select_best_releases_by_channel=SQLiteStorage.select_best_releases_by_channel,
    )
    return storage, history


async def test_current_version_is_refreshed_before_old_history_without_expanding_budget():
    tracker = DockerTracker("test", "team/nginx", registry="registry.test")
    storage, history = source_state(tracker)
    scheduler = ReleaseScheduler(storage)
    await scheduler._configure_container_tracker_fetch_state(4, tracker, history)
    assert tracker._priority_aliases == ("2.0.0",)
    window = _select_incremental_probe_window(
        list(reversed(history[:2])),
        1,
        tracker._alias_last_observed,
        priority_aliases=tracker._priority_aliases,
    )
    assert [release.tag_name for release in window] == ["2.0.0"]
    storage.get_source_release_history_releases_by_source.assert_not_awaited()


async def test_explicit_event_alias_keeps_precedence_over_current_version():
    tracker = DockerTracker("test", "team/nginx", registry="registry.test")
    storage, history = source_state(tracker)
    await ReleaseScheduler(storage)._configure_container_tracker_fetch_state(
        4,
        tracker,
        history,
        priority_aliases=("dev", "dev"),
    )
    assert tracker._priority_aliases == ("dev", "2.0.0")
    window = _select_incremental_probe_window(
        history,
        2,
        tracker._alias_last_observed,
        priority_aliases=tracker._priority_aliases,
    )
    assert [release.tag_name for release in window] == ["dev", "2.0.0"]


async def test_single_source_path_loads_only_that_sources_history_and_respects_filter():
    tracker = DockerTracker(
        "test", "team/nginx", registry="registry.test", filter={"exclude_pattern": r"^2\."}
    )
    storage, _ = source_state(tracker)
    await ReleaseScheduler(storage)._configure_container_tracker_fetch_state(17, tracker)
    storage.get_source_release_history_releases_by_source.assert_awaited_once_with(17)
    assert tracker._priority_aliases == ("1.0.0",)


@pytest.mark.parametrize("as_dict", [False, True])
async def test_pinned_tag_channel_is_refreshed_even_with_newer_numeric_history(as_dict):
    channel = Channel(name="stable", include_pattern=r"^dev$")
    tracker = DockerTracker(
        "test",
        "team/nginx",
        registry="registry.test",
        channels=[channel.model_dump() if as_dict else channel],
    )
    storage, history = source_state(tracker)
    await ReleaseScheduler(storage)._configure_container_tracker_fetch_state(4, tracker, history)
    assert tracker._priority_aliases == ("dev",)


async def test_limit_one_retries_only_current_channels_not_yet_observed():
    tracker = DockerTracker(
        "test",
        "team/nginx",
        registry="registry.test",
        channels=[
            Channel(name="stable", include_pattern=r"^\d"),
            Channel(name="canary", include_pattern=r"^dev$"),
        ],
    )
    storage, history = source_state(tracker)
    scheduler = ReleaseScheduler(storage)
    await scheduler._configure_container_tracker_fetch_state(4, tracker, history)
    assert tracker._priority_aliases == ("dev", "2.0.0")
    storage.get_source_alias_last_observed.return_value["dev"] += timedelta(seconds=1)
    await scheduler._configure_container_tracker_fetch_state(4, tracker, history)
    assert tracker._priority_aliases == ("2.0.0",)
    storage.get_source_alias_last_observed.return_value["2.0.0"] += timedelta(seconds=1)
    await scheduler._configure_container_tracker_fetch_state(4, tracker, history)
    assert tracker._priority_aliases == ()


async def test_current_probe_does_not_starve_rotation_with_limit_one():
    tracker = DockerTracker("test", "team/nginx", registry="registry.test", channels=[])
    storage, history = source_state(tracker)
    scheduler = ReleaseScheduler(storage)
    await scheduler._configure_container_tracker_fetch_state(4, tracker, history)
    assert tracker._priority_aliases == ("2.0.0",)
    # A failed probe/configure-only retry must retain latest priority.
    await scheduler._configure_container_tracker_fetch_state(4, tracker, history)
    assert tracker._priority_aliases == ("2.0.0",)
    storage.get_source_alias_last_observed.return_value["2.0.0"] += timedelta(seconds=1)
    await scheduler._configure_container_tracker_fetch_state(4, tracker, history)
    assert tracker._priority_aliases == ()
    selected = _select_incremental_probe_window(
        list(reversed(history[:2])),
        limit=1,
        alias_last_observed=tracker._alias_last_observed,
        priority_aliases=tracker._priority_aliases,
    )
    assert [release.tag_name for release in selected] == ["1.0.0"]
    history[1].commit_sha = "sha256:" + "9" * 64
    await scheduler._configure_container_tracker_fetch_state(4, tracker, history)
    assert tracker._priority_aliases == ("2.0.0",)
    await scheduler._configure_container_tracker_fetch_state(
        4, tracker, history, priority_aliases=("dev",)
    )
    assert tracker._priority_aliases == ("dev", "2.0.0")
