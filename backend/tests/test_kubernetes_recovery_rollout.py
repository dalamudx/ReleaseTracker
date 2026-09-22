"""Explicit recovery succeeds only after the submitted controller rollout converges."""

from copy import deepcopy

import pytest
from kubernetes import client as kube

from releasetracker.executors import kubernetes_recovery
from releasetracker.executors.base import RuntimeMutationError
from test_kubernetes_snapshot_safety import workload as workload


@pytest.fixture(autouse=True)
def fast_observer(monkeypatch):
    monkeypatch.setattr(kubernetes_recovery, "VERIFY_INTERVAL", 0)
    monkeypatch.setattr(kubernetes_recovery, "VERIFY_TIMEOUT", 0.1)


def ready_model(obj, kind):
    ready = deepcopy(obj)
    ready.metadata.generation = 3
    if kind == "Deployment":
        ready.status = kube.V1DeploymentStatus(
            observed_generation=3,
            replicas=1,
            ready_replicas=1,
            updated_replicas=1,
            available_replicas=1,
            unavailable_replicas=0,
            conditions=[
                kube.V1DeploymentCondition(
                    type="Progressing", status="True", reason="NewReplicaSetAvailable"
                )
            ],
        )
    elif kind == "StatefulSet":
        ready.status = kube.V1StatefulSetStatus(
            observed_generation=3,
            replicas=1,
            ready_replicas=1,
            updated_replicas=1,
            current_revision="revision-new",
            update_revision="revision-new",
        )
    else:
        ready.status = kube.V1DaemonSetStatus(
            observed_generation=3,
            current_number_scheduled=1,
            desired_number_scheduled=1,
            number_misscheduled=0,
            number_ready=1,
            number_available=1,
            updated_number_scheduled=1,
            number_unavailable=0,
        )
    return ready


async def prepare(workload):
    adapter, target, obj, reader, patch = workload
    saved = await adapter.capture_snapshot(target, "unused")
    reader.reset_mock()
    ready = ready_model(obj, target["kind"])
    submitted = deepcopy(ready)
    submitted.status.observed_generation = 2
    patch.return_value = submitted
    return adapter, target, obj, reader, patch, saved, ready


@pytest.mark.asyncio
async def test_recovery_waits_for_generation_and_two_consecutive_ready_samples(workload):
    adapter, target, obj, reader, patch, saved, ready = await prepare(workload)
    pending = deepcopy(ready)
    pending.status.observed_generation = 2
    reader.side_effect = [obj, pending, ready, pending, ready, ready]
    result = await adapter.recover_from_snapshot(target, saved)
    assert result.updated
    assert "rollout verified" in result.message
    assert reader.call_count == 6
    patch.assert_called_once()
    for call in reader.call_args_list[1:]:
        assert 0 < call.kwargs["_request_timeout"] <= kubernetes_recovery.PROBE_TIMEOUT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["uid", "generation", "image", "replicas", "observed_generation", "read_error"]
)
async def test_post_patch_drift_or_incomplete_rollout_never_succeeds(workload, fault):
    adapter, target, obj, reader, patch, saved, ready = await prepare(workload)
    if fault == "uid":
        ready.metadata.uid = "replacement-uid"
    elif fault == "generation":
        ready.metadata.generation = 4
    elif fault == "image":
        ready.spec.template.spec.containers[0].image = (
            "registry.example.test/team/service-a:unexpected"
        )
    elif fault == "replicas":
        if target["kind"] == "DaemonSet":
            ready.status.number_ready = 0
        else:
            ready.status.ready_replicas = 0
    elif fault == "observed_generation":
        ready.status.observed_generation = 2
    reads = 0

    def read(*args, **kwargs):
        nonlocal reads
        reads += 1
        if reads == 1:
            return obj
        if fault == "read_error":
            raise TimeoutError("fixture API unavailable")
        return ready

    reader.side_effect = read
    with pytest.raises(RuntimeMutationError) as raised:
        await adapter.recover_from_snapshot(target, saved)
    assert raised.value.destructive_started is True
    patch.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["missing", "uid", "generation", "bool_generation", "older_generation"]
)
async def test_missing_or_invalid_patch_evidence_cannot_be_replaced_by_later_get(workload, fault):
    adapter, target, obj, reader, patch, saved, ready = await prepare(workload)
    if fault == "missing":
        patch.return_value = None
    elif fault == "uid":
        patch.return_value.metadata.uid = None
    elif fault == "generation":
        patch.return_value.metadata.generation = None
    elif fault == "bool_generation":
        patch.return_value.metadata.generation = True
    else:
        patch.return_value.metadata.generation = 1
    reader.return_value = obj
    with pytest.raises(RuntimeMutationError):
        await adapter.recover_from_snapshot(target, saved)
    assert reader.call_count == 1
    patch.assert_called_once()


