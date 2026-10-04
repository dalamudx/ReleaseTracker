"""The reviewed and executed reference must match the saved tag/digest policy."""

import pytest
from unittest.mock import AsyncMock

from releasetracker.config import RuntimeConnectionConfig, ExecutorServiceBinding
from releasetracker.services.deployment_diff import frozen_targets
from test_deployment_configuration_diff import setup
from test_executor_scheduler import (
    FakeAdapter,
    FakeKubernetesWorkloadAdapter,
    _mock_scheduler_target,
)

pytestmark = pytest.mark.asyncio
DIGEST = "sha256:" + "a" * 64
TAG = "docker.io/library/nginx:stable"


@pytest.mark.parametrize("mode", ["tag", "digest"])
@pytest.mark.parametrize(
    "current", ["docker.io/library/nginx:1.25", TAG, "docker.io/library/nginx@" + DIGEST]
)
async def test_queued_review_builds_policy_reference_for_all_current_forms(storage, mode, current):
    executor, scheduler, handler, adapter, live, task = await setup(storage, mode)
    live["image"] = current
    live["create_config"]["image"] = current
    try:
        source = await storage.get_tracker_source(executor.tracker_source_id)
        with frozen_targets(task):
            desired = scheduler._build_target_image(
                current_image=current,
                target_version="stable",
                target_digest=DIGEST,
                executor_config=executor,
                tracker_source=source,
                tracker_source_type="container",
            )
        expected = TAG if mode == "tag" else "docker.io/library/nginx@" + DIGEST
        assert desired == expected
        evidence = await handler._collect_admission_evidence(executor, task)
        lines = evidence.configuration_diff["lines"]
        if current == expected:
            assert lines == []
        else:
            assert [line["value"] for line in lines] == [f'"{current}"', f'"{expected}"']
    finally:
        await scheduler.shutdown()


@pytest.mark.parametrize("initial_mode", ["tag", "digest"])
async def test_policy_switch_is_not_skipped_for_matching_artifact_and_does_not_oscillate(
    storage, initial_mode
):
    executor, scheduler, handler, _, _, task = await setup(storage, initial_mode)
    desired = TAG if initial_mode == "tag" else "docker.io/library/nginx@" + DIGEST
    adapter = FakeAdapter(
        await storage.get_runtime_connection(executor.runtime_connection_id),
        current_image="docker.io/library/nginx@" + DIGEST if initial_mode == "tag" else TAG,
        current_digest=DIGEST,
    )
    scheduler._adapters[executor.id] = adapter
    _mock_scheduler_target(scheduler, ("stable", DIGEST))
    try:
        with frozen_targets(task):
            first = await scheduler._execute_executor(executor, manual=True)
            second = await scheduler._execute_executor(executor, manual=True)
        assert first.status == "success" and first.to_version == desired
        assert second.status == "skipped" and second.to_version == desired
        assert adapter.current_image == desired
        assert len(adapter.update_calls) == 1
    finally:
        await scheduler.shutdown()


@pytest.mark.parametrize("mode", ["tag", "digest"])
async def test_kubernetes_queued_review_and_approved_patch_use_same_reference(storage, mode):
    executor, scheduler, handler, _, _, original_task = await setup(storage, mode)
    runtime_id = await storage.create_runtime_connection(
        RuntimeConnectionConfig(name="isolated-k8s", type="kubernetes", config={"in_cluster": True})
    )
    executor = executor.model_copy(
        update={
            "runtime_type": "kubernetes",
            "runtime_connection_id": runtime_id,
            "target_ref": {
                "mode": "kubernetes_workload",
                "namespace": "isolated",
                "kind": "Deployment",
                "name": "nginx",
            },
            "service_bindings": [
                ExecutorServiceBinding(
                    service="nginx",
                    tracker_source_id=executor.tracker_source_id,
                    channel_name="stable",
                )
            ],
        }
    )
    await storage.save_executor_config(executor)
    adapter = FakeKubernetesWorkloadAdapter(
        await storage.get_runtime_connection(runtime_id),
        current_images={
            "nginx": "docker.io/library/nginx@" + DIGEST if mode == "tag" else TAG,
            "unbound": "haproxy:3.0",
        },
    )
    adapter.get_managed_markers = AsyncMock(return_value=({},))
    scheduler._adapters[executor.id] = adapter
    expected = TAG if mode == "tag" else "docker.io/library/nginx@" + DIGEST
    try:
        # Enqueue with the Kubernetes binding, not the old single-container payload.
        await storage.tasks.finish(original_task, "superseded")
        receipt = await handler.enqueue(executor.id, manual=True)
        task = await storage.tasks.claim("deploy")
        assert task["id"] == receipt["task_id"]
        evidence = await handler._collect_admission_evidence(executor, task)
        lines = evidence.configuration_diff["lines"]
        assert len(lines) == 2 and lines[1]["value"] == f'"{expected}"'
        plan = await handler.admission.stage(task, evidence)
        assert plan["state"] == "pending"
        await handler.admission.approve(task["id"], plan["id"], plan["fingerprint"], "admin")
        running = await storage.tasks.claim("deploy")
        await storage.tasks.start_attempt(running)
        await handler.admission.verify_before_write(running, evidence)
        with frozen_targets(running):
            result = await scheduler._execute_executor(executor, manual=True)
            repeated = await scheduler._execute_executor(executor, manual=True)
        assert result.status == "success" and repeated.status == "skipped"
        assert adapter.update_calls == [(executor.target_ref, {"nginx": expected})]
        assert adapter.current_images == {"nginx": expected, "unbound": "haproxy:3.0"}
    finally:
        await scheduler.shutdown()
