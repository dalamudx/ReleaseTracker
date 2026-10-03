"""Configuration review compares real values, masks output and rejects stale approval."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from releasetracker.config import ExecutorConfig
from releasetracker.models import ExecutorSnapshot
from releasetracker.services.recovery_diff import (
    build_review,
    changed_lines,
    project,
    require_review,
)
from releasetracker.services.rollback_service import RollbackService
from releasetracker.services.snapshot_service import SnapshotService


def container(image="sha256:" + "1" * 64, password="old", identifier="one"):
    return {
        "runtime_type": "docker",
        "image": "nginx:old",
        "container_id": identifier,
        "container_name": "isolated-nginx",
        "create_config": {
            "name": "isolated-nginx",
            "image": "nginx:old",
            "environment": [f"PASSWORD={password}", "PORT=80"],
            "ports": {"80/tcp": 18080},
        },
        "recovery_evidence": {"image_id": image},
        "network_config": {
            "endpoints": {
                "bridge": {
                    "Aliases": [identifier, identifier[:12], "api"],
                    "IPAddress": "10.0.0.2",
                    "EndpointID": "transient",
                }
            }
        },
    }


def test_changed_only_secret_values_are_compared_before_redaction():
    old, new = container(), container(password="new-secret")
    lines, truncated = changed_lines(project(old)[0], project(new)[0])
    assert not truncated and len(lines) == 2
    assert [v["operation"] for v in lines] == ["-", "+"]
    assert all(v["redacted"] and v["value"] == "***REDACTED***" for v in lines)
    assert all(v["path"].endswith("/PASSWORD") for v in lines)
    assert "new-secret" not in str(lines)


def test_dynamic_inspect_fields_and_env_order_are_not_diff():
    old, new = container(), container(identifier="two")
    new["State"] = {"StartedAt": "later"}
    new["network_config"]["endpoints"]["bridge"]["IPAddress"] = "10.0.0.8"
    new["create_config"]["environment"].reverse()
    assert changed_lines(project(old)[0], project(new)[0]) == ([], False)


def test_git_signs_ports_and_immutable_image():
    old, new = container(), container(image="sha256:" + "2" * 64)
    new["create_config"]["ports"] = {"80/tcp": 28080}
    lines, _ = changed_lines(project(old)[0], project(new)[0])
    assert len(lines) == 4
    assert lines[0]["operation"] == "-"
    assert any(v["value"] == "28080" and v["operation"] == "+" for v in lines)


def test_compose_semantic_config_not_text_formatting():
    old = {
        "stack_file": "services:\n  web:\n    image: nginx:1\n    environment:\n      TOKEN: old-secret\n",
        "env": [],
    }
    new = {
        "stack_file": 'services: {web: {environment: {TOKEN: new-secret}, image: "nginx:2"}}',
        "env": [],
    }
    lines, _ = changed_lines(project(old)[0], project(new)[0])
    assert len(lines) == 4 and "old-secret" not in str(lines) and "new-secret" not in str(lines)
    assert any(v["path"] == "/compose/services/web/image" for v in lines)


def test_added_nested_secret_array_is_never_exposed():
    lines, _ = changed_lines({}, {"unknown": [{"password": "never-show"}]})
    assert lines[0]["redacted"] and "never-show" not in str(lines)


def test_kubernetes_diff_only_spec_and_restore_scope():
    spec = {
        "selector": {"matchLabels": {"app": "nginx"}},
        "template": {
            "metadata": {"resourceVersion": "10"},
            "spec": {"containers": [{"name": "web", "image": "nginx@sha256:" + "1" * 64}]},
        },
    }
    snapshot = {
        "mode": "kubernetes_workload",
        "kind": "Deployment",
        "containers": {"web": "nginx@sha256:" + "1" * 64},
        "recovery_scope": "workload_spec",
        "workload": {"kind": "Deployment", "metadata": {"generation": 1}, "api_spec": spec},
    }
    before = project(snapshot)[0]
    changed = deepcopy(snapshot)
    changed["workload"]["api_spec"]["replicas"] = 2
    changed["workload"]["api_spec"]["template"]["metadata"]["resourceVersion"] = "20"
    after, scope = project(changed, desired=True, live=snapshot)
    lines, _ = changed_lines(before, after)
    assert scope == "workload_spec" and lines == [
        {"operation": "+", "path": "/spec/replicas", "value": "2", "redacted": False}
    ]


@pytest.mark.parametrize("change", ["secret", "identity", "snapshot", "executor"])
async def test_review_fingerprint_rejects_all_unreviewed_changes(storage, change):
    executor = ExecutorConfig(
        id=1,
        name="isolated",
        runtime_type="docker",
        runtime_connection_id=1,
        tracker_name="test",
        target_ref={"mode": "container", "container_name": "isolated-nginx"},
    )
    current = container()
    adapter = SimpleNamespace(
        supports_single_image_operations=lambda ref: True,
        get_current_image=AsyncMock(return_value="nginx:current"),
        capture_snapshot=AsyncMock(side_effect=lambda *args: deepcopy(current)),
        is_target_missing_error=lambda exc: False,
    )
    snapshot = ExecutorSnapshot(id=7, executor_id=1, snapshot_data=container(password="saved"))
    review = await build_review(storage, executor, adapter, snapshot)
    await require_review(storage, executor, adapter, snapshot, review["review_fingerprint"])
    if change == "secret":
        current["create_config"]["environment"][0] = "PASSWORD=changed"
    if change == "identity":
        current["container_id"] = "replacement"
    if change == "snapshot":
        snapshot.snapshot_data["create_config"]["ports"]["80/tcp"] = 9999
    if change == "executor":
        executor.description = "changed"
    with pytest.raises(HTTPException) as exc:
        await require_review(storage, executor, adapter, snapshot, review["review_fingerprint"])
    assert exc.value.status_code == 409


async def test_missing_target_is_all_additions_not_false_no_diff(storage):
    executor = ExecutorConfig(
        id=1,
        name="isolated",
        runtime_type="docker",
        runtime_connection_id=1,
        tracker_name="test",
        target_ref={"mode": "container", "container_name": "isolated-nginx"},
    )
    adapter = SimpleNamespace(
        supports_single_image_operations=lambda ref: True,
        get_current_image=AsyncMock(side_effect=LookupError()),
        is_target_missing_error=lambda exc: isinstance(exc, LookupError),
    )
    snapshot = ExecutorSnapshot(id=7, executor_id=1, snapshot_data=container())
    review = await build_review(storage, executor, adapter, snapshot)
    assert review["current_missing"] and all(v["operation"] == "+" for v in review["lines"])
    adapter.is_target_missing_error = lambda exc: False
    with pytest.raises(LookupError):
        await build_review(storage, executor, adapter, snapshot)


async def test_truncated_diff_has_no_confirmation_fingerprint(storage):
    executor = ExecutorConfig(
        id=1,
        name="isolated",
        runtime_type="docker",
        runtime_connection_id=1,
        tracker_name="test",
        target_ref={"mode": "container", "container_name": "isolated-nginx"},
    )
    saved = container()
    saved["create_config"]["many"] = {str(i): i for i in range(1001)}
    adapter = SimpleNamespace(
        supports_single_image_operations=lambda ref: True,
        get_current_image=AsyncMock(return_value="nginx"),
        capture_snapshot=AsyncMock(return_value=container()),
        is_target_missing_error=lambda exc: False,
    )
    review = await build_review(
        storage, executor, adapter, ExecutorSnapshot(id=7, executor_id=1, snapshot_data=saved)
    )
    assert (
        review["truncated"]
        and len(review["lines"]) == 1000
        and review["review_fingerprint"] is None
    )


async def test_service_stale_review_rejects_before_claim_or_mutation(storage, monkeypatch):
    from test_rollback_service import _create_executor, _RollbackAdapter

    executor = await _create_executor(storage, name="diff-reject")
    snap_id = await storage.create_executor_snapshot(
        ExecutorSnapshot(executor_id=executor.id, snapshot_data=container())
    )
    adapter = _RollbackAdapter(await storage.get_runtime_connection(executor.runtime_connection_id))
    adapter.capture_snapshot = AsyncMock(return_value=container())
    service = RollbackService(storage, SnapshotService(storage))
    preview = await service.preview(
        executor_config=executor, adapter=adapter, snapshot_id=snap_id, include_diff=True
    )
    assert preview.snapshot_valid and preview.configuration_diff["review_fingerprint"]
    adapter.capture_snapshot.return_value = container(password="external-change")
    claim = AsyncMock(side_effect=AssertionError("must not claim"))
    monkeypatch.setattr(storage, "claim_executor_snapshot_for_rollback", claim)
    with pytest.raises(HTTPException) as exc:
        await service.rollback(
            executor_config=executor,
            adapter=adapter,
            snapshot_id=snap_id,
            actor="admin",
            review_fingerprint=preview.configuration_diff["review_fingerprint"],
        )
    assert exc.value.status_code == 409 and adapter.recover_calls == 0
    claim.assert_not_awaited()


def test_arrays_omit_unchanged_members():
    lines, truncated = changed_lines({"ports": [80, 443]}, {"ports": [80, 8443]})
    assert not truncated and len(lines) == 2
    assert [line["value"] for line in lines] == ["443", "8443"]
    assert all(line["path"] == "/ports/1" for line in lines)


@pytest.mark.parametrize(
    "saved,expected",
    [
        (["", 8080], ("", 8080)),
        (["127.0.0.1", 8080], ("127.0.0.1", 8080)),
        (["::1", 8080], ("::1", 8080)),
        ([["", 8080], ["127.0.0.1", 18080]], [("", 8080), ("127.0.0.1", 18080)]),
        ([8080, 18080], [8080, 18080]),
        (["8080", "18080"], ["8080", "18080"]),
    ],
)
def test_json_port_pairs_preserve_docker_sdk_semantics(saved, expected):
    from releasetracker.executors import DockerRuntimeAdapter
    from docker.utils import convert_port_bindings

    original = {"ports": {"80/tcp": saved}}
    config = DockerRuntimeAdapter._sanitize_docker_create_kwargs(original)
    assert config["ports"]["80/tcp"] == expected
    assert original["ports"]["80/tcp"] == saved
    native = convert_port_bindings(config["ports"])["80/tcp"]
    assert all(binding["HostPort"] in {"8080", "18080"} for binding in native)


def test_long_value_prevents_false_complete_review():
    lines, truncated = changed_lines({}, {"config": "x" * 5000})
    assert truncated and len(lines[0]["value"]) == 4097


def test_nested_command_and_uri_credentials_are_hidden():
    lines, _ = changed_lines(
        {},
        {
            "arbitrary": [{"command": ["secret-argument"]}],
            "uri": "https://user:secret@registry.invalid",
        },
    )
    assert all(line["redacted"] for line in lines)
    assert "secret-argument" not in str(lines) and "user:secret" not in str(lines)


async def test_service_change_during_pre_restore_capture_never_mutates(storage):
    from test_rollback_service import _create_executor, _RollbackAdapter

    executor = await _create_executor(storage, name="late-diff-reject")
    snap_id = await storage.create_executor_snapshot(
        ExecutorSnapshot(executor_id=executor.id, snapshot_data=container())
    )
    adapter = _RollbackAdapter(await storage.get_runtime_connection(executor.runtime_connection_id))
    adapter.capture_snapshot = AsyncMock(
        side_effect=[container(), container(), container(), container(password="late-change")]
    )
    service = RollbackService(storage, SnapshotService(storage))
    preview = await service.preview(
        executor_config=executor, adapter=adapter, snapshot_id=snap_id, include_diff=True
    )
    with pytest.raises(HTTPException) as exc:
        await service.rollback(
            executor_config=executor,
            adapter=adapter,
            snapshot_id=snap_id,
            actor="admin",
            review_fingerprint=preview.configuration_diff["review_fingerprint"],
        )
    assert exc.value.status_code == 409 and adapter.recover_calls == 0
    snapshot = await storage.get_executor_snapshot_by_id(executor.id, snap_id)
    assert snapshot is not None
    db = await storage._get_connection()
    row = await (
        await db.execute(
            "SELECT COUNT(*) FROM executor_snapshot_claims WHERE snapshot_id=?", (snap_id,)
        )
    ).fetchone()
    assert row[0] == 0
