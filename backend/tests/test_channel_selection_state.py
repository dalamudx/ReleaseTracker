"""Selections use current state, never the retained audit-history window."""

import pytest

from releasetracker.models import AggregateTracker, TrackerSource, ReleaseChannel, Release
from releasetracker.storage import sqlite_selection as selection
from releasetracker.executor_scheduler_target_resolution import (
    _resolve_tracker_latest_target_details_from_storage,
)

pytestmark = pytest.mark.asyncio


async def setup(storage, *, channels=None, config=None):
    tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="isolated-selection",
            sources=[
                TrackerSource(
                    source_key="images",
                    source_type="container",
                    source_config={
                        "image": "acme/app",
                        "registry": "registry.example",
                        **(config or {}),
                    },
                    release_channels=channels
                    or [
                        ReleaseChannel(release_channel_key="stable", name="stable", type="release")
                    ],
                )
            ],
        )
    )
    return tracker, tracker.sources[0]


def release(tag, digest=1):
    return Release(
        tracker_name="isolated-selection",
        tracker_type="container",
        name=tag,
        version=tag,
        tag_name=tag,
        url="https://registry.example/acme/app",
        commit_sha=f"sha256:{digest:064x}" if digest else None,
        artifact_digest=f"sha256:{digest:064x}" if digest else None,
    )


async def target(storage, tracker, source, channel="stable"):
    return await _resolve_tracker_latest_target_details_from_storage(
        storage,
        tracker.name,
        channel,
        tracker_source_id=source.id,
        tracker_source_type=source.source_type,
    )


async def test_concrete_tag_wins_when_alias_payloads_share_display_version(storage):
    tracker, source = await setup(storage)
    releases = [
        release(tag).model_copy(update={"version": "2.5.3"}) for tag in ("latest", "2.5", "2.5.3")
    ]
    await selection.save_candidates(storage, source, releases, commit=True)
    winners = await selection.refresh_selections(storage, tracker)
    assert winners[0].tag_name == "2.5.3"
    assert (await target(storage, tracker, source)).deploy_alias == "2.5.3"


async def test_container_variant_remains_stable_while_dev_requires_prerelease_channel(storage):
    tracker, source = await setup(storage)
    await selection.save_candidates(
        storage, source, [release("1.30.0-trixie"), release("1.31.0-dev", 2)], commit=True
    )
    assert (await selection.refresh_selections(storage, tracker))[0].tag_name == "1.30.0-trixie"


async def test_selection_without_any_audit_history_matches_executor(storage):
    tracker, source = await setup(storage)
    await selection.save_candidates(
        storage,
        source,
        [release("0.7.19"), release("7048583", 2), release("0.7"), release("latest")],
        commit=True,
    )
    winners = await selection.refresh_selections(storage, tracker)
    assert [r.version for r in winners] == ["0.7.19"]
    current = await storage.get_tracker_current_release_rows(tracker.name)
    assert current[0]["version"] == "0.7.19"
    assert current[0]["tracker_release_history_id"] is None
    resolved = await target(storage, tracker, source)
    assert resolved.deploy_alias == "0.7.19"
    assert resolved.digest == release("0.7.19").commit_sha
    assert await storage.get_tracker_release_history_releases(tracker.id) == []
    await storage.cleanup_release_history(retention_count=1)
    assert (await target(storage, tracker, source)).deploy_alias == "0.7.19"


async def test_explicit_integer_version_format(storage):
    tracker, source = await setup(storage, config={"version_format": "pep440"})
    await selection.save_candidates(
        storage, source, [release("42"), release("0.7.19", 2)], commit=True
    )
    assert [r.version for r in await selection.refresh_selections(storage, tracker)] == ["42"]


async def test_unresolved_higher_manifest_blocks_deployment(storage):
    tracker, source = await setup(storage)
    await selection.save_candidates(
        storage, source, [release("1.0.0")], listed_tags=["1.0.0"], commit=True
    )
    await selection.refresh_selections(storage, tracker)
    await selection.save_candidates(
        storage, source, [], listed_tags=["1.0.0", "2.0.0"], commit=True
    )
    current = await selection.refresh_selections(storage, tracker)
    assert [r.version for r in current] == ["1.0.0"]
    assert (await selection.snapshots(storage, tracker.id))[0]["state"] == "pending"
    assert await target(storage, tracker, source) is None
    await selection.save_candidates(storage, source, [release("2.0.0", 2)], commit=True)
    await selection.refresh_selections(storage, tracker)
    assert (await target(storage, tracker, source)).deploy_alias == "2.0.0"


