"""Normal deployment reviews use live config and frozen artifacts; no runtime endpoints."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.config import ExecutorConfig, ExecutorServiceBinding
from releasetracker.executor_scheduler import ExecutorScheduler
from releasetracker.services.deploy_tasks import DeployTasks
from releasetracker.services.deployment_diff import (
    EXPECTED_UPDATE_STATE,
    INSPECTED_UPDATE_STATE,
    UPDATE_REFUSAL,
    frozen_targets,
    native_review,
    verify_update_state,
)
from releasetracker.storage.sqlite_deployment_admission import AdmissionConflict
from helpers.executor_runtime import (
    create_runtime_connection,
    save_docker_tracker_config,
)
from test_recovery_configuration_diff import container


async def setup(storage, reference_mode="digest"):
    await save_docker_tracker_config(storage, name="diff-update", image="library/nginx")
    from datetime import datetime, timezone
    from releasetracker.models import Release

    tracker = await storage.get_aggregate_tracker("diff-update")
    await storage.save_source_observations(
        tracker.id,
        tracker.sources[0],
        [
            Release(
                tracker_name=tracker.name,
                tracker_type="container",
                name="stable",
                tag_name="stable",
                version="stable",
                published_at=datetime.now(timezone.utc),
                url="https://fixture.invalid",
                commit_sha="sha256:" + "a" * 64,
            )
        ],
    )
    conn_id = await create_runtime_connection(storage)
    executor_id = await storage.create_executor_config(
        ExecutorConfig(
            name="isolated-diff",
            runtime_type="docker",
            runtime_connection_id=conn_id,
            tracker_name=tracker.name,
            tracker_source_id=tracker.sources[0].id,
            channel_name="stable",
            image_reference_mode=reference_mode,
            target_ref={"mode": "container", "container_name": "isolated-nginx"},
            health_check={"readiness_enabled": False},
        )
    )
    executor = await storage.get_executor_config(executor_id)
    scheduler = ExecutorScheduler(storage)
    handler = DeployTasks(storage, scheduler)
    current = container()
    current["image"] = "docker.io/library/nginx:stable"
    current["create_config"]["image"] = current["image"]
    adapter = SimpleNamespace(
        validate_target_ref=AsyncMock(),
        get_managed_markers=AsyncMock(return_value=({},)),
        get_current_image=AsyncMock(side_effect=lambda ref: current["image"]),
        get_current_image_digest=AsyncMock(return_value="sha256:" + "b" * 64),
        supports_single_image_operations=lambda ref: True,
        capture_snapshot=AsyncMock(side_effect=lambda *args: deepcopy(current)),
        is_target_missing_error=lambda exc: False,
    )
    scheduler._adapters[executor_id] = adapter
    receipt = await handler.enqueue(executor_id, manual=True)
    task = await storage.tasks.claim("deploy")
    assert task["id"] == receipt["task_id"]
    return executor, scheduler, handler, adapter, current, task


@pytest.mark.parametrize("change", ["environment", "ports", "mounts", "identity", "artifact"])
async def test_live_config_change_invalidates_approval(storage, change):
    executor, scheduler, handler, adapter, current, task = await setup(storage)
    try:
        evidence = await handler._collect_admission_evidence(executor, task)
        plan = await handler.admission.stage(task, evidence)
        assert plan["summary"]["configuration_diff"]["lines"]
        assert "PASSWORD" not in str(plan["summary"])
        assert "old" not in str(plan["summary"])
        await handler.admission.approve(task["id"], plan["id"], plan["fingerprint"], "admin")
        task = await storage.tasks.claim("deploy")
        await storage.tasks.start_attempt(task)
        if change == "environment":
            current["create_config"]["environment"][0] = "PASSWORD=new-private-value"
        elif change == "ports":
            current["create_config"]["ports"]["80/tcp"] = 18081
        elif change == "mounts":
            current["create_config"]["volumes"] = {"/new/path": {"bind": "/app", "mode": "ro"}}
        elif change == "identity":
            current["container_id"] = "replacement"
        else:
            current["recovery_evidence"]["image_id"] = "sha256:" + "3" * 64
        latest = await handler._collect_admission_evidence(executor, task)
        assert latest.evidence_hash != evidence.evidence_hash
        with pytest.raises(AdmissionConflict, match="deployment_plan_changed"):
            await handler.admission.verify_before_write(task, latest)
    finally:
        await scheduler.shutdown()


async def test_only_image_changes_in_update_diff_and_target_is_frozen(storage):
    executor, scheduler, handler, adapter, current, task = await setup(storage)
    try:
        evidence = await handler._collect_admission_evidence(executor, task)
        lines = evidence.configuration_diff["lines"]
        assert len(lines) == 2 and all(v["path"] == "/create_config/image" for v in lines)
        assert lines[0]["value"] == '"docker.io/library/nginx:stable"'
        assert "@sha256:" in lines[1]["value"]
        assert [v["operation"] for v in lines] == ["-", "+"]
        current["State"] = {"RestartCount": 99, "StartedAt": "changed"}
        assert (
            await handler._collect_admission_evidence(executor, task)
        ).evidence_hash == evidence.evidence_hash
    finally:
        await scheduler.shutdown()


async def test_tag_reference_mode_uses_pure_tag_when_version_changes(storage):
    executor, scheduler, handler, adapter, current, task = await setup(storage, "tag")
    current["image"] = "docker.io/library/nginx:1.26.0"
    current["create_config"]["image"] = current["image"]
    try:
        evidence = await handler._collect_admission_evidence(executor, task)
        lines = evidence.configuration_diff["lines"]
        assert len(lines) == 2
        assert lines[0] == {
            "operation": "-",
            "path": "/create_config/image",
            "value": '"docker.io/library/nginx:1.26.0"',
            "redacted": False,
        }
        assert lines[1] == {
            "operation": "+",
            "path": "/create_config/image",
            "value": '"docker.io/library/nginx:stable"',
            "redacted": False,
        }
        assert "@sha256:" not in lines[1]["value"]
    finally:
        await scheduler.shutdown()


async def test_after_pull_guard_detects_drift_and_records_safe_refusal(storage):
    executor, scheduler, handler, adapter, current, task = await setup(storage)
    try:
        await handler._collect_admission_evidence(executor, task)
        token = EXPECTED_UPDATE_STATE.set(INSPECTED_UPDATE_STATE.get())
        flag = {}
        flag_token = UPDATE_REFUSAL.set(flag)
        try:
            await verify_update_state(adapter, executor.target_ref)
            current["create_config"]["environment"][0] = "PASSWORD=externally-edited"
            with pytest.raises(ValueError, match="deployment_configuration_changed"):
                await verify_update_state(adapter, executor.target_ref)
            assert flag["blocked"]
        finally:
            EXPECTED_UPDATE_STATE.reset(token)
            UPDATE_REFUSAL.reset(flag_token)
    finally:
        await scheduler.shutdown()


async def test_manual_plan_refresh_preserves_or_supersedes_fingerprint(storage):
    executor, scheduler, handler, adapter, current, task = await setup(storage)
    try:
        original = await handler.admission.stage(
            task, await handler._collect_admission_evidence(executor, task)
        )
        task = await storage.tasks.get(task["id"])
        same = await handler.admission.stage(
            task, await handler._collect_admission_evidence(executor, task)
        )
        assert same["fingerprint"] == original["fingerprint"]
        current["create_config"]["ports"]["80/tcp"] = 18888
        fresh = await handler.admission.stage(
            task, await handler._collect_admission_evidence(executor, task)
        )
        assert fresh["id"] != original["id"] and fresh["fingerprint"] != original["fingerprint"]
        with pytest.raises(AdmissionConflict):
            await handler.admission.approve(
                task["id"], original["id"], original["fingerprint"], "admin"
            )
        await handler.admission.approve(task["id"], fresh["id"], fresh["fingerprint"], "admin")
    finally:
        await scheduler.shutdown()


async def test_grouped_diff_only_selected_service(storage):
    executor, scheduler, handler, adapter, current, task = await setup(storage)
    try:
        executor = executor.model_copy(
            update={
                "target_ref": {"mode": "docker_compose", "project": "isolated"},
                "service_bindings": [
                    ExecutorServiceBinding(
                        service="web",
                        tracker_source_id=executor.tracker_source_id,
                        channel_name="stable",
                    )
                ],
            }
        )
        item = deepcopy(current)
        item["compose_service"] = "web"
        other = deepcopy(current)
        other["container_name"] = "other"
        other["compose_service"] = "other"
        adapter.supports_single_image_operations = lambda ref: False
        adapter.capture_snapshot = AsyncMock(
            return_value={
                "mode": "docker_compose",
                "runtime_type": "docker",
                "snapshots": [item, other],
            }
        )
        with frozen_targets(task):
            _, diff = await native_review(storage, scheduler, executor, adapter, task)
        assert len(diff["lines"]) == 2
        assert all("/isolated-nginx/" in line["path"] for line in diff["lines"])
    finally:
        await scheduler.shutdown()


async def test_portainer_proof_uses_real_native_environment_not_only_stack_yaml(
    storage, monkeypatch
):
    from releasetracker.services.deployment_diff import observed_state, protected_state
    from releasetracker.executors import portainer_recovery

    monkeypatch.setattr(
        portainer_recovery,
        "containers",
        AsyncMock(return_value={"web": [{"id": "native-container"}]}),
    )
    actual = {
        "Id": "native-container",
        "Config": {"Env": ["SECRET=first"]},
        "HostConfig": {"PortBindings": {"80/tcp": [{"HostPort": "18080"}]}},
        "State": {"Running": True},
    }
    adapter = SimpleNamespace(
        _request_json=AsyncMock(side_effect=lambda *args, **kwargs: deepcopy(actual))
    )
    snapshot = {"stack_file": "services: {web: {image: nginx:stable}}", "stack_id": 1}
    observed = {"compose": {"services": {"web": {"image": "nginx:stable"}}}}
    first = await observed_state(adapter, {"endpoint_id": 1}, snapshot, observed)
    actual["State"] = {"Running": False}
    assert await observed_state(adapter, {"endpoint_id": 1}, snapshot, observed) == first
    actual["Config"]["Env"] = ["SECRET=changed"]
    changed = await observed_state(adapter, {"endpoint_id": 1}, snapshot, observed)
    assert protected_state(storage, first) != protected_state(storage, changed)
    assert "changed" not in protected_state(storage, changed)


@pytest.mark.parametrize("digest_match", [False, True])
@pytest.mark.parametrize("reference_mode", ["tag", "digest"])
async def test_scheduler_same_tag_respects_reference_mode(storage, digest_match, reference_mode):
    from test_executor_scheduler import FakeAdapter, _mock_scheduler_target

    executor, scheduler, handler, _, current, task = await setup(storage, reference_mode)
    try:
        current_digest = "sha256:" + ("a" if digest_match else "b") * 64
        current_image = current["image"]
        if reference_mode == "digest":
            current_image += "@" + current_digest
        adapter = FakeAdapter(
            await storage.get_runtime_connection(executor.runtime_connection_id),
            current_image=current_image,
            current_digest=current_digest,
        )
        scheduler._adapters[executor.id] = adapter
        _mock_scheduler_target(scheduler, ("stable", "sha256:" + "a" * 64))
        with frozen_targets(task):
            outcome = await scheduler._execute_executor(executor, manual=True)
        should_update = reference_mode == "digest" and not digest_match
        assert outcome.status == ("success" if should_update else "skipped")
        assert bool(adapter.update_calls) is should_update
        expected = (
            "docker.io/library/nginx@sha256:" + "a" * 64
            if reference_mode == "digest"
            else current["image"]
        )
        assert outcome.to_version == expected
    finally:
        await scheduler.shutdown()


async def test_approval_api_reinspects_and_rejects_changed_live_config(storage):
    from releasetracker.routers.tasks import approve_deployment, DeploymentApproval
    from releasetracker.models import User
    from fastapi import HTTPException

    executor, scheduler, handler, adapter, current, task = await setup(storage)
    try:
        plan = await handler.admission.stage(
            task, await handler._collect_admission_evidence(executor, task)
        )
        current["create_config"]["ports"]["80/tcp"] = 18888
        user = User(id=1, username="admin", email="admin@fixture.invalid", is_admin=True)
        with pytest.raises(HTTPException) as exc:
            await approve_deployment(
                task["id"],
                DeploymentApproval(
                    plan_id=plan["id"], fingerprint=plan["fingerprint"], plan_reviewed=True
                ),
                storage,
                user,
                scheduler,
            )
        assert exc.value.status_code == 409 and exc.value.detail == "deployment_plan_changed"
        latest = await handler.admission.latest(task["id"])
        assert latest["id"] != plan["id"] and latest["state"] == "pending"
        result = await approve_deployment(
            task["id"],
            DeploymentApproval(
                plan_id=latest["id"], fingerprint=latest["fingerprint"], plan_reviewed=True
            ),
            storage,
            user,
            scheduler,
        )
        assert result.status_code == 202
    finally:
        await scheduler.shutdown()


async def test_native_guard_refusal_finishes_superseded_without_mutation(storage):
    from releasetracker.services.task_effects import MUTATION_GUARD

    executor, scheduler, handler, adapter, current, task = await setup(storage)
    try:
        evidence = await handler._collect_admission_evidence(executor, task)
        plan = await handler.admission.stage(task, evidence)
        await handler.admission.approve(task["id"], plan["id"], plan["fingerprint"], "admin")
        task = await storage.tasks.claim("deploy")
        await storage.tasks.start_attempt(task)

        async def simulate(config, **kwargs):
            await MUTATION_GUARD.get()()
            current["create_config"]["ports"]["80/tcp"] = 19999
            try:
                await verify_update_state(adapter, config.target_ref)
            except ValueError:
                pass
            await storage.set_executor_run_status(kwargs["run_id"], "failed")
            return SimpleNamespace(status="failed")

        scheduler._run_executor_with_overlap_guard = simulate
        outcome = await handler.execute(task)
        assert outcome.state == "superseded" and outcome.code == "deployment_configuration_changed"
        persisted = await storage.tasks.get(task["id"])
        assert persisted["result"]["mutation_started"] is False
    finally:
        await scheduler.shutdown()