@pytest.mark.asyncio
async def test_unsupported_rollout_is_rejected_before_patch(workload):
    adapter, target, obj, reader, patch, saved, _ = await prepare(workload)
    if target["kind"] == "Deployment":
        obj.spec.paused = True
    elif target["kind"] == "StatefulSet":
        obj.spec.update_strategy = kube.V1StatefulSetUpdateStrategy(type="OnDelete")
    else:
        obj.spec.update_strategy = kube.V1DaemonSetUpdateStrategy(type="OnDelete")
    with pytest.raises(ValueError, match="cannot verify"):
        await adapter.recover_from_snapshot(target, saved)
    patch.assert_not_called()


@pytest.mark.asyncio
async def test_kind_specific_rollout_failure_is_not_ready(workload):
    adapter, target, obj, reader, patch, saved, ready = await prepare(workload)
    if target["kind"] == "Deployment":
        ready.status.conditions = [
            kube.V1DeploymentCondition(
                type="Progressing", status="False", reason="ProgressDeadlineExceeded"
            )
        ]
    elif target["kind"] == "StatefulSet":
        ready.status.current_revision = "revision-old"
    else:
        ready.status.number_misscheduled = 1
    reader.side_effect = lambda *args, **kwargs: ready if kwargs else obj
    with pytest.raises(RuntimeMutationError):
        await adapter.recover_from_snapshot(target, saved)
    patch.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "image",
    [
        "registry.example.test/team/service-a:stable",
        "registry.example.test/team/service-a",
        "registry.example.test/team/service-a@sha256:invalid",
    ],
)
async def test_unproven_artifact_is_rejected_before_patch(workload, image):
    adapter, target, obj, reader, patch = workload
    obj.spec.template.spec.containers[0].image = image
    saved = await adapter.capture_snapshot(target, image)
    with pytest.raises(ValueError, match="digest-pinned"):
        await adapter.recover_from_snapshot(target, saved)
    patch.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [False, True])
async def test_real_recovery_task_only_releases_blocked_deployment_after_rollout(
    storage, workload, verified
):
    from releasetracker.executor_scheduler import ExecutorScheduler
    from releasetracker.models import ExecutorSnapshot
    from releasetracker.services.recovery_tasks import RecoveryTasks
    from test_kubernetes_snapshot_safety import create_executor

    adapter, target, obj, reader, patch, saved, ready = await prepare(workload)
    executor_id = await create_executor(storage, adapter, target)
    snapshot_id = await storage.create_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data=saved,
            trigger="pre_update",
            unredacted_persisted=True,
        )
    )
    executor = await storage.get_executor_config(executor_id)
    scheduler = ExecutorScheduler(storage)
    scheduler._adapters[executor_id] = adapter
    if not verified:
        ready.status.observed_generation = 2
    reader.side_effect = lambda *args, **kwargs: ready if kwargs else obj
    blocked = await storage.tasks.enqueue(
        kind="deploy",
        resource_key="deployment-mutations",
        dedupe_key="example-failed-deployment",
        target_label="example",
        payload={"executor_id": executor_id},
        trigger_mode="manual",
    )
    claimed = await storage.tasks.claim("deploy")
    await storage.tasks.finish(claimed, "needs_attention")
    tasks = RecoveryTasks(storage, scheduler)
    queued = await tasks.enqueue_recovery(executor, snapshot_id, "rollback", actor="fixture-admin")
    task = await storage.tasks.get(queued["task_id"])
    assert await tasks.prepare(task) is None
    result = await tasks.execute(task)
    persisted = await storage.tasks.get(blocked["id"])
    run = await storage.get_executor_run(result.result["run_id"])
    assert patch.call_count == 1, (run.message, run.diagnostics)
    if verified:
        assert run.status == "success"
        assert persisted["error_code"] == "operator_reconciled"
        assert persisted["state"] == "failed"
    else:
        assert run.status == "failed"
        assert result.state == "needs_attention"
        assert persisted["state"] == "needs_attention"
    latest = await storage.get_executor_snapshot(executor_id)
    assert latest.trigger == "pre_rollback"
    assert latest.id != snapshot_id