async def test_complete_empty_list_does_not_resurrect_history(storage):
    tracker, source = await setup(storage)
    await storage.save_source_observations(tracker.id, source, [release("1.0.0")])
    await selection.refresh_selections(storage, tracker)
    await selection.save_candidates(storage, source, [], listed_tags=[], commit=True)
    assert await selection.refresh_selections(storage, tracker) == []
    assert await target(storage, tracker, source) is None
    assert await storage.get_tracker_current_release_rows(tracker.name) == []


async def test_same_tag_republish_and_equivalent_version_conflict(storage):
    tracker, source = await setup(storage)
    await selection.save_candidates(storage, source, [release("1.0.0")], commit=True)
    await selection.refresh_selections(storage, tracker)
    before = (await selection.snapshots(storage, tracker.id))[0]["revision"]
    await selection.save_candidates(storage, source, [release("1.0.0", 2)], commit=True)
    await selection.refresh_selections(storage, tracker)
    assert (await target(storage, tracker, source)).digest == release("1.0.0", 2).commit_sha
    assert (await selection.snapshots(storage, tracker.id))[0]["revision"] != before
    await selection.save_candidates(storage, source, [release("v1.0.0", 3)], commit=True)
    await selection.refresh_selections(storage, tracker)
    assert (await selection.snapshots(storage, tracker.id))[0]["state"] == "conflict"
    assert await target(storage, tracker, source) is None


async def test_channel_rules_change_requires_rebuild(storage):
    tracker, source = await setup(storage)
    await selection.save_candidates(storage, source, [release("1.0.0")], commit=True)
    await selection.refresh_selections(storage, tracker)
    source.release_channels[0].include_pattern = "^2\\."
    assert (await selection.bound_selection(storage, source, "stable")) == (None, True)


async def test_fixed_floating_alias_is_explicit(storage):
    tracker, source = await setup(
        storage,
        channels=[
            ReleaseChannel(
                release_channel_key="floating",
                name="stable",
                type="release",
                include_pattern="^latest$",
            )
        ],
    )
    await selection.save_candidates(
        storage, source, [release("latest"), release("9.0.0", 2)], commit=True
    )
    await selection.refresh_selections(storage, tracker)
    assert (await target(storage, tracker, source)).deploy_alias == "latest"


async def test_overlapping_channels_have_independent_decisions(storage):
    tracker, source = await setup(
        storage,
        channels=[
            ReleaseChannel(release_channel_key=k, name=k, type="release", include_pattern=pattern)
            for k, pattern in (("stable", r"^\d+\."), ("beta", r"^1\."))
        ],
    )
    await selection.save_candidates(
        storage, source, [release("1.9.0"), release("2.0.0", 2)], commit=True
    )
    await selection.refresh_selections(storage, tracker)
    assert (await target(storage, tracker, source, "stable")).deploy_alias == "2.0.0"
    assert (await target(storage, tracker, source, "beta")).deploy_alias == "1.9.0"


async def populate_audit(storage, tracker, source, releases):
    run_id = await storage.create_source_fetch_run(source.id, trigger_mode="manual")
    ids = await storage.append_source_history_for_run(
        run_id, source, releases, aggregate_tracker_id=tracker.id
    )
    for item in releases:
        key = storage.release_identity_key_for_source(item, source_type=source.source_type)
        if key in ids:
            await storage.upsert_tracker_release_history(
                tracker.id,
                item,
                primary_source_release_history_id=ids[key],
                source_type=source.source_type,
            )
    await storage.finalize_source_fetch_run(
        run_id, status="success", fetched_count=len(releases), filtered_in_count=len(releases)
    )
    await selection.save_candidates(
        storage, source, releases, listed_tags=[item.tag_name for item in releases]
    )
    await selection.refresh_selections(storage, tracker)


