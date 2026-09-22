"""Real Kubernetes models, encrypted snapshots and conditional recovery boundaries."""

from copy import deepcopy
from datetime import date, datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from kubernetes import client as kube
from kubernetes.client.exceptions import ApiException

from helpers.executor_runtime import save_docker_tracker_config
from releasetracker.config import (
    Channel,
    ExecutorConfig,
    ExecutorServiceBinding,
    RuntimeConnectionConfig,
)
from releasetracker.executor_scheduler import ExecutorScheduler
from releasetracker.executors.kubernetes import KubernetesRuntimeAdapter
from releasetracker.models import ExecutorSnapshot
from releasetracker.services.snapshot_integrity import verify_snapshot_integrity

WHEN = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
IMAGE = "registry.example.test/team/service-a@sha256:" + "a" * 64
SECRET = "fixture-only-secret-value"
METHODS = {"Deployment": "deployment", "StatefulSet": "stateful_set", "DaemonSet": "daemon_set"}


def model(kind):
    metadata = kube.V1ObjectMeta(
        name="service-a",
        namespace="apps",
        uid="fixture-workload-uid",
        resource_version="7",
        generation=2,
        creation_timestamp=WHEN,
        managed_fields=[
            kube.V1ManagedFieldsEntry(
                manager="fixture-manager", operation="Update", api_version="apps/v1", time=WHEN
            )
        ],
    )
    template = kube.V1PodTemplateSpec(
        metadata=kube.V1ObjectMeta(labels={"app": "service-a"}, creation_timestamp=WHEN),
        spec=kube.V1PodSpec(
            containers=[
                kube.V1Container(
                    name="service-a",
                    image=IMAGE,
                    env=[kube.V1EnvVar(name="API_TOKEN", value=SECRET)],
                )
            ]
        ),
    )
    selector = kube.V1LabelSelector(match_labels={"app": "service-a"})
    if kind == "Deployment":
        return kube.V1Deployment(
            metadata=metadata,
            spec=kube.V1DeploymentSpec(selector=selector, template=template, replicas=1),
            status=kube.V1DeploymentStatus(
                conditions=[
                    kube.V1DeploymentCondition(
                        type="Available",
                        status="True",
                        last_transition_time=WHEN,
                        last_update_time=WHEN,
                    )
                ]
            ),
        )
    if kind == "StatefulSet":
        return kube.V1StatefulSet(
            metadata=metadata,
            spec=kube.V1StatefulSetSpec(
                selector=selector, template=template, service_name="service-a", replicas=1
            ),
            status=kube.V1StatefulSetStatus(
                replicas=1,
                conditions=[
                    kube.V1StatefulSetCondition(
                        type="Ready", status="True", last_transition_time=WHEN
                    )
                ],
            ),
        )
    return kube.V1DaemonSet(
        metadata=metadata,
        spec=kube.V1DaemonSetSpec(selector=selector, template=template),
        status=kube.V1DaemonSetStatus(
            current_number_scheduled=1,
            desired_number_scheduled=1,
            number_misscheduled=0,
            number_ready=1,
            conditions=[
                kube.V1DaemonSetCondition(type="Ready", status="True", last_transition_time=WHEN)
            ],
        ),
    )


@pytest.fixture(params=list(METHODS))
def workload(request):
    kind = request.param
    obj = model(kind)
    api = SimpleNamespace()
    reader = Mock(return_value=obj)
    patch = Mock(return_value=deepcopy(obj))
    setattr(api, f"read_namespaced_{METHODS[kind]}", reader)
    setattr(api, f"patch_namespaced_{METHODS[kind]}", patch)
    runtime = RuntimeConnectionConfig(
        name="example-cluster", type="kubernetes", config={"namespace": "apps", "in_cluster": True}
    )
    adapter = KubernetesRuntimeAdapter(runtime, apps_api=api)
    target = {"mode": "kubernetes_workload", "namespace": "apps", "kind": kind, "name": "service-a"}
    return adapter, target, obj, reader, patch


async def create_executor(storage, adapter, target):
    runtime_id = await storage.create_runtime_connection(adapter.runtime_connection)
    await save_docker_tracker_config(
        storage,
        name="example-source",
        image="registry.example.test/team/service-a",
        channels=[Channel(name="stable", type="release")],
    )
    tracker = await storage.get_aggregate_tracker("example-source")
    source_id = tracker.sources[0].id
    return await storage.save_executor_config(
        ExecutorConfig(
            name="example-executor",
            runtime_type="kubernetes",
            runtime_connection_id=runtime_id,
            tracker_name="example-source",
            tracker_source_id=source_id,
            channel_name="stable",
            update_mode="manual",
            target_ref=target,
            service_bindings=[
                ExecutorServiceBinding(
                    service="service-a", tracker_source_id=source_id, channel_name="stable"
                )
            ],
        )
    )


