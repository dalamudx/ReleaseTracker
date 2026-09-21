from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.services import deployment_readiness_probes as probes

IMAGE = "registry.example.test/team/service-a:1.2.0"


def workload(kind="Deployment"):
    return {
        "kind": kind,
        "metadata": {"uid": "fictional-uid", "generation": 4},
        "containers": [{"name": "service-a", "image": IMAGE}],
        "spec": {"replicas": 2},
        "status": {
            "observedGeneration": 4,
            "replicas": 2,
            "updatedReplicas": 2,
            "readyReplicas": 2,
            "availableReplicas": 2,
            "conditions": [
                {"type": "Progressing", "status": "True", "reason": "NewReplicaSetAvailable"}
            ],
        },
    }


@pytest.mark.parametrize(
    "change,outcome",
    [
        ({}, "healthy"),
        ({"updatedReplicas": 1}, "pending"),
        ({"replicas": 3}, "pending"),
        ({"observedGeneration": 3}, "pending"),
        ({"availableReplicas": 1}, "pending"),
        (
            {
                "conditions": [
                    {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded"}
                ]
            },
            "unhealthy",
        ),
    ],
)
def test_deployment_requires_all_new_replicas(change, outcome):
    submitted = workload()
    current = deepcopy(submitted)
    current["status"].update(change)
    assert probes._workload_status(current, submitted, {"service-a": IMAGE})[0] == outcome


@pytest.mark.parametrize("field,value", [("uid", "replacement"), ("generation", 5)])
def test_subsequent_generation_or_recreated_workload_is_superseded(field, value):
    submitted = workload()
    current = deepcopy(submitted)
    current["metadata"][field] = value
    assert probes._workload_status(current, submitted, {"service-a": IMAGE})[0] == "superseded"


def test_old_template_and_missing_submitted_identity_never_healthy():
    current = workload()
    assert probes._workload_status(current, {}, {"service-a": IMAGE})[0] == "unknown"
    assert (
        probes._workload_status(current, current, {"service-a": IMAGE + "-next"})[0] == "superseded"
    )


@pytest.mark.parametrize("strategy", [{"type": "OnDelete"}, {"rollingUpdate": {"partition": 1}}])
def test_statefulset_unsupported_rollout_semantics(strategy):
    current = workload("StatefulSet")
    current["spec"]["updateStrategy"] = strategy
    assert probes._workload_status(current, current, {"service-a": IMAGE})[0] == "unsupported"


def test_statefulset_revision_must_converge_and_not_be_superseded():
    submitted = workload("StatefulSet")
    submitted["status"].update(currentRevision="r2", updateRevision="r2")
    current = deepcopy(submitted)
    assert probes._workload_status(current, submitted, {"service-a": IMAGE})[0] == "healthy"
    current["status"]["currentRevision"] = "r1"
    assert probes._workload_status(current, submitted, {"service-a": IMAGE})[0] == "pending"
    current["status"].update(currentRevision="r3", updateRevision="r3")
    assert probes._workload_status(current, submitted, {"service-a": IMAGE})[0] == "superseded"


def test_daemonset_all_scheduled_replicas_required():
    current = workload("DaemonSet")
    current["status"].update(
        desiredNumberScheduled=3, updatedNumberScheduled=3, numberReady=3, numberAvailable=3
    )
    assert probes._workload_status(current, current, {"service-a": IMAGE})[0] == "healthy"
    current["status"]["updatedNumberScheduled"] = 2
    assert probes._workload_status(current, current, {"service-a": IMAGE})[0] == "pending"


def container(cid="new-a", health="healthy"):
    return {
        "id": cid,
        "image": IMAGE,
        "image_id": "sha256:" + "a" * 64,
        "state": {"Running": True, "Health": {"Status": health}},
        "restart_count": 0,
    }


@pytest.mark.parametrize(
    "change,outcome",
    [
        ("none", "healthy"),
        ("missing", "pending"),
        ("old-image", "superseded"),
        ("replaced", "superseded"),
        ("unhealthy", "unhealthy"),
        ("starting", "pending"),
    ],
)
def test_container_replica_identity_and_native_health(change, outcome):
    submitted = [container(), container("new-b")]
    current = deepcopy(submitted)
    if change == "missing":
        current.pop()
    elif change == "old-image":
        current[1]["image_id"] = "sha256:" + "b" * 64
    elif change == "replaced":
        current[1]["id"] = "external-replacement"
    elif change in {"unhealthy", "starting"}:
        current[1]["state"]["Health"]["Status"] = change
    assert probes._container_status(current, submitted, IMAGE, 2)[0] == outcome


def test_no_healthcheck_reports_runtime_state_and_detects_restarts():
    submitted = [container(health=None)]
    current = deepcopy(submitted)
    assert probes._container_status(current, submitted, IMAGE, 1)[0] == "healthy"
    current[0]["restart_count"] = 1
    assert probes._container_status(current, submitted, IMAGE, 1)[0] == "unhealthy"
    current[0]["restart_count"] = None
    assert probes._container_status(current, submitted, IMAGE, 1) == (
        "healthy",
        "Running; no healthcheck configured",
    )


@pytest.mark.asyncio
async def test_probe_contract_and_old_containers_fail_closed(monkeypatch):
    snapshot = {"containers": {"service-a": [container()]}}
    monkeypatch.setattr(probes, "_capture", AsyncMock(return_value=snapshot))
    executor = SimpleNamespace(target_ref={"mode": "docker_compose"})
    result = await probes.probe_deployment(
        None,
        None,
        executor,
        {
            "baseline": snapshot,
            "target": snapshot,
            "services": [{"service": "service-a", "to_version": IMAGE, "status": "success"}],
        },
    )
    assert result["outcome"] == "pending"
    assert set(result["services"][0]) == {"service", "status", "method", "message"}


@pytest.mark.asyncio
async def test_capture_error_redacted_and_no_healthy_fallback(monkeypatch):
    monkeypatch.setattr(probes, "_capture", AsyncMock(side_effect=RuntimeError("token=secret")))
    assert await probes.capture_deployment_baseline(None, None, None) == {
        "error": "native identity capture unavailable"
    }
    result = await probes.probe_deployment(None, None, SimpleNamespace(target_ref={}), {})
    assert result["outcome"] == "unknown"
    assert "secret" not in str(result)


@pytest.mark.asyncio
async def test_async_enrollment_is_pending_then_pinned_and_runtime_state(monkeypatch):
    old = {"containers": {"service-a": [container("old-a"), container("old-b")]}}
    new = {"containers": {"service-a": [container("new-a", None), container("new-b", None)]}}
    monkeypatch.setattr(probes, "_capture", AsyncMock(return_value=new))
    executor = SimpleNamespace(target_ref={"mode": "portainer_stack"})
    verification = {
        "baseline": old,
        "target": old,
        "services": [{"service": "service-a", "to_version": IMAGE}],
    }
    result = await probes.probe_deployment(None, None, executor, verification)
    assert result["outcome"] == "pending"
    assert result["verification_update"]["target"]["containers"] == new["containers"]
    verification.update(result["verification_update"])
    result = await probes.probe_deployment(None, None, executor, verification)
    assert result["outcome"] == "healthy"
    assert result["services"][0]["method"] == "runtime_state"
    new["containers"]["service-a"][1] = container("external")
    # Simulate persisted JSON, not a shared mutable in-memory object.
    verification["target"] = {
        "containers": {"service-a": [container("new-a", None), container("new-b", None)]}
    }
    assert (await probes.probe_deployment(None, None, executor, verification))[
        "outcome"
    ] == "superseded"


def test_missing_count_and_incomplete_enrollment_cannot_pass():
    new = {"containers": {"service-a": [container()]}}
    assert (
        probes._probe_containers(new, {"target": new}, {"service-a": IMAGE})["outcome"] == "unknown"
    )
    old = {"containers": {"service-a": [container("old-a"), container("old-b")]}}
    result = probes._probe_containers(new, {"baseline": old, "target": old}, {"service-a": IMAGE})
    assert result["outcome"] == "pending" and "verification_update" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("generation,outcome", [(4, "healthy"), (5, "superseded"), (3, "pending")])
async def test_kubernetes_post_capture_generation_is_exactly_next(monkeypatch, generation, outcome):
    before = workload()
    before["metadata"]["generation"] = 3
    current = workload()
    current["metadata"]["generation"] = generation
    current["status"]["observedGeneration"] = generation
    snapshot = {"kind": "kubernetes_workload", "workloads": {"Deployment/service-a": current}}
    monkeypatch.setattr(probes, "_capture", AsyncMock(return_value=snapshot))
    result = await probes.probe_deployment(
        None,
        None,
        SimpleNamespace(target_ref={"mode": "kubernetes_workload"}),
        {
            "baseline": {"workloads": {"Deployment/service-a": before}},
            "target": snapshot,
            "services": [{"service": "service-a", "to_version": IMAGE}],
        },
    )
    assert result["outcome"] == outcome


@pytest.mark.asyncio
async def test_ssh_no_healthcheck_uses_pinned_identity_and_counts(monkeypatch):
    old = {"containers": {"service-a": [container("old")]}}
    target = {"containers": {"service-a": [container("new", None)]}}
    current = {**target, "images": {"service-a": IMAGE}}
    monkeypatch.setattr(probes, "_capture_ssh", AsyncMock(return_value=current))
    context = {
        "expected": {"service-a": "sha256:" + "a" * 64},
        "counts": {"service-a": 1},
        "targets": {"service-a": IMAGE},
    }
    result = await probes.probe_deployment(
        None,
        None,
        SimpleNamespace(target_ref={"mode": "ssh_compose"}),
        {"baseline": old, "target": target, "readiness_context": context},
    )
    assert result["outcome"] == "healthy"
    assert result["services"][0]["method"] == "runtime_state"
    current["images"]["service-a"] = IMAGE + "-external"
    result = await probes.probe_deployment(
        None,
        None,
        SimpleNamespace(target_ref={"mode": "ssh_compose"}),
        {"baseline": old, "target": target, "readiness_context": context},
    )
    assert result["outcome"] == "superseded"


@pytest.mark.asyncio
async def test_capture_baseline_materializes_credentials_and_redacts(monkeypatch):
    from releasetracker.services import runtime_credentials
    from unittest.mock import Mock

    connection = SimpleNamespace(enabled=True)
    hydrated = SimpleNamespace(enabled=True)
    materialize = AsyncMock(return_value=hydrated)
    monkeypatch.setattr(
        runtime_credentials, "materialize_runtime_connection_credentials", materialize
    )
    item = SimpleNamespace(
        id="fictional-id",
        attrs={
            "Image": "sha256:" + "a" * 64,
            "Config": {"Image": IMAGE, "Env": ["SECRET=do-not-persist"]},
            "State": {"Running": True, "Health": {"Status": "healthy", "Log": ["SECRET"]}},
        },
    )
    adapter = SimpleNamespace(_get_container=Mock(return_value=item))
    scheduler = SimpleNamespace(_get_adapter=Mock(return_value=adapter))
    storage = SimpleNamespace(get_runtime_connection=AsyncMock(return_value=connection))
    executor = SimpleNamespace(
        id=1,
        runtime_connection_id=2,
        target_ref={"mode": "container", "container_name": "service-a"},
    )
    result = await probes.capture_deployment_baseline(storage, scheduler, executor)
    scheduler._get_adapter.assert_called_once_with(1, hydrated)
    materialize.assert_awaited_once_with(storage, connection)
    assert result["containers"]["container"][0]["id"] == "fictional-id"
    assert "SECRET" not in str(result)


@pytest.mark.asyncio
async def test_helm_old_deployed_revision_is_not_healthy(monkeypatch):
    captured = {
        "kind": "helm_release",
        "release": {"version": 2, "info": {"status": "deployed"}},
        "workloads": {"Deployment/service-a": workload()},
    }
    monkeypatch.setattr(probes, "_capture", AsyncMock(return_value=captured))
    executor = SimpleNamespace(target_ref={"mode": "helm_release"})
    assert (
        await probes.probe_deployment(
            None, None, executor, {"baseline": captured, "target": captured}
        )
    )["outcome"] == "pending"
    assert (
        await probes.probe_deployment(
            None, None, executor, {"baseline": {"release": {"version": 1}}, "target": captured}
        )
    )["outcome"] == "healthy"


def test_configured_healthcheck_without_report_is_not_runtime_state():
    current = [container(health=None)]
    current[0]["healthcheck_configured"] = True
    assert probes._container_status(current, current, IMAGE, 1)[0] == "pending"


@pytest.mark.asyncio
async def test_ssh_scheduler_defers_and_preserves_snapshot(monkeypatch):
    from releasetracker import executor_scheduler_ssh as module
    from releasetracker.services.deployment_readiness_context import DEFER_READINESS

    execute = AsyncMock(
        return_value={
            "status": "success",
            "snapshot_id": 42,
            "readiness_context": {
                "deferred": True,
                "expected": {"service-a": "sha256:" + "a" * 64},
                "counts": {"service-a": 1},
            },
        }
    )
    monkeypatch.setattr(module, "execute_update", execute)
    scheduler = module.ExecutorSchedulerSSH()
    scheduler.storage = SimpleNamespace(set_executor_snapshot_locked=AsyncMock())
    scheduler._resolve_ssh_update = AsyncMock(
        return_value=(object(), object(), {"service-a": IMAGE})
    )
    scheduler._create_run_record = AsyncMock(return_value=7)
    scheduler._finalize_run = AsyncMock(return_value="finalized")
    scheduler._prune_snapshot_history = AsyncMock()
    executor = SimpleNamespace(id=1, enabled=True)
    token = DEFER_READINESS.set(True)
    try:
        assert await scheduler._execute_ssh_compose_executor(executor, manual=True) == "finalized"
    finally:
        DEFER_READINESS.reset(token)
    assert execute.await_args.kwargs["defer_verification"] is True
    scheduler.storage.set_executor_snapshot_locked.assert_not_awaited()
    scheduler._prune_snapshot_history.assert_not_awaited()
    final = scheduler._finalize_run.await_args.kwargs
    assert "pending" in final["message"] and "verified" not in final["message"]
    assert final["diagnostics"]["readiness_context"]["deferred"] is True


@pytest.mark.asyncio
async def test_target_capture_prefers_mutation_response_identity(monkeypatch):
    captured = {"kind": "kubernetes_workload", "workloads": {"Deployment/service-a": workload()}}
    captured["workloads"]["Deployment/service-a"]["metadata"]["generation"] = 5
    monkeypatch.setattr(probes, "capture_deployment_baseline", AsyncMock(return_value=captured))
    adapter = SimpleNamespace(
        _readiness_submission={
            "namespace": "test",
            "kind": "Deployment",
            "name": "service-a",
            "metadata": {"uid": "fictional-uid", "generation": 4},
            "images": {"service-a": IMAGE},
        }
    )
    monkeypatch.setattr(probes, "_adapter", AsyncMock(return_value=adapter))
    executor = SimpleNamespace(
        target_ref={
            "mode": "kubernetes_workload",
            "namespace": "test",
            "kind": "Deployment",
            "name": "service-a",
        }
    )
    target = await probes.capture_deployment_target(None, None, executor)
    assert target["workloads"]["Deployment/service-a"]["metadata"]["generation"] == 4
    assert target["submitted_images"] == {"service-a": IMAGE}
    probes.capture_deployment_baseline.assert_not_awaited()


@pytest.mark.asyncio
async def test_resource_scope_uses_uid_not_connection_alias(monkeypatch):
    evidence = {"workloads": {"Deployment/service-a": workload()}}
    monkeypatch.setattr(probes, "capture_deployment_baseline", AsyncMock(return_value=evidence))
    first = SimpleNamespace(runtime_connection_id=1, target_ref={"mode": "kubernetes_workload"})
    alias = SimpleNamespace(runtime_connection_id=2, target_ref={"mode": "kubernetes_workload"})
    assert (
        await probes.resolve_deployment_resource_scope(None, None, first)
        == await probes.resolve_deployment_resource_scope(None, None, alias)
        == "kubernetes:uid:fictional-uid"
    )
    evidence["workloads"]["Deployment/service-a"]["metadata"]["uid"] = "other-uid"
    assert (
        await probes.resolve_deployment_resource_scope(None, None, first)
        == "kubernetes:uid:other-uid"
    )
    evidence.clear()
    assert await probes.resolve_deployment_resource_scope(None, None, first) == "*"
    assert (
        await probes.resolve_deployment_resource_scope(
            None, None, SimpleNamespace(target_ref={"mode": "helm_release"})
        )
        == "*"
    )


@pytest.mark.asyncio
async def test_timed_out_sdk_read_does_not_spawn_another_worker(monkeypatch):
    import asyncio
    from threading import Event
    from unittest.mock import Mock

    release = Event()
    adapter = object()
    real_wait_for = asyncio.wait_for

    async def short_wait(awaitable, *, timeout):
        return await real_wait_for(awaitable, timeout=0.01)

    monkeypatch.setattr(probes.asyncio, "wait_for", short_wait)
    callback = Mock(side_effect=lambda: release.wait(2))
    try:
        with pytest.raises(TimeoutError):
            await probes._bounded_thread(adapter, "test", callback)
        with pytest.raises(TimeoutError, match="still outstanding"):
            await probes._bounded_thread(adapter, "test", callback)
        callback.assert_called_once()
    finally:
        release.set()
        task = probes._READS.get((id(adapter), "test"))
        if task:
            await task


@pytest.mark.asyncio
async def test_portainer_reads_actual_container_health_not_stack_status():
    from unittest.mock import Mock
    from releasetracker.services.runtime_policy import RuntimeOperationPolicy

    adapter = SimpleNamespace(
        _operation_policy=RuntimeOperationPolicy(),
        fetch_stack_detail=AsyncMock(return_value={"Name": "project-a", "Status": 1}),
        _resolve_stack_type=Mock(return_value="standalone"),
        _request_payload=AsyncMock(
            side_effect=[
                [{"Id": "fictional-container"}],
                {
                    "Id": "fictional-container",
                    "Image": "sha256:" + "a" * 64,
                    "Config": {
                        "Image": IMAGE,
                        "Labels": {"com.docker.compose.service": "service-a"},
                    },
                    "State": {
                        "Running": True,
                        "Health": {"Status": "unhealthy", "Log": ["private"]},
                    },
                },
            ]
        ),
    )
    groups = await probes._portainer_containers(adapter, {"endpoint_id": 1, "stack_id": 2})
    assert groups["service-a"][0]["state"]["Health"]["Status"] == "unhealthy"
    assert "private" not in str(groups)
    assert adapter._operation_policy.read_retries == 1
    assert adapter._request_payload.await_count == 2
