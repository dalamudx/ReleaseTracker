from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from helpers.executor_runtime import save_docker_tracker_config
from releasetracker.config import Channel, ExecutorConfig, ExecutorServiceBinding
from releasetracker.services import ssh_compose
from releasetracker import executor_scheduler_ssh as module
from test_ssh_connections import config, credential


async def setup(storage):
    cid = await credential(storage)
    connection = config(cid)
    rid = await storage.create_runtime_connection(connection)
    await save_docker_tracker_config(
        storage, name="ssh-app", image="app", channels=[Channel(name="stable", type="release")]
    )
    tracker = await storage.get_aggregate_tracker("ssh-app")
    sid = tracker.sources[0].id
    return ExecutorConfig(
        name="ssh-deploy",
        runtime_type="ssh",
        runtime_connection_id=rid,
        tracker_name="ssh-app",
        tracker_source_id=sid,
        channel_name="stable",
        update_mode="manual",
        image_reference_mode="tag",
        target_ref={
            "mode": "ssh_compose",
            "tool": "docker_compose",
            "project": "app",
            "working_dir": "/app",
            "config_files": ["compose.yml"],
            "write_strategy": "source",
        },
        service_bindings=[
            ExecutorServiceBinding(service="web", tracker_source_id=sid, channel_name="stable")
        ],
    )


@pytest.mark.asyncio
async def test_ssh_executor_api_registration_and_admin_recovery_guard(
    authed_client, storage, monkeypatch
):
    from releasetracker.services import ssh_compose_ownership

    monkeypatch.setattr(ssh_compose_ownership, "prepare", AsyncMock())
    executor = await setup(storage)
    analyze = AsyncMock(return_value={"services": [{"service": "web", "safe_to_edit": True}]})
    monkeypatch.setattr(ssh_compose, "analyze_compose", analyze)
    response = authed_client.post("/api/executors", json=executor.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    eid = response.json()["id"]
    stored = await storage.get_executor_config(eid)
    assert stored.runtime_type == "ssh"
    assert stored.target_ref["mode"] == "ssh_compose"
    assert stored.service_bindings[0].service == "web"
    assert (
        authed_client.post(
            f"/api/executors/{eid}/ssh/recover",
            json={"snapshot_id": 1, "action": "restore_files", "remote_commands_stopped": False},
        ).status_code
        == 422
    )
    scheduler = authed_client.executor_scheduler
    assert await scheduler._try_acquire_executor_run(eid)
    try:
        assert (
            authed_client.post(
                f"/api/executors/{eid}/ssh/recover",
                json={"snapshot_id": 1, "action": "restore_files", "remote_commands_stopped": True},
            ).status_code
            == 409
        )
    finally:
        await scheduler._release_executor_run(eid)


@pytest.mark.asyncio
@pytest.mark.parametrize("project", [None, [], 42])
async def test_ssh_update_analysis_rejects_invalid_project(authed_client, project):
    response = authed_client.post(
        "/api/executors/ssh/compose/analyze", json={"runtime_connection_id": 1, "target": project}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_ssh_update_analysis_uses_override_aware_read_only_path(
    authed_client, storage, monkeypatch
):
    from releasetracker.routers import ssh_compose as router

    analyze = AsyncMock(
        return_value={"read_only": True, "services": [], "selected_tool": "docker_compose"}
    )
    monkeypatch.setattr(router, "analyze_update_project", analyze)
    cid = await credential(storage)
    rid = await storage.create_runtime_connection(config(cid))
    response = authed_client.post(
        "/api/executors/ssh/compose/analyze",
        json={
            "runtime_connection_id": rid,
            "target": {
                "working_dir": "/app",
                "project": "app",
                "config_files": ["compose.yml"],
                "tool": "docker_compose",
                "write_strategy": "override",
            },
        },
    )
    assert response.status_code == 200 and response.json()["read_only"]
    assert analyze.await_args.args[2].write_strategy == "override"


@pytest.mark.asyncio
async def test_executor_discovery_requires_saved_enabled_ssh_connection(
    authed_client, storage, monkeypatch
):
    from releasetracker.routers import ssh_compose as router

    discover = AsyncMock(return_value={"items": [], "read_only": True})
    monkeypatch.setattr(router, "discover_compose_projects", discover)
    url = "/api/executors/ssh/compose/discover"
    assert authed_client.post(url, json={"runtime_connection_id": 99999}).status_code == 404
    assert (
        authed_client.post(
            url, json={"runtime_connection_id": 1, "connection": {"host": "unconfigured"}}
        ).status_code
        == 422
    )
    cid = await credential(storage)
    connection = config(cid)
    connection.enabled = False
    rid = await storage.create_runtime_connection(connection)
    assert authed_client.post(url, json={"runtime_connection_id": rid}).status_code == 400
    discover.assert_not_awaited()
    connection.enabled = True
    rid = await storage.create_runtime_connection(connection.model_copy(update={"name": "enabled"}))
    assert authed_client.post(url, json={"runtime_connection_id": rid}).status_code == 200
    discover.assert_awaited_once()
    assert (
        authed_client.post("/api/runtime-connections/ssh/compose/analyze", json={}).status_code
        == 404
    )


@pytest.mark.asyncio
async def test_scheduler_resolves_source_then_uses_ssh_engine_and_normal_run_history(
    storage, monkeypatch
):
    from releasetracker.executor_scheduler import ExecutorScheduler

    executor = await setup(storage)
    executor.target_ref["discovery_id"] = "0123456789abcdefabcd"
    executor.id = await storage.save_executor_config(executor)
    scheduler = ExecutorScheduler(storage)
    verify_discovery = AsyncMock()
    monkeypatch.setattr(module, "verify_discovered_project", verify_discovery)

    @asynccontextmanager
    async def sftp():
        yield object()

    @asynccontextmanager
    async def opened(*args):
        yield SimpleNamespace(sftp=sftp)

    monkeypatch.setattr(module, "verify_session", AsyncMock())
    monkeypatch.setattr(module, "open_ssh_session", opened)
    monkeypatch.setattr(
        module,
        "read_state",
        AsyncMock(return_value=({}, {}, {"services": {"web": {"image": "app:1"}}}, {}, None)),
    )
    resolve = AsyncMock(return_value=("2", "sha256:" + "a" * 64))
    monkeypatch.setattr(scheduler, "_resolve_tracker_latest_target", resolve)
    run = AsyncMock(
        return_value={"status": "success", "snapshot_id": None, "plan": {"services": []}}
    )
    monkeypatch.setattr(module, "execute_update", run)
    try:
        result = await scheduler._execute_executor(executor, manual=True)
        assert result.status == "success"
        assert run.await_args.args[3] == {"web": "app:2"}
        assert verify_discovery.await_count == 1
        assert verify_discovery.await_args.args[2].discovery_id == "0123456789abcdefabcd"
        assert resolve.await_args.kwargs["tracker_source_id"] == executor.tracker_source_id
        latest_run = await storage.get_latest_executor_run(executor.id)
        assert latest_run.status == "success"
        assert latest_run.diagnostics["services"] == [
            {
                "service": "web",
                "status": "success",
                "from_version": None,
                "to_version": "app:2",
                "message": "updated",
            }
        ]
    finally:
        await scheduler.shutdown()
