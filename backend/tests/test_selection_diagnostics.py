import pytest

from releasetracker.config import Channel
from releasetracker.models import AggregateTracker, Release, ReleaseChannel, TrackerSource
from releasetracker.storage import sqlite_selection
from releasetracker.tag_ordering import (
    ordering_basis,
    ordering_diagnostics,
    ordering_family_conflicts,
)


def release(tag):
    return Release(
        tracker_name="diagnostic",
        tracker_type="container",
        name=tag,
        version=tag,
        tag_name=tag,
        url="",
    )


@pytest.mark.parametrize(
    "tag,pattern,reason,fields",
    [
        ("latest", None, "no_ordering_key", {}),
        ("3386.vabc_def-1", None, "no_ordering_key", {}),
        (
            "release-2",
            r"^(?P<version>[^-]+)-(?P<revision>[^-]+)$",
            "invalid_numeric_version",
            {"version": "release", "revision": "2"},
        ),
        (
            "12-r2",
            r"^(?P<version>[^-]+)-(?P<revision>[^-]+)$",
            "invalid_numeric_revision",
            {"version": "12", "revision": "r2"},
        ),
        ("build-123", r"(?P<version>\d+)", "pattern_not_full_match", {}),
        ("latest", "[", "invalid_pattern", {}),
    ],
)
def test_diagnoses_actual_ordering_failure(tag, pattern, reason, fields):
    rows = ordering_diagnostics([release(tag)], Channel(name="stable", include_pattern=pattern))
    assert rows == [{"tag": tag, "reason": reason, "family": None, "fields": fields}]


def test_reports_only_unorderable_tags_and_summarizes_family_conflicts():
    releases = [
        release(tag) for tag in ["42", "1.2.3", "1.2.4", "1.2.3-alpine", "1.2.3-bookworm", "latest"]
    ]
    assert [row["tag"] for row in ordering_diagnostics(releases)] == [
        "1.2.3-alpine",
        "1.2.3-bookworm",
    ]
    groups = ordering_family_conflicts(releases)
    assert {group["family"]: group["count"] for group in groups} == {
        "version": 2,
        "variant:alpine": 1,
        "variant:bookworm": 1,
    }
    valid = releases[:-1]
    assert [row["tag"] for row in ordering_diagnostics(valid)] == ["1.2.3-alpine", "1.2.3-bookworm"]
    assert ordering_basis(releases) == "version"
    assert ordering_family_conflicts(valid) == groups


@pytest.mark.parametrize(
    "tags,pattern",
    [
        (["latest"], "^latest$"),
        (["1.2.3", "latest"], None),
        (
            ["3386.vabc_def-1", "3386.vabc_def-10"],
            r"^(?P<version>\d+)\.v[a-z_]+-(?P<revision>\d+)$",
        ),
    ],
)
def test_valid_selection_does_not_report_ignored_tags(tags, pattern):
    assert (
        ordering_diagnostics(
            [release(tag) for tag in tags], Channel(name="stable", include_pattern=pattern)
        )
        == []
    )


@pytest.mark.asyncio
async def test_current_inventory_diagnostics_are_scoped_paginated_searchable_and_read_only(
    storage, authed_client
):
    await storage.create_aggregate_tracker(
        AggregateTracker(
            name="diagnostic",
            sources=[
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_config={"image": "example/app", "registry": "docker.io"},
                    release_channels=[
                        ReleaseChannel(
                            release_channel_key="stable",
                            name="stable",
                            include_pattern="^build-",
                            exclude_pattern="ignore",
                        )
                    ],
                )
            ],
        )
    )
    source = (await storage.get_aggregate_tracker("diagnostic")).sources[0]
    tags = [f"build-{i:03}abc" for i in range(35)] + ["build-ignoreabc", "9.0.0"]
    await sqlite_selection.save_candidates(
        storage, source, [release(tag) for tag in tags], listed_tags=tags
    )
    await sqlite_selection.refresh_selections(
        storage, await storage.get_aggregate_tracker("diagnostic")
    )
    before = await sqlite_selection.snapshots(storage, source.aggregate_tracker_id)
    params = {"tracker_source_id": source.id, "channel_key": "stable", "limit": 7, "offset": 7}
    response = authed_client.get("/api/trackers/diagnostic/selection-diagnostics", params=params)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["candidate_count"] == data["affected_count"] == data["total"] == 35
    assert [item["tag"] for item in data["items"]] == tags[7:14]
    assert data["include_pattern"] == "^build-" and data["exclude_pattern"] == "ignore"
    assert data["source_label"] == "image"
    result = authed_client.get(
        "/api/trackers/diagnostic/selection-diagnostics",
        params={**params, "offset": 0, "search": "BUILD-00"},
    ).json()
    assert result["total"] == 10
    assert len(result["items"]) == 7
    assert await sqlite_selection.snapshots(storage, source.aggregate_tracker_id) == before
    for extra, status in [
        ({"tracker_source_id": source.id + 100}, 404),
        ({"channel_key": "missing"}, 404),
        ({"limit": 101}, 422),
        ({"offset": -1}, 422),
    ]:
        assert (
            authed_client.get(
                "/api/trackers/diagnostic/selection-diagnostics", params={**params, **extra}
            ).status_code
            == status
        )
    assert (
        authed_client.get("/api/trackers/missing/selection-diagnostics", params=params).status_code
        == 404
    )

    mixed = ["build-1.2.3", "build-1.2.3-alpine", "build-xxx"]
    await sqlite_selection.save_candidates(
        storage, source, [release(tag) for tag in mixed], listed_tags=mixed, commit=True
    )
    url = "/api/trackers/diagnostic/selection-diagnostics"
    initial = authed_client.get(url, params={**params, "offset": 0}).json()
    assert initial["total"] == 0 and initial["ordering_basis"] is None
    assert initial["reason"] == "incomparable_versions"
    assert sorted(group["count"] for group in initial["conflicting_families"]) == [1, 1]
    assert {tag for group in initial["conflicting_families"] for tag in group["examples"]} == {
        "build-1.2.3",
        "build-1.2.3-alpine",
    }
    # Legacy query parameters cannot change the diagnostic basis or choose a family.
    result = authed_client.get(
        url, params={**params, "offset": 0, "reference_family": "natural:build-#.#.#"}
    ).json()
    assert result == initial


def test_unknown_conflict_basis_uses_bounded_family_samples_without_guessing():
    records = (
        [release(f"build-{i}") for i in range(8)]
        + [release(f"snapshot-{i}") for i in range(2)]
        + [release("latest")]
    )
    assert ordering_basis(records) is None
    assert ordering_diagnostics(records) == []
    groups = ordering_family_conflicts(records)
    assert [group["count"] for group in groups] == [8, 2]
    assert groups[0]["examples"] == ["build-0", "build-1", "build-2"]
    assert all(len(group["examples"]) <= 3 for group in groups)


def test_jenkins_basis_comes_from_numeric_fields_even_when_extraction_fails():
    rule = Channel(name="stable", include_pattern=r"^(?P<version>[^-]+)-(?P<revision>[^-]+)$")
    records = [release("3386-r2")]
    assert ordering_basis(records, rule) == "extracted"
    assert ordering_diagnostics(records, rule)[0]["reason"] == "invalid_numeric_revision"


def test_production_feishin_ignored_tags_are_not_diagnostic_errors():
    records = [release(tag) for tag in ["1.17.0", "1.16.0", "latest", "fix-docker-env", "0", "1"]]
    assert ordering_basis(records) == "version"
    assert ordering_diagnostics(records) == []
