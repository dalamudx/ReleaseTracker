"""Push times persist per artifact, never migrate across digest changes."""
import json
from datetime import datetime, timezone

import aiosqlite

import pytest
from releasetracker.models import AggregateTracker, TrackerSource
from releasetracker.trackers.docker import DockerTracker


@pytest.mark.parametrize("field", ["registry_pushed_at", "registry_created_at", "registry_modified_at"])
async def test_push_time_survives_unavailable_metadata_but_not_digest_change(storage, field):
    tracker = await storage.create_aggregate_tracker(AggregateTracker(
        name="push-time-test", sources=[TrackerSource(source_key="image", source_type="container",
            source_config={"image": "library/nginx", "registry": "registry-1.docker.io"})]))
    source = tracker.sources[0]
    adapter = DockerTracker(tracker.name, "library/nginx")
    pushed = datetime(2026, 10, 11, 12, 34, 56, tzinfo=timezone.utc)
    old_digest = "sha256:" + "a" * 64
    async def save(digest, time):
        release = adapter._tag_to_release("1.2.0")
        release.commit_sha = digest
        setattr(release, field, time)
        await storage.save_source_observations(tracker.id, source, [release])
        async with aiosqlite.connect(storage.db_path) as db:
            row = await (await db.execute(
                "SELECT raw_payload FROM source_release_observations WHERE tracker_source_id = ?",
                (source.id,))).fetchone()
            return json.loads(row[0])
    assert (await save(old_digest, pushed))[field] == pushed.isoformat()
    assert (await save(old_digest, None))[field] == pushed.isoformat()
    assert not (await save("sha256:" + "b" * 64, None)).get(field)
    async with aiosqlite.connect(storage.db_path) as db:
        row = await (await db.execute(
            "SELECT raw_payload FROM source_release_history WHERE tracker_source_id = ? AND commit_sha = ?",
            (source.id, old_digest))).fetchone()
        assert json.loads(row[0])[field] == pushed.isoformat()