@pytest.mark.asyncio
async def test_real_workload_snapshot_round_trips_through_encrypted_storage(storage, workload):
    adapter, target, obj, _, patch = workload
    executor_id = await create_executor(storage, adapter, target)
    payload = await adapter.capture_snapshot(target, IMAGE)
    assert obj.metadata.creation_timestamp is WHEN
    snapshot_id = await storage.create_executor_snapshot(
        ExecutorSnapshot(
            executor_id=executor_id,
            snapshot_data=payload,
            trigger="pre_update",
            unredacted_persisted=True,
        )
    )
    restored = await storage.get_executor_snapshot(executor_id)
    assert restored.id == snapshot_id
    assert verify_snapshot_integrity(restored) == "verified"
    saved = restored.snapshot_data["workload"]
    assert saved["metadata"]["creation_timestamp"] == WHEN.isoformat()
    assert saved["metadata"]["managed_fields"][0]["time"] == WHEN.isoformat()
    assert saved["spec"]["template"]["metadata"]["creation_timestamp"] == WHEN.isoformat()
    assert saved["status"]["conditions"][0]["last_transition_time"] == WHEN.isoformat()
    assert saved["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] == SECRET
    db = await storage._get_connection()
    row = await (
        await db.execute("SELECT snapshot_data FROM executor_snapshots WHERE id=?", (snapshot_id,))
    ).fetchone()
    assert json.loads(row[0])["kind"] == "executor_encrypted_v1"
    assert SECRET not in row[0]
    patch.assert_not_called()


def test_recursive_date_serialization_preserves_none_and_input():
    value = {"nested": ({"date": date(2026, 1, 2), "at": WHEN, "optional": None},)}
    converted = KubernetesRuntimeAdapter._readiness_fields(value)
    assert json.loads(json.dumps(converted)) == {
        "nested": [{"date": "2026-01-02", "at": WHEN.isoformat(), "optional": None}]
    }
    assert value["nested"][0]["at"] is WHEN


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_fails", [False, True])
async def test_scheduler_persists_snapshot_before_mutation_and_patch(
    storage, workload, monkeypatch, capture_fails
):
    adapter, target, _, _, patch = workload
    executor_id = await create_executor(storage, adapter, target)
    scheduler = ExecutorScheduler(storage)
    scheduler._adapters[executor_id] = adapter
    scheduler._resolve_tracker_latest_target = AsyncMock(return_value=("2.0.0", None))
    events = []
    save = storage.create_executor_snapshot

    async def capture(snapshot):
        if capture_fails:
            raise OSError("fixture snapshot storage unavailable")
        result = await save(snapshot)
        events.append("snapshot")
        return result

    async def mark():
        assert events == ["snapshot"]
        events.append("mutation")

    response = patch.return_value

    def apply(*args, **kwargs):
        assert events == ["snapshot", "mutation"]
        events.append("patch")
        return response

    monkeypatch.setattr(storage, "create_executor_snapshot", capture)
    monkeypatch.setattr(
        "releasetracker.executor_scheduler_grouped_runtime_kubernetes.mark_deployment_mutation",
        mark,
    )
    patch.side_effect = apply
    outcome = await scheduler.run_executor_now(executor_id)
    if capture_fails:
        assert outcome.status == "failed"
        assert "snapshot storage unavailable" in outcome.message
        assert events == []
        patch.assert_not_called()
        assert await storage.get_executor_snapshot(executor_id) is None
    else:
        assert outcome.status == "success", outcome.message
        assert events == ["snapshot", "mutation", "patch"]
        saved = await storage.get_executor_snapshot(executor_id)
        assert saved.snapshot_data["workload"]["metadata"]["creation_timestamp"] == WHEN.isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "recreated",
        "missing_saved_uid",
        "missing_live_uid",
        "missing_resource_version",
        "wrong_runtime",
        "unauthorized_namespace",
    ],
)
async def test_recovery_rejects_identity_or_authorization_gap_without_patch(workload, fault):
    adapter, target, obj, reader, patch = workload
    saved = await adapter.capture_snapshot(target, IMAGE)
    reader.reset_mock()
    if fault == "recreated":
        obj.metadata.uid = "different-workload-uid"
    elif fault == "missing_saved_uid":
        saved["workload"]["metadata"].pop("uid")
    elif fault == "missing_live_uid":
        obj.metadata.uid = None
    elif fault == "missing_resource_version":
        obj.metadata.resource_version = None
    elif fault == "wrong_runtime":
        saved["runtime_type"] = "docker"
    else:
        target = dict(target, namespace="unconfigured")
        saved["namespace"] = "unconfigured"
    with pytest.raises(ValueError):
        await adapter.recover_from_snapshot(target, saved)
    patch.assert_not_called()
    if fault in {"wrong_runtime", "unauthorized_namespace"}:
        reader.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_submits_uid_and_latest_resource_version_without_replaying_conflict(
    workload,
):
    adapter, target, obj, _, patch = workload
    saved = await adapter.capture_snapshot(target, IMAGE)
    obj.metadata.resource_version = "8"
    patch.side_effect = ApiException(status=409, reason="Conflict")
    with pytest.raises(ApiException):
        await adapter.recover_from_snapshot(target, saved)
    patch.assert_called_once()
    body = patch.call_args.args[2]
    assert body[:2] == [
        {"op": "test", "path": "/metadata/uid", "value": "fixture-workload-uid"},
        {"op": "test", "path": "/metadata/resourceVersion", "value": "8"},
    ]
    assert body[2] == {"op": "replace", "path": "/spec", "value": saved["workload"]["api_spec"]}
