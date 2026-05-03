import json

import aiosqlite
import pytest

from releasetracker.config import Channel, TrackerConfig
from releasetracker.models import AggregateTracker, TrackerSource


@pytest.mark.asyncio
async def test_save_tracker_runtime_config_keeps_runtime_channels_out_of_canonical_sources(
    storage,
):
    await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-runtime-only",
            primary_changelog_source_key="image",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_rank=0,
                    source_config={"repo": "owner/aggregate-runtime-only"},
                ),
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_rank=1,
                    source_config={
                        "image": "ghcr.io/acme/aggregate-runtime-only",
                        "registry": "ghcr.io",
                    },
                ),
            ],
        )
    )

    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="aggregate-runtime-only",
            type="container",
            enabled=True,
            image="ghcr.io/acme/aggregate-runtime-only",
            registry="ghcr.io",
            interval=120,
            channels=[
                Channel(name="stable", type="release", enabled=True),
                Channel(name="beta", type="prerelease", enabled=True),
            ],
        )
    )

    aggregate_tracker = await storage.get_aggregate_tracker("aggregate-runtime-only")
    runtime_config = await storage.get_tracker_config("aggregate-runtime-only")

    assert aggregate_tracker is not None
    image_source = next(source for source in aggregate_tracker.sources if source.source_key == "image")
    assert image_source.release_channels == []

    assert runtime_config is not None
    assert runtime_config.channels == []


@pytest.mark.asyncio
async def test_save_tracker_runtime_config_persists_runtime_only_fields_in_trackers_row(storage):
    await storage.create_aggregate_tracker(
        AggregateTracker(
            name="aggregate-runtime-row-shape",
            primary_changelog_source_key="image",
            sources=[
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_config={
                        "image": "ghcr.io/acme/aggregate-runtime-row-shape",
                        "registry": "ghcr.io",
                    },
                )
            ],
        )
    )

    await storage.save_tracker_runtime_config(
        TrackerConfig(
            name="aggregate-runtime-row-shape",
            type="container",
            enabled=True,
            image="ghcr.io/acme/aggregate-runtime-row-shape",
            registry="ghcr.io",
            interval=180,
            version_sort_mode="semver",
            fetch_limit=25,
            fetch_timeout=45,
            fallback_tags=True,
            github_fetch_mode="rest_first",
            channels=[Channel(name="stable", type="release", enabled=True)],
        )
    )

    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        tracker_row = await (
            await db.execute("SELECT * FROM trackers WHERE name = ?", ("aggregate-runtime-row-shape",))
        ).fetchone()

    assert tracker_row is not None
    assert tracker_row["type"] == "container"
    assert tracker_row["repo"] is None
    assert tracker_row["project"] is None
    assert tracker_row["instance"] is None
    assert tracker_row["chart"] is None
    assert tracker_row["image"] is None
    assert tracker_row["registry"] is None
    assert tracker_row["credential_name"] is None
    assert tracker_row["interval"] == 180
    assert tracker_row["version_sort_mode"] == "semver"
    assert tracker_row["fetch_limit"] == 25
    assert tracker_row["fetch_timeout"] == 45
    assert tracker_row["fallback_tags"] == 1
    assert tracker_row["github_fetch_mode"] == "rest_first"
    assert json.loads(tracker_row["channels"]) == [
        {
            "name": "stable",
            "type": "release",
            "include_pattern": None,
            "exclude_pattern": None,
            "enabled": True,
        }
    ]
