"""Tests covering container-source `published_at` stability.

The scheduler re-runs `append_source_history_for_run` on every check. Prior
to the digest-diff fix the container branch always rewrote `published_at`
with the tracker's newly-generated timestamp, so downstream "X minutes ago"
UIs looked like the image had just been published on every tick.

These tests exercise the storage layer directly to assert the new semantics:

    * first insert → the row's `published_at` is whatever the tracker passed
      in (normally `datetime.now()` at that point);
    * identical digest on a subsequent run → `published_at` sticks to the
      first-insert value;
    * a new digest (image was rebuilt) → `published_at` moves to the new
      value, because the underlying image content actually changed;
    * `commit_sha` absent on one side of the comparison (either legacy data
      or a fetch where the registry refused to return a digest) → we treat
      it as "no evidence of change" and keep the existing timestamp.
"""

from datetime import datetime, timedelta

import aiosqlite
import pytest

from releasetracker.models import AggregateTracker, Release, TrackerSource


async def _fresh_container_tracker(storage, name: str) -> AggregateTracker:
    return await storage.create_aggregate_tracker(
        AggregateTracker(
            name=name,
            primary_changelog_source_key="docker",
            sources=[
                TrackerSource(
                    source_key="docker",
                    source_type="container",
                    source_config={
                        "image": "library/sample-web",
                        "registry": "registry-1.docker.io",
                    },
                )
            ],
        )
    )


def _make_container_release(
    tracker_name: str,
    tag: str,
    *,
    digest: str | None,
    published_at: datetime,
) -> Release:
    return Release(
        tracker_name=tracker_name,
        tracker_type="container",
        name=tag,
        tag_name=tag,
        version=tag,
        published_at=published_at,
        url=f"https://registry-1.docker.io/library/sample-web:{tag}",
        prerelease=False,
        commit_sha=digest,
    )


async def _query_published_at(storage, source_id: int, tag: str) -> str:
    """Return the most recent published_at for the tag across all
    source_release_history rows (container sources can have multiple rows
    per tag when the digest changes)."""
    async with aiosqlite.connect(storage.db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT published_at FROM source_release_history"
                " WHERE tracker_source_id = ? AND tag_name = ?"
                " ORDER BY datetime(published_at) DESC, id DESC LIMIT 1",
                (source_id, tag),
            )
        ).fetchone()
    assert row is not None, f"no source_release_history row for tag {tag!r}"
    return row["published_at"]


async def _append(storage, aggregate_tracker, release):
    source = aggregate_tracker.sources[0]
    run_id = await storage.create_source_fetch_run(source.id, trigger_mode="manual")
    await storage.append_source_history_for_run(
        run_id,
        source,
        [release],
        aggregate_tracker_id=aggregate_tracker.id,
    )


@pytest.mark.asyncio
async def test_container_published_at_stays_put_when_digest_unchanged(storage):
    aggregate_tracker = await _fresh_container_tracker(storage, "container-stable-digest")
    source_id = aggregate_tracker.sources[0].id

    first_seen = datetime.fromisoformat("2026-05-01T10:00:00+00:00")
    release = _make_container_release(
        aggregate_tracker.name,
        "v1.2.3",
        digest="sha256:" + "a" * 64,
        published_at=first_seen,
    )
    await _append(storage, aggregate_tracker, release)

    recorded_first = await _query_published_at(storage, source_id, "v1.2.3")

    # Re-run with the exact same digest but a much later timestamp, as would
    # happen on every scheduled check of an unchanged tag.
    later = first_seen + timedelta(hours=6)
    await _append(
        storage,
        aggregate_tracker,
        _make_container_release(
            aggregate_tracker.name,
            "v1.2.3",
            digest="sha256:" + "a" * 64,
            published_at=later,
        ),
    )

    recorded_after = await _query_published_at(storage, source_id, "v1.2.3")
    assert recorded_after == recorded_first


@pytest.mark.asyncio
async def test_container_published_at_refreshes_when_digest_changes(storage):
    aggregate_tracker = await _fresh_container_tracker(storage, "container-rebuild")
    source_id = aggregate_tracker.sources[0].id

    first_seen = datetime.fromisoformat("2026-05-01T10:00:00+00:00")
    await _append(
        storage,
        aggregate_tracker,
        _make_container_release(
            aggregate_tracker.name,
            "latest",
            digest="sha256:" + "a" * 64,
            published_at=first_seen,
        ),
    )

    rebuilt_at = first_seen + timedelta(days=3)
    await _append(
        storage,
        aggregate_tracker,
        _make_container_release(
            aggregate_tracker.name,
            "latest",
            digest="sha256:" + "b" * 64,
            published_at=rebuilt_at,
        ),
    )

    recorded = await _query_published_at(storage, source_id, "latest")
    assert recorded == rebuilt_at.isoformat()


@pytest.mark.asyncio
async def test_container_published_at_preserved_when_new_digest_is_missing(storage):
    """If digest resolution fails we must not overwrite the known-good time."""
    aggregate_tracker = await _fresh_container_tracker(storage, "container-digest-gap")
    source_id = aggregate_tracker.sources[0].id

    first_seen = datetime.fromisoformat("2026-05-01T10:00:00+00:00")
    await _append(
        storage,
        aggregate_tracker,
        _make_container_release(
            aggregate_tracker.name,
            "v1.2.3",
            digest="sha256:" + "a" * 64,
            published_at=first_seen,
        ),
    )

    gap_check_at = first_seen + timedelta(hours=5)
    await _append(
        storage,
        aggregate_tracker,
        _make_container_release(
            aggregate_tracker.name,
            "v1.2.3",
            digest=None,  # registry refused to return a digest this time
            published_at=gap_check_at,
        ),
    )

    recorded = await _query_published_at(storage, source_id, "v1.2.3")
    assert recorded == first_seen.isoformat()


@pytest.mark.asyncio
async def test_non_container_source_still_preserves_when_version_and_digest_match(storage):
    """Regression guard: refactoring the conditional didn't change non-container semantics."""
    aggregate_tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="github-stable-release",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_config={"repo": "owner/github-stable-release"},
                )
            ],
        )
    )
    source = aggregate_tracker.sources[0]
    source_id = source.id

    first_seen = datetime.fromisoformat("2026-05-01T10:00:00+00:00")
    release = Release(
        tracker_name=aggregate_tracker.name,
        tracker_type="github",
        name="v1.2.3",
        tag_name="v1.2.3",
        version="v1.2.3",
        url="https://example.com/releases/v1.2.3",
        published_at=first_seen,
        prerelease=False,
        commit_sha="abc123",
    )
    run_id = await storage.create_source_fetch_run(source.id, trigger_mode="manual")
    await storage.append_source_history_for_run(
        run_id, source, [release], aggregate_tracker_id=aggregate_tracker.id
    )

    replay_run_id = await storage.create_source_fetch_run(source.id, trigger_mode="manual")
    replay = release.model_copy(update={"published_at": first_seen + timedelta(days=1)})
    await storage.append_source_history_for_run(
        replay_run_id, source, [replay], aggregate_tracker_id=aggregate_tracker.id
    )

    recorded = await _query_published_at(storage, source_id, "v1.2.3")
    assert recorded == first_seen.isoformat()
