"""Direction, identity and upstream time are independent contracts."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from releasetracker.config import Channel, TrackerConfig
from releasetracker.models import Release
from releasetracker.storage.sqlite import SQLiteStorage
from releasetracker.services.version_policy import version_policy_reason
from releasetracker.versioning import parsed_version, select_version_candidate


def release(version, *, day=1, digest=None, kind="container", chart=None):
    return Release(
        tracker_name="isolated-nginx",
        tracker_type=kind,
        name=version,
        version=version,
        tag_name=version,
        url="https://example.invalid",
        published_at=datetime(2026, 10, day, tzinfo=timezone.utc),
        artifact_digest=digest,
        commit_sha=digest,
        chart_version=chart,
    )


@pytest.mark.parametrize("mode", ["published_at", "semver"])
def test_late_old_image_never_beats_new_version(mode):
    old, new = release("1.39.16", day=4), release("1.45.0", day=2)
    assert SQLiteStorage.select_best_release([old, new], [], sort_mode=mode) is new
    assert old.published_at is None and new.published_at is None


def test_opaque_tags_require_an_explicit_fixed_tag_channel():
    opaque = release("nightly")
    assert SQLiteStorage.select_best_release([opaque], []) is None
    assert (
        SQLiteStorage.select_best_release(
            [opaque], [Channel(name="canary", type="release", include_pattern="^nightly$")]
        )
        is not None
    )


def test_equivalent_versions_with_conflicting_digests_are_not_time_resolved():
    a, b = release("1.2.3", digest="sha256:a"), release("v1.2.3", day=5, digest="sha256:b")
    assert select_version_candidate([a, b]) is None
    assert SQLiteStorage.select_best_release([a, b], []) is None
    b.artifact_digest = b.commit_sha = a.artifact_digest
    assert select_version_candidate([a, b]) is not None


def test_helm_compares_chart_not_application_version():
    newer_app = release("9.0.0", kind="helm", chart="1.0.0")
    newer_chart = release("8.0.0", kind="helm", chart="2.0.0")
    assert select_version_candidate([newer_app, newer_chart]) is newer_chart


def test_semver_suffixes_and_build_metadata():
    assert parsed_version("1.0.0-alpha.2") < parsed_version("1.0.0-alpha.10")
    assert parsed_version("1.0.0-alpha.10") < parsed_version("1.0.0")
    assert parsed_version("v1.2.3+build.1") == parsed_version("release/1.2.3+build.2")
    assert parsed_version("1.30.0-trixie") > parsed_version("1.29.0-trixie")
    assert parsed_version("1.0.0-alpha.01") is None


def test_legacy_sort_setting_is_input_only():
    config = TrackerConfig(
        name="nginx", type="container", image="library/nginx", version_sort_mode="published_at"
    )
    assert config.version_sort_mode == "semver"
    assert config.model_dump()["version_sort_mode"] == "semver"


@pytest.mark.parametrize("manual", [True, False])
@pytest.mark.parametrize("policy", ["all", "minor", "patch"])
@pytest.mark.parametrize(
    "recovery", ["container_config", "helm_revision", "kubernetes_spec", "compose_config"]
)
def test_downgrade_gate_is_independent_of_policy_and_trigger(manual, policy, recovery):
    task = {
        "payload": {
            "manual": manual,
            "auto_update_policy": policy,
            "targets": [
                {
                    "source_id": 1,
                    "channel": "stable",
                    "target": ["1.39.16", "sha256:new"],
                    "chart_version": "1.39.16",
                }
            ],
            "policy_bindings": [
                {"tracker_source_id": 1, "channel_name": "stable", "service": "nginx"}
            ],
        }
    }
    evidence = SimpleNamespace(
        recovery=recovery,
        configuration={
            "current_image": "registry.invalid/nginx:1.42.6",
            "current_chart_version": "1.42.6",
            "current_services": {"nginx": "registry.invalid/nginx:1.42.6"},
        },
    )
    assert version_policy_reason(task, evidence) == "version_downgrade_requires_approval"


@pytest.mark.asyncio
async def test_missing_time_roundtrip_and_build_enrichment(storage):
    from tests.test_container_published_at_stability import _fresh_container_tracker, _append

    tracker = await _fresh_container_tracker(storage, "isolated-time-nginx")
    digest = "sha256:" + "a" * 64
    item = release("1.2.3", digest=digest)
    item.tracker_name = tracker.name
    await _append(storage, tracker, item)
    rows = await storage.get_source_release_history_releases_by_source(tracker.sources[0].id)
    assert rows[0].published_at is None
    assert rows[0].artifact_created_at is None
    first_observed = rows[0].first_observed_at
    assert first_observed is not None
    item.artifact_created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await _append(storage, tracker, item)
    item.artifact_created_at = None
    await _append(storage, tracker, item)
    rows = await storage.get_source_release_history_releases_by_source(tracker.sources[0].id)
    assert len(rows) == 1
    assert rows[0].published_at is None
    assert rows[0].artifact_created_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert rows[0].first_observed_at == first_observed


@pytest.mark.asyncio
async def test_legacy_selection_task_is_blocked_without_rewriting_target(storage):
    import json
    from tests.test_deployment_admission import _seed_executor, task, evidence
    from releasetracker.storage.sqlite_deployment_admission import (
        DeploymentAdmissionStore,
        AdmissionConflict,
    )

    await _seed_executor(storage, 1)
    queued = await task(storage)
    original = dict(queued["payload"])
    original.pop("selection_revision")
    db = await storage._get_connection()
    await db.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps(original), queued["id"]))
    await db.commit()
    claimed = await storage.tasks.claim("deploy")
    store = DeploymentAdmissionStore(storage)
    plan = await store.stage(claimed, evidence())
    assert plan["state"] == "blocked"
    assert plan["reason"] == "selection_rules_changed"
    with pytest.raises(AdmissionConflict):
        await store.approve(queued["id"], plan["id"], plan["fingerprint"], "admin")
    retained = await storage.tasks.get(queued["id"])
    assert retained["payload"] == original
    assert len(await storage.tasks.list()) == 1


@pytest.mark.parametrize(
    "before,after", [("1!1.2.3", "1.2.3"), ("1.2.3.post2", "1.2.3.post1"), ("1.2.3.2", "1.2.3.1")]
)
def test_compatibility_versions_keep_full_precedence_in_deployment_gate(before, after):
    from tests.test_version_policy import task, evidence

    assert parsed_version(after) < parsed_version(before)
    assert (
        version_policy_reason(task("all", after, manual=True), evidence(before))
        == "version_downgrade_requires_approval"
    )


def test_initial_management_summary_does_not_hide_downgrade():
    from releasetracker.services.deployment_plan import TargetEvidence, public_summary

    evidence = TargetEvidence(
        runtime_identity="test-runtime",
        target_identity="nginx-test",
        configuration={"current_image": "nginx:1.30.0"},
        markers=({},),
        recovery="container_config",
    )
    task = {
        "target_label": "isolated-nginx",
        "payload": {
            "auto_update_policy": "all",
            "manual": True,
            "targets": [{"target": ["1.29.0", "sha256:test"]}],
        },
    }
    assert (
        public_summary(task, evidence)["version_policy_reason"]
        == "version_downgrade_requires_approval"
    )


@pytest.mark.asyncio
async def test_rule_migration_rebuilds_current_alias_without_external_actions(storage, monkeypatch):
    from unittest.mock import AsyncMock
    from tests.helpers.executor_runtime import save_docker_tracker_config
    from tests.test_container_published_at_stability import _append
    from releasetracker.scheduler import ReleaseScheduler

    await save_docker_tracker_config(storage, name="isolated-rebuild-nginx", image="library/nginx")
    tracker = await storage.get_aggregate_tracker("isolated-rebuild-nginx")
    for version, suffix in [("1.45.0", "a"), ("1.45.0", "b"), ("1.39.16", "c")]:
        item = release(version, digest="sha256:" + suffix * 64)
        item.tracker_name = tracker.name
        await _append(storage, tracker, item)
    history = await storage.get_source_release_history_releases_by_source(tracker.sources[0].id)
    for item in history:
        await storage.upsert_tracker_release_history(
            tracker.id, item, primary_source_release_history_id=item.id, source_type="container"
        )
    aggregate_history = await storage.get_tracker_release_history_releases(tracker.id)
    old = next(r for r in aggregate_history if r.commit_sha == "sha256:" + "a" * 64)
    assert not any(ref.is_current for ref in old.alias_references)
    await storage.refresh_tracker_current_releases(tracker.id, [old], source_type="container")
    await storage.set_setting("internal.release_selection_rebuild_pending", "2")
    scheduler = ReleaseScheduler(storage)
    monkeypatch.setattr(scheduler, "_add_or_update_tracker_job", AsyncMock())
    monkeypatch.setattr(scheduler, "_refresh_notifiers", AsyncMock())
    fetch = AsyncMock(side_effect=AssertionError("migration must not fetch"))
    notify = AsyncMock(side_effect=AssertionError("migration must not notify"))
    monkeypatch.setattr(scheduler, "_fetch_tracker_releases", fetch)
    monkeypatch.setattr(scheduler, "_refresh_tracker_projection_and_notify", notify)
    await scheduler.initialize()
    current = await storage.get_tracker_current_releases(tracker.id)
    assert len(current) == 1
    assert current[0].version == "1.45.0"
    assert current[0].commit_sha == "sha256:" + "b" * 64
    assert (await storage.get_tracker_status(tracker.name)).last_version == "1.45.0"
    assert await storage.get_setting("internal.release_selection_rebuild_pending") == "0"
    assert await storage.tasks.list() == []
    fetch.assert_not_awaited()
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_desired_snapshot_is_not_silently_reselected(monkeypatch):
    from unittest.mock import AsyncMock
    from releasetracker.services import deployment_targets

    storage = SimpleNamespace(
        get_executor_desired_state=AsyncMock(
            return_value=SimpleNamespace(
                desired_state_revision="old", desired_target={"binding_targets": []}
            )
        )
    )
    resolve = AsyncMock(side_effect=AssertionError("must preserve old target"))
    monkeypatch.setattr(
        deployment_targets, "_resolve_tracker_latest_target_details_from_storage", resolve
    )
    with pytest.raises(ValueError, match="selection_rules_changed"):
        await deployment_targets.resolve_deployment_targets(
            storage, SimpleNamespace(id=1), manual=False, desired_revision="old"
        )
    resolve.assert_not_awaited()
