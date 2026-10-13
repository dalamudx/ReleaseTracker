"""Desired deployment work follows coordinates, not selection bookkeeping."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import releasetracker.executor_trigger as trigger
import releasetracker.services.deploy_tasks as deploy_tasks_module
from releasetracker import versioning
from releasetracker.config import ExecutorConfig
from releasetracker.services.deploy_tasks import DeployTasks, _already_current
from releasetracker.versioning import SELECTION_RULES_VERSION

pytestmark = pytest.mark.asyncio

DIGEST = "sha256:" + "a" * 64


async def _executor(storage, **overrides):
    from helpers.executor_runtime import create_runtime_connection

    connection_id = await create_runtime_connection(storage)
    return await storage.create_executor_config(
        ExecutorConfig(
            **{
                "name": "queue-reconcile",
                "runtime_type": "docker",
                "runtime_connection_id": connection_id,
                "tracker_name": "queue-tracker",
                "channel_name": "stable",
                "enabled": True,
                "update_mode": "immediate",
                "target_ref": {"mode": "container", "container_name": "app"},
            }
            | overrides
        )
    )


def _target(alias="1.2.0", selection="selection-a"):
    return SimpleNamespace(
        deploy_alias=alias,
        digest=DIGEST,
        display_version=alias,
        aliases=[alias],
        chart_version=None,
        chart_digest=None,
        selection_revision=selection,
    )


async def _publish(storage, executor_id, monkeypatch, target):
    monkeypatch.setattr(
        trigger,
        "_resolve_tracker_latest_target_details_from_storage",
        AsyncMock(return_value=target),
    )
    config = await storage.get_executor_config(executor_id)
    return await trigger.enqueue_executor_binding_targets(storage, config)


async def _deploy_task(storage, executor_id, **payload):
    return await storage.tasks.enqueue(
        kind="deploy",
        resource_key="deployment-mutations",
        dedupe_key=f"deploy:{executor_id}",
        target_label="queue-reconcile",
        trigger_mode="manual" if payload.get("manual") else "automatic",
        max_retries=0,
        payload={"executor_id": executor_id, "selection_revision": SELECTION_RULES_VERSION}
        | payload,
    )


async def test_rule_bump_with_same_coordinates_refreshes_without_queuing(storage, monkeypatch):
    executor_id = await _executor(storage)
    assert await _publish(storage, executor_id, monkeypatch, _target()) is True
    first = await storage.get_executor_desired_state(executor_id)
    await storage.complete_executor_desired_state(
        executor_id, expected_revision=first.desired_state_revision
    )

    monkeypatch.setattr(versioning, "SELECTION_RULES_VERSION", SELECTION_RULES_VERSION + 1)
    assert await _publish(storage, executor_id, monkeypatch, _target(selection="b")) is False
    refreshed = await storage.get_executor_desired_state(executor_id)
    assert refreshed.desired_state_revision == first.desired_state_revision
    assert refreshed.pending is False
    assert refreshed.desired_target["selection_revision"] == SELECTION_RULES_VERSION + 1
    assert refreshed.desired_target["binding_targets"][0]["selection_revision"] == "b"

    assert await _publish(storage, executor_id, monkeypatch, _target("1.3.0", "c")) is True
    moved = await storage.get_executor_desired_state(executor_id)
    assert moved.pending is True
    assert moved.desired_state_revision != first.desired_state_revision


async def test_old_revision_format_is_rebased_without_new_work(storage, monkeypatch):
    executor_id = await _executor(storage)
    await _publish(storage, executor_id, monkeypatch, _target())
    current = await storage.get_executor_desired_state(executor_id)
    legacy = "queue-tracker:bindings:legacy-format"
    db = await storage._get_connection()
    await db.execute(
        "UPDATE executor_desired_state SET desired_state_revision=?, "
        "last_completed_revision=?, pending=0 WHERE executor_id=?",
        (legacy, legacy, executor_id),
    )
    await db.commit()

    assert await _publish(storage, executor_id, monkeypatch, _target()) is False
    rebased = await storage.get_executor_desired_state(executor_id)
    assert rebased.desired_state_revision == current.desired_state_revision
    assert rebased.last_completed_revision == current.desired_state_revision
    assert rebased.pending is False


async def test_startup_sweep_retires_only_unappliable_deployments(storage, monkeypatch):
    executor_id = await _executor(storage)
    await _publish(storage, executor_id, monkeypatch, _target())
    revision = (await storage.get_executor_desired_state(executor_id)).desired_state_revision
    current_auto = await _deploy_task(storage, executor_id, manual=False, desired_revision=revision)
    current_manual = await _deploy_task(storage, executor_id + 100, manual=True)
    stale_manual = await _deploy_task(
        storage, executor_id + 200, manual=True, selection_revision=SELECTION_RULES_VERSION - 1
    )
    stale_auto = await _deploy_task(
        storage, executor_id + 300, manual=False, desired_revision="other:bindings:old"
    )

    assert await storage.tasks.supersede_obsolete_deploys(SELECTION_RULES_VERSION) == 2
    states = {
        task["id"]: (task["state"], task["error_code"])
        for task in [
            await storage.tasks.get(item["id"])
            for item in (current_auto, current_manual, stale_manual, stale_auto)
        ]
    }
    assert states[current_auto["id"]][0] == "queued"
    assert states[current_manual["id"]][0] == "queued"
    assert states[stale_manual["id"]] == ("superseded", "selection_rules_changed")
    assert states[stale_auto["id"]] == ("superseded", "target_replaced")


async def test_manual_success_fulfils_matching_desired_target(storage, monkeypatch):
    executor_id = await _executor(storage)
    await _publish(storage, executor_id, monkeypatch, _target())
    revision = (await storage.get_executor_desired_state(executor_id)).desired_state_revision
    automatic = await _deploy_task(storage, executor_id, manual=False, desired_revision=revision)
    targets = [{"source_id": None, "channel": "stable", "target": ["1.2.0", DIGEST]}]
    manual = {
        "state": "succeeded",
        "payload": {"executor_id": executor_id, "manual": True, "targets": targets},
    }

    other = {
        **manual,
        "payload": {**manual["payload"], "targets": [{**targets[0], "target": ["1.1.0", DIGEST]}]},
    }
    await DeployTasks(storage, None).finished(other)
    assert (await storage.get_executor_desired_state(executor_id)).pending is True
    assert (await storage.tasks.get(automatic["id"]))["state"] == "queued"

    await DeployTasks(storage, None).finished(manual)
    settled = await storage.get_executor_desired_state(executor_id)
    assert settled.pending is False
    assert settled.last_completed_revision == revision
    retired = await storage.tasks.get(automatic["id"])
    assert (retired["state"], retired["error_code"]) == ("superseded", "target_applied")


@pytest.mark.parametrize(
    "mode,lines,expected",
    [
        ("kubernetes_workload", [], True),
        ("helm_release", [], True),
        ("portainer_stack", [], True),
        ("kubernetes_workload", [{"operation": "+", "path": "/x", "value": "1"}], False),
        # A same-tag rebuild of a single container is not visible in its diff.
        ("container", [], False),
    ],
)
async def test_only_reference_compared_targets_skip_empty_reviews(mode, lines, expected):
    executor = SimpleNamespace(target_ref={"mode": mode})
    from test_deployment_admission import evidence

    def review(*, truncated=False):
        diff = {"scope": "workload_spec", "lines": lines, "truncated": truncated}
        return evidence(configuration_diff=diff)

    assert _already_current(executor, review()) is expected
    assert _already_current(executor, review(truncated=True)) is False


@pytest.mark.parametrize("current,expected", [(True, "no_change"), (False, "deferred")])
async def test_no_op_work_settles_before_waiting_for_the_window(
    storage, monkeypatch, current, expected
):
    window = {
        "timezone": "UTC",
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "start_time": "02:00",
        "end_time": "05:00",
    }
    executor_id = await _executor(
        storage, update_mode="maintenance_window", maintenance_window=window
    )
    await _publish(storage, executor_id, monkeypatch, _target())
    revision = (await storage.get_executor_desired_state(executor_id)).desired_state_revision
    scheduler = SimpleNamespace(
        _refresh_system_timezone=AsyncMock(),
        _within_maintenance_window=lambda _window: False,
        _seconds_until_next_maintenance_window=lambda _window: 3600,
    )
    deploy = DeployTasks(storage, scheduler)
    executor = await storage.get_executor_config(executor_id)
    task = {
        "payload": {
            "executor_id": executor_id,
            "manual": False,
            "desired_revision": revision,
            "config_identity": await deploy.identity(executor),
            "selection_revision": SELECTION_RULES_VERSION,
        }
    }
    monkeypatch.setattr(deploy, "_collect_admission_evidence", AsyncMock(return_value=object()))
    monkeypatch.setattr(deploy_tasks_module, "_already_current", lambda _e, _ev: current)

    outcome = await deploy.prepare(task)
    if expected == "no_change":
        assert (outcome.state, outcome.code) == ("no_change", "already_current")
    else:
        assert outcome.code == "maintenance_window"


async def test_changed_target_stages_admission_once(storage, monkeypatch):
    from test_deployment_admission import evidence

    executor_id = await _executor(storage)
    executor = await storage.get_executor_config(executor_id)
    scheduler = SimpleNamespace(_running_executor_ids=set(), readiness=None)
    deploy = DeployTasks(storage, scheduler)
    task = {
        "id": 999,
        "payload": {
            "executor_id": executor_id,
            "manual": True,
            "config_identity": await deploy.identity(executor),
            "selection_revision": SELECTION_RULES_VERSION,
        },
    }
    monkeypatch.setattr(
        deploy_tasks_module, "mutation_resource_key", AsyncMock(return_value="test")
    )
    monkeypatch.setattr(storage.tasks, "bind_mutation_scope", AsyncMock(return_value=True))
    monkeypatch.setattr(storage, "get_latest_executor_run", AsyncMock(return_value=None))
    observed = evidence()
    monkeypatch.setattr(deploy, "_collect_admission_evidence", AsyncMock(return_value=observed))
    stage = AsyncMock(return_value={"id": 1, "state": "pending", "reason": "unmanaged"})
    monkeypatch.setattr(deploy.admission, "stage", stage)

    result = await deploy.prepare(task)
    assert result.state == "awaiting_approval"
    stage.assert_awaited_once_with(task, observed)
