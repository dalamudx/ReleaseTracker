"""General tag ordering, channel state, retention and preview share one contract."""

import pytest
from releasetracker.config import Channel
from releasetracker.models import ReleaseChannel
from releasetracker.tag_ordering import tag_order, ordering_issue
from releasetracker.versioning import select_version_candidate
from releasetracker.storage import sqlite_selection as selection
from test_channel_selection_state import setup, release, populate_audit, target

PATTERN = r"^(?<version>\d+)\.v[\w_]+-(?<revision>\d+)$"


@pytest.mark.parametrize(
    "tags,expected",
    [
        (["1", "1.17", "1.17.0", "latest"], "1.17.0"),
        (["7048583", "0.7.19"], "0.7.19"),
        (["41", "42"], "42"),
        (["build-9", "build-100"], "build-100"),
        (["2026-9-30", "2026-10-1"], "2026-10-1"),
        (["1.0.0-rc.10", "1.0.0"], "1.0.0"),
    ],
)
def test_automatic_formats(tags, expected):
    assert select_version_candidate([release(tag) for tag in tags]).tag_name == expected


@pytest.mark.parametrize(
    "tags",
    [
        ["build-2", "snapshot-100"],
        ["1.2.3", "build-100"],
        ["3386.v353e57a_1b_ea_0-1"],
        ["sha-a1b2"],
        ["latest", "stable"],
    ],
)
def test_never_guess_unrelated_or_hash_versions(tags):
    assert select_version_candidate([release(tag) for tag in tags]) is None


def test_explicit_fields_ignore_hash_but_detect_same_version_artifact_conflict():
    channel = Channel(name="stable", include_pattern=PATTERN)
    old = release("3385.vffff_9999-100", 1)
    new = release("3386.v0000_0000-1", 2)
    newer = release("3386.v0000_0000-10", 3)
    assert select_version_candidate([old, new, newer], channel=channel) == newer
    conflict = release("3386.vffff_9999-10", 4)
    assert select_version_candidate([newer, conflict], channel=channel) is None
    assert ordering_issue([newer, conflict], channel) is None
    assert tag_order("3386.v353e57a_1b_ea_0-1") is None


@pytest.mark.asyncio
async def test_extracted_channel_retains_latest_and_resolves_deployment(storage):
    tracker, source = await setup(
        storage,
        channels=[
            ReleaseChannel(
                release_channel_key="stable", name="stable", type="release", include_pattern=PATTERN
            )
        ],
    )
    releases = [release(f"3386.vdead_beef-{i}", i) for i in range(1, 26)]
    await populate_audit(storage, tracker, source, releases)
    before = await target(storage, tracker, source)
    assert before.deploy_alias == "3386.vdead_beef-25"
    await storage.cleanup_release_history(retention_count=20)
    history = await storage.get_tracker_release_history_releases(tracker.id)
    assert {r.tag_name for r in history} == {r.tag_name for r in releases[-20:]}
    assert (await target(storage, tracker, source)).digest == before.digest


@pytest.mark.asyncio
async def test_unorderable_tags_remain_in_history_without_target(storage):
    tracker, source = await setup(storage)
    records = [release("3386.vdead_beef-1", 1), release("3387.vffff_abcd-1", 2)]
    await populate_audit(storage, tracker, source, records)
    await storage.cleanup_release_history(retention_count=20)
    assert len(await storage.get_tracker_release_history_releases(tracker.id)) == 2
    snapshot = (await selection.snapshots(storage, tracker.id))[0]
    assert snapshot["reason"] == "unorderable_version"
    assert snapshot["state"] != "ready"


@pytest.mark.asyncio
async def test_preview_uses_selection_rules_and_exclude_precedence():
    from releasetracker.routers.trackers import VersionPreviewPayload, preview_version_order

    data = VersionPreviewPayload(
        channel={"name": "stable", "include_pattern": PATTERN, "exclude_pattern": "-10$"},
        tags=["3386.vdead_beef-1", "3386.vdead_beef-10"],
    )
    result = await preview_version_order(data, _user=None)
    assert result["selected"] == "3386.vdead_beef-1"
    assert len(result["groups"]) == 1


@pytest.mark.asyncio
async def test_preview_invalid_pattern_fails():
    from fastapi import HTTPException
    from releasetracker.routers.trackers import VersionPreviewPayload, preview_version_order

    with pytest.raises(HTTPException):
        await preview_version_order(
            VersionPreviewPayload(
                channel={"name": "stable", "include_pattern": "["}, tags=["build-1"]
            ),
            _user=None,
        )


def test_image_variants_are_not_ordered_by_variant_names():
    channel = Channel(name="stable")
    assert (
        select_version_candidate(
            [release("1.2.3-alpine"), release("1.2.3-debian")], channel=channel
        )
        is None
    )
    assert (
        select_version_candidate(
            [release("1.2.3-alpine"), release("1.2.4-alpine")], channel=channel
        ).tag_name
        == "1.2.4-alpine"
    )


def test_custom_versions_keep_unknown_deployment_direction_gate():
    from releasetracker.services.version_policy import version_policy_reason
    from test_version_policy import task, evidence

    reason = version_policy_reason(
        task("all", "3386.vdead_beef-10", manual=True), evidence("3385.vdead_beef-1")
    )
    assert reason == "version_unknown_requires_approval"


def test_helm_keeps_semver_suffix_ordering():
    a = release("1.2.3-chart.1").model_copy(
        update={"tracker_type": "helm", "chart_version": "1.2.3-chart.1"}
    )
    b = release("1.2.3-chart.2").model_copy(
        update={"tracker_type": "helm", "chart_version": "1.2.3-chart.2"}
    )
    assert select_version_candidate([a, b]) == b


def test_pattern_validation_rejects_non_string_values():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Channel(name="stable", include_pattern=42)


@pytest.mark.asyncio
async def test_preview_places_specific_alias_first():
    from releasetracker.routers.trackers import VersionPreviewPayload, preview_version_order

    result = await preview_version_order(
        VersionPreviewPayload(channel={"name": "stable"}, tags=["1", "1.17", "1.17.0"]), _user=None
    )
    assert result["selected"] == result["groups"][0]["tag"] == "1.17.0"
