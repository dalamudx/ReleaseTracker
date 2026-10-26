"""Helm proofs distinguish approved user values from generated chart defaults."""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasetracker.services import deployment_diff, helm_configuration, recovery_diff

MANIFEST = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: isolated-nginx
  namespace: proof-test
  resourceVersion: '1'
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: nginx
          image: nginx:1.26
status:
  readyReplicas: 1
"""


def snapshot():
    return {
        "mode": "helm_release",
        "namespace": "proof-test",
        "release_name": "isolated-nginx",
        "chart_name": "nginx",
        "chart_version": "1.0.0",
        "revision": 1,
        "helm_configuration": {
            "schema": 1,
            "values": {"innocent": "secret-value", "secret-key-in-values": {"a": 1}},
            "manifest": MANIFEST,
        },
    }


@pytest.fixture
def key_storage():
    return SimpleNamespace(system_key_manager=SimpleNamespace(jwt_secret="isolated-proof-key"))


async def review(monkeypatch, key_storage, current, target="1.1.0"):
    monkeypatch.setattr(deployment_diff, "image_targets", AsyncMock(return_value={}))
    adapter = SimpleNamespace(
        capture_snapshot=AsyncMock(side_effect=lambda *args: deepcopy(current)),
        get_helm_release_digest=AsyncMock(return_value=None),
    )
    executor = SimpleNamespace(target_ref={"mode": "helm_release"})
    task = {"payload": {"targets": [{"chart_version": target}]}}
    hashes = {}
    token = deployment_diff.INSPECTED_UPDATE_STATE.set(None)
    try:
        state_hash, diff = await deployment_diff.native_review(
            key_storage, None, executor, adapter, task, configuration_hashes=hashes
        )
        state = deployment_diff.INSPECTED_UPDATE_STATE.get()
    finally:
        deployment_diff.INSPECTED_UPDATE_STATE.reset(token)
    return state_hash, diff, hashes, state, adapter, executor


def test_manifest_canonicalization_ignores_only_runtime_fields():
    original = helm_configuration.declared_manifest(MANIFEST)
    changed_status = MANIFEST.replace("resourceVersion: '1'", "resourceVersion: '9'").replace(
        "readyReplicas: 1", "readyReplicas: 0"
    )
    assert helm_configuration.declared_manifest(changed_status) == original
    assert (
        helm_configuration.declared_manifest(MANIFEST.replace("replicas: 1", "replicas: 2"))
        != original
    )
    assert (
        helm_configuration.declared_manifest(MANIFEST.replace("nginx:1.26", "nginx:1.27"))
        != original
    )


def test_manifest_document_order_is_stable():
    configmap = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: config}\ndata: {key: value}\n"
    assert helm_configuration.declared_manifest(MANIFEST + "---\n" + configmap) == (
        helm_configuration.declared_manifest(configmap + "---\n" + MANIFEST)
    )


@pytest.mark.parametrize("manifest", ["", "[]", "metadata: {}", MANIFEST + "---\n" + MANIFEST])
def test_missing_or_ambiguous_manifest_fails_closed(manifest):
    current = snapshot()
    current["helm_configuration"]["manifest"] = manifest
    with pytest.raises(ValueError):
        helm_configuration.projection(current, required=True)


@pytest.mark.parametrize(
    "configuration",
    [None, {}, {"schema": True, "values": {}}, {"schema": 1, "values": None, "manifest": MANIFEST}],
)
def test_missing_or_invalid_configuration_cannot_authorize_update(configuration):
    current = snapshot()
    current["helm_configuration"] = configuration
    with pytest.raises(ValueError):
        helm_configuration.projection(current, required=True)


def test_empty_values_are_valid_and_not_missing():
    current = snapshot()
    current["helm_configuration"]["values"] = {}
    assert (
        helm_configuration.projection(current, required=True)[0]["helm_configuration"]["values"]
        == {}
    )


def test_legacy_rollback_snapshot_keeps_revision_scope():
    current = snapshot()
    del current["helm_configuration"]
    desired, scope = recovery_diff.project(current, desired=True)
    assert scope == "helm_revision"
    assert desired == {"chart_name": "nginx", "chart_version": "1.0.0", "revision": 1}


async def test_chart_defaults_can_change_but_user_values_cannot(monkeypatch, key_storage):
    current = snapshot()
    before = await review(monkeypatch, key_storage, current)
    current["chart_version"] = "1.1.0"
    current["revision"] = 2
    current["helm_configuration"]["manifest"] = MANIFEST.replace("replicas: 1", "replicas: 2")
    after = await review(monkeypatch, key_storage, current)
    assert before[2]["desired"] == after[2]["current"]
    assert before[0] != after[0]  # Stored manifest remains part of drift/baseline evidence.
    current["helm_configuration"]["values"]["innocent"] = "external-change"
    drift = await review(monkeypatch, key_storage, current)
    assert before[2]["desired"] != drift[2]["current"]
    other_key = SimpleNamespace(system_key_manager=SimpleNamespace(jwt_secret="other-key"))
    assert drift[2]["current"] != (await review(monkeypatch, other_key, current))[2]["current"]


@pytest.mark.parametrize("field", ["values", "manifest"])
async def test_prewrite_guard_detects_values_or_manifest_changes(monkeypatch, key_storage, field):
    current = snapshot()
    before = await review(monkeypatch, key_storage, current)
    if field == "values":
        current["helm_configuration"][field]["innocent"] = "changed"
    else:
        current["helm_configuration"][field] = MANIFEST.replace("replicas: 1", "replicas: 2")
    token = deployment_diff.EXPECTED_UPDATE_STATE.set(before[3])
    try:
        with pytest.raises(ValueError, match="deployment_configuration_changed"):
            await deployment_diff.verify_update_state(before[4], before[5].target_ref)
    finally:
        deployment_diff.EXPECTED_UPDATE_STATE.reset(token)


async def test_deployment_requires_new_configuration_evidence(monkeypatch, key_storage):
    current = snapshot()
    del current["helm_configuration"]
    with pytest.raises(ValueError, match="Helm"):
        await review(monkeypatch, key_storage, current)


def test_diff_hides_values_manifest_and_secret_mapping_keys():
    before = helm_configuration.projection(snapshot())[0]
    after = deepcopy(before)
    after["helm_configuration"]["values"]["secret-key-in-values"] = "new-secret"
    next(iter(after["helm_configuration"]["manifest"].values()))["spec"][
        "secret-path"
    ] = "secret-value"
    lines, truncated = helm_configuration.changed_configuration(
        before, after, recovery_diff.changed_lines
    )
    public = json.dumps(lines)
    assert not truncated and len(lines) == 4
    for secret in ("secret-key-in-values", "secret-path", "secret-value", "new-secret"):
        assert secret not in public
    assert all(line["redacted"] for line in lines)


def test_values_lists_and_marker_like_keys_remain_exact(key_storage):
    original = helm_configuration.projection(snapshot())[0]
    original["helm_configuration"]["values"] = {
        "annotations": {"releasetracker.io/deployment-id": "user-value"},
        "ordered": [{"name": "first"}, {"name": "second"}],
    }
    changed = deepcopy(original)
    changed["helm_configuration"]["values"]["ordered"].reverse()
    assert deployment_diff.configuration_state(
        key_storage, original
    ) != deployment_diff.configuration_state(key_storage, changed)
    changed = deepcopy(original)
    changed["helm_configuration"]["values"]["annotations"][
        "releasetracker.io/deployment-id"
    ] = "changed"
    assert deployment_diff.configuration_state(
        key_storage, original
    ) != deployment_diff.configuration_state(key_storage, changed)


async def test_prewrite_guard_accepts_unchanged_helm_configuration(monkeypatch, key_storage):
    current = snapshot()
    before = await review(monkeypatch, key_storage, current)
    token = deployment_diff.EXPECTED_UPDATE_STATE.set(before[3])
    try:
        assert await deployment_diff.verify_update_state(before[4], before[5].target_ref) == current
    finally:
        deployment_diff.EXPECTED_UPDATE_STATE.reset(token)


async def test_native_review_proof_binds_manifest_and_masks_output(monkeypatch, key_storage):
    current = snapshot()
    before = await review(monkeypatch, key_storage, current)
    current["helm_configuration"]["manifest"] = MANIFEST.replace("replicas: 1", "replicas: 2")
    after = await review(monkeypatch, key_storage, current)
    assert before[0] != after[0]
    assert before[2] == after[2]  # Upgrade invariant is deliberately user values only.
    assert "secret-value" not in json.dumps(after[1])
    assert "secret-key-in-values" not in json.dumps(after[1])