async def test_each_channel_retains_twenty_independent_records(storage):
    tracker, source = await setup(
        storage,
        channels=[
            ReleaseChannel(
                release_channel_key=name, name=name, type="release", include_pattern=pattern
            )
            for name, pattern in (("stable", r"^1\."), ("beta", r"^2\."))
        ],
    )
    releases = [
        release(f"{major}.0.{patch}", major * 100 + patch)
        for major in (1, 2)
        for patch in range(25)
    ]
    await populate_audit(storage, tracker, source, releases)
    before = {
        channel: (await target(storage, tracker, source, channel)).deploy_alias
        for channel in ("stable", "beta")
    }
    result = await storage.cleanup_release_history(retention_count=20, reclaim=False)
    assert [item["retained_count"] for item in result["channels"]] == [20, 20]
    history = await storage.get_tracker_release_history_releases(tracker.id)
    assert len(history) == 40
    assert sum(item.version.startswith("1.") for item in history) == 20
    assert sum(item.version.startswith("2.") for item in history) == 20
    assert {
        channel: (await target(storage, tracker, source, channel)).deploy_alias
        for channel in before
    } == before
    db = await storage._get_connection()
    violations = [
        tuple(row) for row in await (await db.execute("PRAGMA foreign_key_check")).fetchall()
    ]
    assert not violations, violations


async def test_overlapping_channels_keep_union_without_shared_quota(storage):
    tracker, source = await setup(
        storage,
        channels=[
            ReleaseChannel(
                release_channel_key=name, name=name, type="release", include_pattern=pattern
            )
            for name, pattern in (("stable", r"^\d+\."), ("beta", r"^1\."))
        ],
    )
    await populate_audit(
        storage,
        tracker,
        source,
        [release(f"1.0.{patch}", patch + 1) for patch in range(25)] + [release("2.0.0", 100)],
    )
    result = await storage.cleanup_release_history(retention_count=20, reclaim=False)
    assert [item["retained_count"] for item in result["channels"]] == [20, 20]
    assert len(await storage.get_tracker_release_history_releases(tracker.id)) == 21
    await storage.cleanup_release_history(retention_count=2, reclaim=False)
    assert (await target(storage, tracker, source, "stable")).deploy_alias == "2.0.0"
    assert (await target(storage, tracker, source, "beta")).deploy_alias == "1.0.24"


async def test_new_inventory_blocks_old_selection_until_refreshed(storage):
    tracker, source = await setup(storage)
    await selection.save_candidates(storage, source, [release("1.0.0")], commit=True)
    await selection.refresh_selections(storage, tracker)
    old_revision = (await selection.snapshots(storage, tracker.id))[0]["revision"]
    await selection.save_candidates(storage, source, [release("2.0.0", 2)], commit=True)
    assert await target(storage, tracker, source) is None
    assert (await selection.snapshots(storage, tracker.id))[0]["state"] == "pending"
    await selection.refresh_selections(storage, tracker)
    assert (await target(storage, tracker, source)).deploy_alias == "2.0.0"
    assert (await selection.snapshots(storage, tracker.id))[0]["revision"] != old_revision


async def test_repeated_confirmation_does_not_change_target_revision(storage):
    tracker, source = await setup(storage)
    await selection.save_candidates(storage, source, [release("1.0.0")], commit=True)
    await selection.refresh_selections(storage, tracker)
    revision = (await selection.snapshots(storage, tracker.id))[0]["revision"]
    await selection.save_candidates(
        storage, source, [release("1.0.0").model_copy(update={"body": "new notes"})], commit=True
    )
    await selection.refresh_selections(storage, tracker)
    assert (await selection.snapshots(storage, tracker.id))[0]["revision"] == revision


async def test_pending_task_artifact_is_extra_protection_without_changing_queue(storage):
    import json

    tracker, source = await setup(storage)
    releases = [release(f"1.0.{patch}", patch + 1) for patch in range(5)]
    await populate_audit(storage, tracker, source, releases)
    db = await storage._get_connection()
    payload = json.dumps(
        {"targets": [{"tracker_name": tracker.name, "target": ["1.0.0", releases[0].commit_sha]}]}
    )
    await db.execute(
        "INSERT INTO tasks(kind,resource_key,dedupe_key,target_label,payload,max_retries,due_at,created_at,updated_at,state) VALUES('deploy','isolated-task','task','restore',?,0,0,0,0,'queued')",
        (payload,),
    )
    await db.commit()
    result = await storage.cleanup_release_history(retention_count=2, reclaim=False)
    assert result["protected_history_count"] == 1
    assert {
        item.version for item in await storage.get_tracker_release_history_releases(tracker.id)
    } == {"1.0.0", "1.0.3", "1.0.4"}
    assert tuple(
        await (
            await db.execute("SELECT payload,state FROM tasks WHERE resource_key='isolated-task'")
        ).fetchone()
    ) == (payload, "queued")


