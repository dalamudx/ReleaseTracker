"""Planned configuration identity survives authorized recreation, not live drift."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.services.deployment_diff import _portainer_overrides, configuration_state
from releasetracker.services.deployment_plan import managed_markers
from releasetracker.services.recovery_diff import _normalize


def test_approval_binds_the_planned_configuration():
    from dataclasses import replace
    from test_deployment_admission import evidence
    from releasetracker.services.deployment_plan import plan_fingerprint

    task = {"id": 1, "payload": {"config_identity": "executor", "targets": []}}
    reviewed = evidence(desired_configuration_hash="reviewed")
    changed = replace(reviewed, desired_configuration_hash="different-planned-config")
    assert reviewed.evidence_hash == changed.evidence_hash
    assert plan_fingerprint(task, reviewed) != plan_fingerprint(task, changed)


@pytest.mark.parametrize("style", ["labels", "Labels", "annotations"])
def test_configuration_identity_ignores_only_our_markers(style):
    storage = SimpleNamespace(system_key_manager=SimpleNamespace(jwt_secret="fixture"))
    original = {style: {"owner": "team", **managed_markers("i", "t", 1)}}
    current = {style: {"owner": "team", **managed_markers("i", "t", 2)}}
    assert configuration_state(storage, original) == configuration_state(storage, current)
    current[style]["owner"] = "another-team"
    assert configuration_state(storage, original) != configuration_state(storage, current)
    assert configuration_state(storage, {}) == configuration_state(
        storage, {style: managed_markers("i", "t", 1)}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift", [None, "environment", "restart_policy", "mount", "command", "user_label"]
)
async def test_portainer_identity_separates_new_image_defaults_from_runtime_overrides(drift):
    async def projected(revision):
        identifier = str(revision) * 64
        image_id = "sha256:" + str(revision + 2) * 64
        image_config = {
            "Env": [f"IMAGE_VERSION={revision}", "PATH=/bin"],
            "Cmd": [f"entry-{revision}"],
        }
        adapter = SimpleNamespace(
            inspect_local_image=AsyncMock(return_value={"Id": image_id, "Config": image_config})
        )
        inspected = {"Id": identifier, "Image": image_id}
        config = {
            **deepcopy(image_config),
            "Image": "example/app:stable",
            "Hostname": identifier[:12],
            "Labels": {
                "com.docker.compose.service": "app",
                "com.docker.compose.config-hash": str(revision),
                **managed_markers("i", "t", revision),
            },
        }
        config["Env"].append("APP_MODE=production")
        native = {
            "service": "app",
            "config": _normalize(config),
            "host_config": {"RestartPolicy": {"Name": "always"}},
            "mounts": [{"Source": "/data", "Destination": "/app/data"}],
            "networks": {},
        }
        if revision == 2:
            if drift == "environment":
                native["config"]["Env"]["EXTRA"] = "unapproved"
            elif drift == "restart_policy":
                native["host_config"]["RestartPolicy"]["Name"] = "no"
            elif drift == "mount":
                native["mounts"][0]["Source"] = "/other"
            elif drift == "command":
                native["config"]["Cmd"] = ["unapproved"]
            elif drift == "user_label":
                native["config"]["Labels"]["owner"] = "external"
        result = await _portainer_overrides(
            adapter, {}, inspected, native, {"environment": {"APP_MODE": "production"}}
        )
        adapter.inspect_local_image.assert_awaited_once_with({}, image_id)
        return result

    before, after = await projected(1), await projected(2)
    assert (before == after) is (drift is None)


@pytest.mark.asyncio
async def test_same_tag_kubernetes_plan_includes_pull_policy_and_converges(storage, monkeypatch):
    from releasetracker.services import deployment_diff, deployment_version_evidence

    image = "example/app:stable"
    snapshot = {
        "mode": "kubernetes_workload",
        "recovery_scope": "workload_spec",
        "containers": {"app": image},
        "artifact_evidence": {"status": "verified"},
        "workload": {
            "kind": "Deployment",
            "metadata": {"uid": "workload"},
            "api_spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {"name": "app", "image": image, "imagePullPolicy": "IfNotPresent"}
                        ]
                    }
                }
            },
        },
    }
    adapter = SimpleNamespace(capture_snapshot=AsyncMock(side_effect=lambda *a: deepcopy(snapshot)))
    executor = SimpleNamespace(target_ref={"mode": "kubernetes_workload"})
    monkeypatch.setattr(deployment_diff, "image_targets", AsyncMock(return_value={"app": image}))
    proof = {"service": "app", "same_artifact": False}
    monkeypatch.setattr(
        deployment_version_evidence,
        "collect_version_evidence",
        AsyncMock(side_effect=lambda *a: (dict(proof),)),
    )
    before = {}
    _, diff = await deployment_diff.native_review(
        storage, None, executor, adapter, {}, version_evidence=[], configuration_hashes=before
    )
    assert any(
        row["path"].endswith("/imagePullPolicy") and row["value"] == '"Always"'
        for row in diff["lines"]
    )
    snapshot["workload"]["api_spec"]["template"]["spec"]["containers"][0][
        "imagePullPolicy"
    ] = "Always"
    snapshot["workload"]["api_spec"]["template"]["metadata"] = {
        "annotations": managed_markers("i", "t", 1)
    }
    proof["same_artifact"] = True
    after = {}
    await deployment_diff.native_review(
        storage, None, executor, adapter, {}, version_evidence=[], configuration_hashes=after
    )
    assert after["current"] == before["desired"]


@pytest.mark.asyncio
async def test_same_tag_kubernetes_write_matches_reviewed_pull_policy():
    from test_executor_adapters import FakeAppsApi, FakeContainerSpec, FakeWorkload
    from releasetracker.config import RuntimeConnectionConfig
    from releasetracker.executors.kubernetes import KubernetesRuntimeAdapter
    from releasetracker.services.deployment_version_evidence import FORCE_IMAGE_UPDATES
    from releasetracker.services.deployment_plan import MANAGED_MARKERS
    from releasetracker.services.deployment_diff import EXPECTED_UPDATE_STATE

    workload = FakeWorkload("app", [FakeContainerSpec("app", "app:stable")])
    api = FakeAppsApi([workload], [], [])
    adapter = KubernetesRuntimeAdapter(
        RuntimeConnectionConfig(
            name="test",
            type="kubernetes",
            config={"namespace": "apps", "in_cluster": True},
            secrets={},
        ),
        apps_api=api,
    )
    forced = FORCE_IMAGE_UPDATES.set(frozenset({"app"}))
    markers = MANAGED_MARKERS.set(managed_markers("i", "t", 42))
    expected = EXPECTED_UPDATE_STATE.set(None)
    try:
        await adapter.update_workload_services(
            {
                "mode": "kubernetes_workload",
                "namespace": "apps",
                "kind": "Deployment",
                "name": "app",
            },
            {"app": "app:stable"},
        )
    finally:
        FORCE_IMAGE_UPDATES.reset(forced)
        MANAGED_MARKERS.reset(markers)
        EXPECTED_UPDATE_STATE.reset(expected)
    body = api.patch_calls[0][3]
    template = body["spec"]["template"]
    assert template["spec"]["containers"] == [
        {"name": "app", "image": "app:stable", "imagePullPolicy": "Always"}
    ]
    assert template["metadata"]["annotations"]["releasetracker.io/deployment-id"] == "42"
