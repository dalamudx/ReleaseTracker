"""Full workload spec recovery stays atomic and excludes external resources/data."""

from copy import deepcopy
import json
from unittest.mock import Mock

import pytest
from kubernetes import client as kube
from urllib3 import HTTPResponse

from releasetracker.executors import kubernetes_recovery
from releasetracker.executors.base import RuntimeMutationError
from test_kubernetes_snapshot_safety import IMAGE, SECRET, workload as workload
from test_kubernetes_recovery_rollout import ready_model


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(kubernetes_recovery, "VERIFY_INTERVAL", 0)
    monkeypatch.setattr(kubernetes_recovery, "VERIFY_TIMEOUT", 0.1)


def configure(obj):
    pod = obj.spec.template.spec
    pod.init_containers = [
        kube.V1Container(name="prepare", image=IMAGE, command=["sh", "-c", "true"])
    ]
    pod.service_account_name = "example-account"
    pod.containers[0].ports = [kube.V1ContainerPort(container_port=8080)]
    pod.containers[0].resources = kube.V1ResourceRequirements(limits={"memory": "64Mi"})
    pod.containers[0].readiness_probe = kube.V1Probe(
        http_get=kube.V1HTTPGetAction(path="/ready", port=8080)
    )
    pod.volumes = [
        kube.V1Volume(
            name="data",
            persistent_volume_claim=kube.V1PersistentVolumeClaimVolumeSource(
                claim_name="example-data"
            ),
        )
    ]


@pytest.mark.asyncio
async def test_restore_full_spec_removes_drift_and_restores_init_containers(workload):
    adapter, target, obj, reader, patch = workload
    configure(obj)
    saved = await adapter.capture_snapshot(target, IMAGE)
    ready = ready_model(obj, target["kind"])
    # Both addition and deletion must be reversed, not strategic-merged by name.
    obj.spec.template.spec.containers = [kube.V1Container(name="new-sidecar", image=IMAGE)]
    obj.spec.template.spec.init_containers = []
    obj.spec.template.spec.volumes = []
    obj.spec.template.spec.service_account_name = "changed-account"
    obj.metadata.resource_version = "9"
    patch.return_value = ready
    reader.side_effect = lambda *args, **kwargs: ready if kwargs else obj
    result = await adapter.recover_from_snapshot(target, saved)
    assert result.updated
    assert "spec restored" in result.message
    patch.assert_called_once()
    body = patch.call_args.args[2]
    assert body[:2] == [
        {"op": "test", "path": "/metadata/uid", "value": obj.metadata.uid},
        {"op": "test", "path": "/metadata/resourceVersion", "value": "9"},
    ]
    assert body[2] == {"op": "replace", "path": "/spec", "value": saved["workload"]["api_spec"]}
    spec = body[2]["value"]
    pod = spec["template"]["spec"]
    assert pod["serviceAccountName"] == "example-account"
    assert pod["initContainers"][0]["image"] == IMAGE
    assert [c["name"] for c in pod["containers"]] == ["service-a"]
    assert pod["containers"][0]["env"][0]["value"] == SECRET
    assert pod["containers"][0]["ports"][0]["containerPort"] == 8080
    assert pod["containers"][0]["readinessProbe"]["httpGet"]["path"] == "/ready"
    assert "creationTimestamp" not in spec["template"]["metadata"]
    assert saved["recovery_scope"] == "workload_spec"
    assert "referenced_resources" in saved["recovery_exclusions"]
    assert "volume_data" in saved["recovery_exclusions"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["submission", "observation"])