async def test_failed_manifest_reconfirmation_keeps_display_but_blocks_deployment(storage):
    tracker, source = await setup(storage)
    await selection.save_candidates(storage, source, [release("1.0.0")], commit=True)
    await selection.refresh_selections(storage, tracker)
    await selection.save_candidates(
        storage,
        source,
        [
            release("1.0.0").model_copy(
                update={"commit_sha": None, "artifact_digest": None, "candidate_confirmed": False}
            )
        ],
        commit=True,
    )
    await selection.refresh_selections(storage, tracker)
    assert await target(storage, tracker, source) is None
    assert (await selection.snapshots(storage, tracker.id))[0]["state"] == "pending"
    assert (await selection.current_rows(storage, tracker.id))[0]["version"] == "1.0.0"
    assert (await selection.snapshots(storage, tracker.id))[0][
        "reason"
    ] == selection.MANIFEST_UNCONFIRMED


async def test_backfill_with_latest_tag_pointer_is_ready_without_recheck(storage):
    tracker, source = await setup(storage)
    run_id = await storage.create_source_fetch_run(source.id, trigger_mode="manual")
    await storage.append_source_history_for_run(
        run_id, source, [release("1.0.0")], aggregate_tracker_id=tracker.id
    )
    await storage.finalize_source_fetch_run(
        run_id, status="success", fetched_count=1, filtered_in_count=1
    )
    await selection.refresh_selections(storage, tracker)
    snapshot = (await selection.snapshots(storage, tracker.id))[0]
    assert (snapshot["state"], snapshot["reason"]) == ("ready", None)
    assert (await target(storage, tracker, source)).deploy_alias == "1.0.0"


async def test_backfill_without_tag_pointer_awaits_registry_check(storage):
    tracker, source = await setup(storage)
    run_id = await storage.create_source_fetch_run(source.id, trigger_mode="manual")
    await storage.append_source_history_for_run(
        run_id, source, [release("1.0.0")], aggregate_tracker_id=tracker.id
    )
    await storage.finalize_source_fetch_run(
        run_id, status="success", fetched_count=1, filtered_in_count=1
    )
    db = await storage._get_connection()
    await db.execute("DELETE FROM source_release_aliases WHERE tracker_source_id=?", (source.id,))
    await db.commit()
    await selection.refresh_selections(storage, tracker)
    snapshot = (await selection.snapshots(storage, tracker.id))[0]
    assert (snapshot["state"], snapshot["reason"]) == ("pending", selection.AWAITING_CHECK)
    assert await target(storage, tracker, source) is None
    # A real registry check confirms the tag pointer and clears the notice without approval.
    await selection.save_candidates(
        storage, source, [release("1.0.0")], listed_tags=["1.0.0"], commit=True
    )
    await selection.refresh_selections(storage, tracker)
    assert (await selection.snapshots(storage, tracker.id))[0]["state"] == "ready"
    assert (await target(storage, tracker, source)).deploy_alias == "1.0.0"


async def test_legacy_reason_text_is_reported_as_reason_code(storage):
    tracker, source = await setup(storage)
    await selection.save_candidates(
        storage,
        source,
        [
            release("1.0.0").model_copy(
                update={"commit_sha": None, "artifact_digest": None, "candidate_confirmed": False}
            )
        ],
        commit=True,
    )
    await selection.refresh_selections(storage, tracker)
    db = await storage._get_connection()
    await db.execute(
        "UPDATE tracker_channel_selections SET reason='Highest candidate manifest is not confirmed'"
    )
    await db.commit()
    assert (await selection.snapshots(storage, tracker.id))[0][
        "reason"
    ] == selection.MANIFEST_UNCONFIRMED
    await db.execute("UPDATE source_inventory_scans SET origin='bootstrap'")
    await db.commit()
    assert (await selection.snapshots(storage, tracker.id))[0]["reason"] == selection.AWAITING_CHECK
