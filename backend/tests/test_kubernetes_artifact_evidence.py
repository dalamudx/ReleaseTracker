"""Only controller-bound, converged Pod manifest evidence can pin historical tags."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from kubernetes import client as kube

from releasetracker.executors import kubernetes_artifact_evidence as evidence
from releasetracker.executors import kubernetes_recovery
from test_kubernetes_snapshot_safety import workload as workload
from test_kubernetes_recovery_rollout import ready_model

TAG = "registry.example.test/team/service-a:1.0.0"
INIT_TAG = "registry.example.test/team/init:1.0.0"
PIN = "registry.example.test/team/service-a@sha256:" + "a" * 64
INIT_PIN = "registry.example.test/team/init@sha256:" + "b" * 64


def ref(kind, uid):
    return kube.V1OwnerReference(
        api_version="apps/v1", kind=kind, name="service-a", uid=uid, controller=True
    )


@pytest.fixture
def observed(workload, monkeypatch):
    adapter, target, obj, reader, patch = workload
    kind = target["kind"]
    obj.metadata.generation = 3
    obj.metadata.annotations = {"deployment.kubernetes.io/revision": "2"}
    obj.spec.template.spec.containers[0].image = TAG
    obj.spec.template.spec.init_containers = [kube.V1Container(name="initialize", image=INIT_TAG)]
    obj.status = ready_model(obj, kind).status
    if kind == "DaemonSet":
        for key in (
            "current_number_scheduled",
            "desired_number_scheduled",
            "number_ready",
            "number_available",
            "updated_number_scheduled",
        ):
            setattr(obj.status, key, 2)
    else:
        obj.spec.replicas = 2
        for key in ("replicas", "ready_replicas", "updated_replicas"):
            setattr(obj.status, key, 2)
        if kind == "Deployment":
            obj.status.available_replicas = 2
    uid = obj.metadata.uid
    rs = kube.V1ReplicaSet(
        metadata=kube.V1ObjectMeta(
            name="service-a-new",
            namespace="apps",
            uid="replica-uid",
            annotations={"deployment.kubernetes.io/revision": "2"},
            labels={"pod-template-hash": "hash-new"},
            owner_references=[ref(kind, uid)],
        ),
        spec=kube.V1ReplicaSetSpec(
            selector=obj.spec.selector, template=deepcopy(obj.spec.template), replicas=2
        ),
    )
    template = evidence.normalize_spec(adapter._workload_from_obj(kind, obj)["api_spec"])[
        "template"
    ]
    revision = kube.V1ControllerRevision(
        metadata=kube.V1ObjectMeta(
            name="revision-new",
            namespace="apps",
            uid="revision-uid",
            labels={"controller.kubernetes.io/hash": "hash-new"},
            owner_references=[ref(kind, uid)],
        ),
        revision=2,
        data={"spec": {"template": {**deepcopy(template), "$patch": "replace"}}},
    )
    if kind == "DaemonSet":
        revision.metadata.name = "service-a-hash-new"
    owner_kind, owner_uid = ("ReplicaSet", "replica-uid") if kind == "Deployment" else (kind, uid)
    label = "pod-template-hash" if kind == "Deployment" else "controller-revision-hash"
    label_value = "revision-new" if kind == "StatefulSet" else "hash-new"
    pods = []
    for i in range(2):
        pod_spec = deepcopy(obj.spec.template.spec)
        pod_spec.node_name = f"example-node-{i}"
        pod = kube.V1Pod(
            metadata=kube.V1ObjectMeta(
                name=f"service-a-{i}",
                namespace="apps",
                uid=f"pod-{i}",
                resource_version=f"20{i}",
                labels={label: label_value},
                owner_references=[ref(owner_kind, owner_uid)],
            ),
            spec=pod_spec,
            status=kube.V1PodStatus(
                phase="Running",
                conditions=[kube.V1PodCondition(type="Ready", status="True")],
                container_statuses=[
                    kube.V1ContainerStatus(
                        name="service-a",
                        image=TAG,
                        image_id="docker-pullable://" + PIN,
                        ready=True,
                        restart_count=0,
                        state=kube.V1ContainerState(running=kube.V1ContainerStateRunning()),
                    )
                ],
                init_container_statuses=[
                    kube.V1ContainerStatus(
                        name="initialize",
                        image=INIT_TAG,
                        image_id=INIT_PIN,
                        ready=False,
                        restart_count=0,
                        state=kube.V1ContainerState(
                            terminated=kube.V1ContainerStateTerminated(exit_code=0)
                        ),
                    )
                ],
            ),
        )
        pods.append(pod)
    pod_list = kube.V1PodList(items=pods, metadata=kube.V1ListMeta(resource_version="200"))
    rs_list = kube.V1ReplicaSetList(items=[rs], metadata=kube.V1ListMeta(resource_version="200"))
    revisions = kube.V1ControllerRevisionList(
        items=[revision], metadata=kube.V1ListMeta(resource_version="200")
    )
    apps = adapter._apps_api
    apps.list_namespaced_replica_set = Mock(return_value=rs_list)
    apps.read_namespaced_controller_revision = Mock(return_value=revision)
    apps.list_namespaced_controller_revision = Mock(return_value=revisions)
    adapter._core_api = SimpleNamespace(list_namespaced_pod=Mock(return_value=pod_list))
    monkeypatch.setattr(kubernetes_recovery, "VERIFY_INTERVAL", 0)
    monkeypatch.setattr(kubernetes_recovery, "VERIFY_TIMEOUT", 0.1)
    return SimpleNamespace(
        adapter=adapter,
        target=target,
        obj=obj,
        reader=reader,
        patch=patch,
        pods=pods,
        pod_list=pod_list,
        rs=rs,
        rs_list=rs_list,
        revision=revision,
        revisions=revisions,
    )


@pytest.mark.asyncio
async def test_capture_retains_tags_and_recovery_uses_only_proven_manifests(observed):
    x = observed
    saved = await x.adapter.capture_snapshot(x.target, TAG)
    proof = saved["artifact_evidence"]
    assert proof["status"] == "verified", proof
    assert proof["images"] == {
        "containers": {"service-a": PIN},
        "initContainers": {"initialize": INIT_PIN},
    }
    assert len(proof["pods"]) == 2
    assert saved["containers"]["service-a"] == TAG
    assert (
        saved["workload"]["api_spec"]["template"]["spec"]["initContainers"][0]["image"] == INIT_TAG
    )
    original = deepcopy(saved)
    restored = deepcopy(x.obj)
    restored.metadata.generation = 4
    restored.status.observed_generation = 4
    restored.spec.template.spec.containers[0].image = PIN
    restored.spec.template.spec.init_containers[0].image = INIT_PIN
    x.patch.return_value = restored
    x.reader.side_effect = lambda *args, **kwargs: restored if x.patch.called else x.obj
    result = await x.adapter.recover_from_snapshot(x.target, saved)
    assert result.updated
    x.patch.assert_called_once()
    pod = x.patch.call_args.args[2][2]["value"]["template"]["spec"]
    assert pod["containers"][0]["image"] == PIN
    assert pod["initContainers"][0]["image"] == INIT_PIN
    assert saved == original
    call = x.adapter._core_api.list_namespaced_pod.call_args
    assert call.kwargs == {
        "label_selector": "app=service-a",
        "limit": evidence.LIMIT,
        "_request_timeout": evidence.READ_TIMEOUT,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "config_id",
        "other_repository",
        "replica_disagreement",
        "init_disagreement",
        "not_ready",
        "init_failed",
        "owner",
        "revision",
        "missing_replica",
        "duplicate_uid",
        "pagination",
        "status_missing",
        "pod_tag_changed",
        "workload_drift",
        "rbac",
    ],
)
async def test_unproven_evidence_never_makes_tag_snapshot_recoverable(observed, fault):
    x = observed
    p = x.pods[0]
    if fault == "config_id":
        p.status.container_statuses[0].image_id = "containerd://sha256:" + "c" * 64
    elif fault == "other_repository":
        p.status.container_statuses[0].image_id = PIN.replace("team/service-a", "other/service-a")
    elif fault == "replica_disagreement":
        p.status.container_statuses[0].image_id = PIN.replace("a" * 64, "c" * 64)
    elif fault == "init_disagreement":
        p.status.init_container_statuses[0].image_id = INIT_PIN.replace("b" * 64, "c" * 64)
    elif fault == "not_ready":
        p.status.conditions[0].status = "False"
    elif fault == "init_failed":
        p.status.init_container_statuses[0].state.terminated.exit_code = 1
    elif fault == "owner":
        p.metadata.owner_references[0].uid = "unrelated-controller"
    elif fault == "revision":
        p.metadata.labels = {key: "old-revision" for key in p.metadata.labels}
    elif fault == "missing_replica":
        x.pod_list.items = x.pods[:1]
    elif fault == "duplicate_uid":
        p.metadata.uid = x.pods[1].metadata.uid
    elif fault == "pagination":
        x.pod_list.metadata._continue = "remaining-page"
    elif fault == "status_missing":
        p.status.container_statuses = []
    elif fault == "pod_tag_changed":
        p.spec.containers[0].image = TAG + "-changed"
    elif fault == "workload_drift":
        changed = deepcopy(x.obj)
        changed.metadata.resource_version = "8"
        x.reader.side_effect = lambda *args, **kwargs: changed if kwargs else x.obj
    else:
        x.adapter._core_api.list_namespaced_pod.side_effect = RuntimeError(
            "fixture forbidden credential=do-not-persist"
        )
    saved = await x.adapter.capture_snapshot(x.target, TAG)
    assert saved["artifact_evidence"]["status"] == "unavailable"
    assert "do-not-persist" not in str(saved)
    with pytest.raises(ValueError, match="digest-pinned"):
        await x.adapter.recover_from_snapshot(x.target, saved)
    x.patch.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["binding", "uid", "spec", "repository", "missing_init", "schema"]
)
async def test_evidence_cannot_be_transplanted_or_partially_applied(observed, fault):
    x = observed
    saved = await x.adapter.capture_snapshot(x.target, TAG)
    assert saved["artifact_evidence"]["status"] == "verified"
    if fault == "binding":
        saved["artifact_evidence"]["binding"] = "wrong"
    elif fault == "uid":
        saved["workload"]["metadata"]["uid"] = "different-workload"
    elif fault == "spec":
        saved["workload"]["api_spec"]["template"]["spec"]["serviceAccountName"] = "other"
    elif fault == "repository":
        saved["artifact_evidence"]["images"]["containers"]["service-a"] = PIN.replace(
            "team/", "other/"
        )
    elif fault == "missing_init":
        saved["artifact_evidence"]["images"]["initContainers"] = {}
    else:
        saved["artifact_evidence"]["schema"] = 2
    with pytest.raises(ValueError, match="evidence is invalid"):
        await x.adapter.recover_from_snapshot(x.target, saved)
    x.patch.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "controller_owner",
        "controller_template",
        "controller_pagination",
        "workload_unready",
        "selector",
        "terminating_pod",
        "init_missing",
    ],
)
async def test_controller_and_completeness_proof_are_mandatory(observed, fault):
    x = observed
    if fault == "controller_owner":
        x.rs.metadata.owner_references[0].uid = "other-owner"
        x.revision.metadata.owner_references[0].uid = "other-owner"
    elif fault == "controller_template":
        x.rs.spec.template.spec.containers[0].image += "-other"
        x.revision.data["spec"]["template"]["spec"]["containers"][0]["image"] += "-other"
    elif fault == "controller_pagination":
        x.rs_list.metadata._continue = "more"
        x.revisions.metadata._continue = "more"
        if x.target["kind"] == "StatefulSet":
            x.revision.metadata.deletion_timestamp = datetime.now(timezone.utc)
    elif fault == "workload_unready":
        x.obj.status.observed_generation = 2
    elif fault == "selector":
        x.obj.spec.selector.match_expressions = [
            kube.V1LabelSelectorRequirement(key="environment", operator="Exists")
        ]
    elif fault == "terminating_pod":
        x.pods[0].metadata.deletion_timestamp = datetime.now(timezone.utc)
    else:
        x.pods[0].status.init_container_statuses = []
    saved = await x.adapter.capture_snapshot(x.target, TAG)
    assert saved["artifact_evidence"]["status"] == "unavailable"
    with pytest.raises(ValueError, match="digest-pinned"):
        await x.adapter.recover_from_snapshot(x.target, saved)
    x.patch.assert_not_called()


@pytest.mark.asyncio
async def test_real_sdk_encrypted_snapshot_to_digest_restore(storage, observed, monkeypatch):
    import json
    from urllib3 import HTTPResponse
    from releasetracker.models import ExecutorSnapshot
    from releasetracker.services.snapshot_integrity import verify_snapshot_integrity
    from test_kubernetes_snapshot_safety import create_executor

    x = observed
    with kube.ApiClient(kube.Configuration(host="https://cluster.example.test")) as api:
        state = {"workload": api.sanitize_for_serialization(x.obj)}
        responses = {
            "pods": api.sanitize_for_serialization(x.pod_list),
            "replicasets": api.sanitize_for_serialization(x.rs_list),
            "controllerrevisions": api.sanitize_for_serialization(x.revisions),
            "controllerrevisions/revision-new": api.sanitize_for_serialization(x.revision),
        }

        def request(method, url, **kwargs):
            assert url.startswith("https://cluster.example.test/")
            path = url.split("?", 1)[0]
            if method == "PATCH":
                assert kwargs["headers"]["Content-Type"] == "application/json-patch+json"
                operations = json.loads(kwargs["body"])
                assert operations[:2] == [
                    {"op": "test", "path": "/metadata/uid", "value": x.obj.metadata.uid},
                    {"op": "test", "path": "/metadata/resourceVersion", "value": "7"},
                ]
                state["workload"]["spec"] = deepcopy(operations[2]["value"])
                state["workload"]["metadata"].update(generation=4, resourceVersion="10")
                state["workload"]["status"]["observedGeneration"] = 4
                response = state["workload"]
            else:
                assert method == "GET"
                suffix = path.split("/namespaces/apps/", 1)[1]
                response = responses.get(suffix, state["workload"])
            return HTTPResponse(body=json.dumps(response).encode(), status=200)

        http = Mock(side_effect=request)
        monkeypatch.setattr(api.rest_client.pool_manager, "request", http)
        x.adapter._apps_api = kube.AppsV1Api(api)
        x.adapter._core_api = kube.CoreV1Api(api)
        saved = await x.adapter.capture_snapshot(x.target, TAG)
        assert saved["artifact_evidence"]["status"] == "verified", saved["artifact_evidence"]
        executor_id = await create_executor(storage, x.adapter, x.target)
        await storage.create_executor_snapshot(
            ExecutorSnapshot(
                executor_id=executor_id,
                snapshot_data=saved,
                trigger="pre_update",
                unredacted_persisted=True,
            )
        )
        persisted = await storage.get_executor_snapshot(executor_id)
        assert verify_snapshot_integrity(persisted) == "verified"
        assert persisted.snapshot_data == saved
        result = await x.adapter.recover_from_snapshot(x.target, persisted.snapshot_data)
        assert result.updated
        pod = state["workload"]["spec"]["template"]["spec"]
        assert pod["containers"][0]["image"] == PIN
        assert pod["initContainers"][0]["image"] == INIT_PIN
        assert sum(call.args[0] == "PATCH" for call in http.call_args_list) == 1
        assert sum("/pods" in call.args[1] for call in http.call_args_list) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["pod_version", "list_version", "ambiguous_controller", "stale_revision"]
)
async def test_revision_and_observation_identity_fail_closed(observed, fault):
    x = observed
    if fault == "pod_version":
        x.pods[0].metadata.resource_version = None
    elif fault == "list_version":
        x.pod_list.metadata.resource_version = None
    elif fault == "ambiguous_controller":
        if x.target["kind"] == "Deployment":
            other = deepcopy(x.rs)
            other.metadata.uid = "duplicate-controller"
            x.rs_list.items.append(other)
        elif x.target["kind"] == "DaemonSet":
            x.revisions.items.append(deepcopy(x.revision))
        else:
            x.revision.metadata.name = "wrong-revision"
    elif x.target["kind"] == "Deployment":
        old = deepcopy(x.rs)
        old.metadata.uid = "old-replica-uid"
        old.metadata.annotations["deployment.kubernetes.io/revision"] = "1"
        x.rs_list.items.append(old)
        x.pods[0].metadata.owner_references[0].uid = old.metadata.uid
    elif x.target["kind"] == "DaemonSet":
        x.revision.metadata.labels["controller-revision-hash"] = "conflicting-hash"
    else:
        x.obj.status.current_revision = "old-revision"
    saved = await x.adapter.capture_snapshot(x.target, TAG)
    assert saved["artifact_evidence"]["status"] == "unavailable"
    with pytest.raises(ValueError, match="digest-pinned"):
        await x.adapter.recover_from_snapshot(x.target, saved)
    x.patch.assert_not_called()


@pytest.mark.asyncio
async def test_unrelated_selector_overlap_does_not_contribute_evidence(observed):
    x = observed
    unrelated = deepcopy(x.pods[0])
    unrelated.metadata.uid = "foreign-pod"
    unrelated.metadata.owner_references[0].uid = "foreign-controller"
    unrelated.status.container_statuses[0].image_id = "unproven-foreign-image"
    x.pod_list.items.append(unrelated)
    saved = await x.adapter.capture_snapshot(x.target, TAG)
    assert saved["artifact_evidence"]["status"] == "verified", saved["artifact_evidence"]
    assert {p["uid"] for p in saved["artifact_evidence"]["pods"]} == {"pod-0", "pod-1"}