async def test_admission_or_external_configuration_change_never_reports_success(workload, phase):
    adapter, target, obj, reader, patch = workload
    saved = await adapter.capture_snapshot(target, IMAGE)
    ready = ready_model(obj, target["kind"])
    changed = deepcopy(ready)
    changed.spec.template.spec.containers[0].env[0].value = "changed-fixture-value"
    patch.return_value = changed if phase == "submission" else ready
    reader.side_effect = lambda *args, **kwargs: changed if kwargs else obj
    with pytest.raises(RuntimeMutationError, match="configuration") as error:
        await adapter.recover_from_snapshot(target, saved)
    assert error.value.destructive_started
    patch.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "selector",
        "init_tag",
        "duplicate_name",
        "missing_spec",
        "image_index",
        "unknown_scope",
        "paused_saved",
    ],
)
async def test_full_spec_preflight_rejects_incomplete_or_unsafe_snapshot(workload, fault):
    adapter, target, obj, reader, patch = workload
    configure(obj)
    saved = await adapter.capture_snapshot(target, IMAGE)
    spec = saved["workload"]["api_spec"]
    if fault == "selector":
        obj.spec.selector.match_labels = {"app": "other"}
    elif fault == "init_tag":
        spec["template"]["spec"]["initContainers"][0][
            "image"
        ] = "registry.example.test/team/service-a:mutable"
    elif fault == "duplicate_name":
        spec["template"]["spec"]["initContainers"][0]["name"] = "service-a"
    elif fault == "missing_spec":
        del saved["workload"]["api_spec"]
    elif fault == "image_index":
        spec["template"]["spec"]["containers"][0]["image"] = IMAGE.replace("a" * 64, "b" * 64)
    elif fault == "unknown_scope":
        saved["recovery_scope"] = "future-unsupported"
    else:
        spec["paused"] = True
    with pytest.raises(ValueError):
        await adapter.recover_from_snapshot(target, saved)
    patch.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_snapshot_remains_explicit_image_only_recovery(workload):
    adapter, target, obj, reader, patch = workload
    saved = await adapter.capture_snapshot(target, IMAGE)
    saved.pop("recovery_scope")
    saved["workload"].pop("api_spec")
    ready = ready_model(obj, target["kind"])
    patch.return_value = ready
    reader.side_effect = lambda *args, **kwargs: ready if kwargs else obj
    result = await adapter.recover_from_snapshot(target, saved)
    assert "images restored" in result.message
    assert isinstance(patch.call_args.args[2], dict)


def test_real_sdk_sends_spec_replacement_as_json_patch_without_network(monkeypatch):
    configuration = kube.Configuration(host="https://cluster.example.test")
    with kube.ApiClient(configuration) as api:
        request = Mock(return_value=HTTPResponse(body=b"{}", status=200))
        monkeypatch.setattr(api.rest_client.pool_manager, "request", request)
        body = [
            {"op": "test", "path": "/metadata/uid", "value": "example-uid"},
            {"op": "replace", "path": "/spec", "value": {"replicas": 1}},
        ]
        kube.AppsV1Api(api).patch_namespaced_deployment(
            "service-a", "apps", body, _preload_content=False
        )
        assert request.call_args.kwargs["headers"]["Content-Type"] == "application/json-patch+json"
        assert json.loads(request.call_args.kwargs["body"]) == body


@pytest.mark.asyncio
async def test_real_sdk_roundtrip_preserves_fields_unknown_to_its_models(workload, monkeypatch):
    adapter, target, obj, _, _ = workload
    configure(obj)
    with kube.ApiClient(kube.Configuration(host="https://cluster.example.test")) as api:
        wire = api.sanitize_for_serialization(obj)
        wire["spec"]["template"]["spec"]["futureServerField"] = {"opaque": ["preserve-me"]}
        state = {"wire": wire}
        expected_status = api.sanitize_for_serialization(ready_model(obj, target["kind"]))["status"]

        def request(method, url, **kwargs):
            assert url.startswith("https://cluster.example.test/")
            if method == "PATCH":
                assert kwargs["headers"]["Content-Type"] == "application/json-patch+json"
                ops = json.loads(kwargs["body"])
                assert ops[0]["value"] == state["wire"]["metadata"]["uid"]
                assert ops[1]["value"] == state["wire"]["metadata"]["resourceVersion"]
                assert ops[2]["op"] == "replace"
                state["wire"]["spec"] = deepcopy(ops[2]["value"])
                state["wire"]["metadata"].update(generation=3, resourceVersion="10")
                state["wire"]["status"] = expected_status
            else:
                assert method == "GET"
            return HTTPResponse(body=json.dumps(state["wire"]).encode(), status=200)

        upstream = Mock(side_effect=request)
        monkeypatch.setattr(api.rest_client.pool_manager, "request", upstream)
        adapter._apps_api = kube.AppsV1Api(api)
        saved = await adapter.capture_snapshot(target, IMAGE)
        unknown = saved["workload"]["api_spec"]["template"]["spec"]["futureServerField"]
        assert unknown == {"opaque": ["preserve-me"]}
        # Confirm the normal model projection really did lose this field.
        assert "futureServerField" not in saved["workload"]["spec"]["template"]["spec"]
        state["wire"]["spec"]["template"]["spec"]["futureServerField"] = {"drift": True}
        state["wire"]["spec"]["template"]["spec"]["serviceAccountName"] = "changed-account"
        state["wire"]["metadata"]["resourceVersion"] = "9"
        result = await adapter.recover_from_snapshot(target, saved)
        assert result.updated
        assert state["wire"]["spec"] == saved["workload"]["api_spec"]
        assert sum(call.args[0] == "PATCH" for call in upstream.call_args_list) == 1
