"""Unsaved executor preview is read-only, secret-safe and owns its native client."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from releasetracker.routers import executors as routes
from test_deployment_configuration_diff import setup


async def test_draft_preview_uses_unsaved_policy_without_persisting(storage, monkeypatch):
    executor, scheduler, _, adapter, current, _ = await setup(storage)
    try:
        adapter.close = AsyncMock()
        monkeypatch.setattr(routes, "_get_runtime_adapter", lambda connection: adapter)
        data = executor.model_dump(mode="json")
        data.update(
            name="unsaved-rename",
            image_selection_mode="use_tracker_image_and_tag",
            image_reference_mode="digest",
        )
        before_tasks = await storage.tasks.list()
        saved = await storage.get_executor_config(executor.id)
        response = await routes.preview_executor_configuration(
            routes.ExecutorConfigurationPreviewRequest(executor_id=executor.id, configuration=data),
            storage,
            scheduler,
        )
        assert response["mutation_performed"] is False and response["comparison_error"] is None
        lines = response["configuration_diff"]["lines"]
        assert len(lines) == 2 and lines[0]["operation"] == "-" and lines[1]["operation"] == "+"
        assert "docker.io/library/nginx@sha256:" in lines[1]["value"]
        assert "PASSWORD" not in str(response) and "old" not in str(response)
        assert await storage.tasks.list() == before_tasks
        assert (await storage.get_executor_config(executor.id)).model_dump() == saved.model_dump()
        adapter.close.assert_awaited_once()
    finally:
        await scheduler.shutdown()


async def test_draft_missing_release_is_not_reported_as_no_changes(storage, monkeypatch):
    from releasetracker.services import deployment_targets

    executor, scheduler, _, adapter, _, _ = await setup(storage)
    try:
        monkeypatch.setattr(
            deployment_targets,
            "resolve_deployment_targets",
            AsyncMock(return_value=[{"target": None}]),
        )
        adapter_factory = AsyncMock(side_effect=AssertionError("no adapter without version"))
        monkeypatch.setattr(routes, "_get_runtime_adapter", adapter_factory)
        response = await routes.preview_executor_configuration(
            routes.ExecutorConfigurationPreviewRequest(
                executor_id=executor.id, configuration=executor.model_dump(mode="json")
            ),
            storage,
            scheduler,
        )
        assert (
            response["comparison_error"] == "no_deployable_version"
            and response["configuration_diff"] is None
        )
        adapter_factory.assert_not_called()
    finally:
        await scheduler.shutdown()


@pytest.mark.parametrize("cancel", [False, True])
async def test_preview_failure_and_cancellation_close_client(storage, monkeypatch, cancel):
    executor, scheduler, _, adapter, _, _ = await setup(storage)
    try:
        adapter.close = AsyncMock()
        entered = asyncio.Event()

        async def capture(*args):
            entered.set()
            if cancel:
                await asyncio.Event().wait()
            raise RuntimeError("uri=https://user:secret@private.invalid PASSWORD=secret")

        adapter.capture_snapshot = capture
        monkeypatch.setattr(routes, "_get_runtime_adapter", lambda connection: adapter)
        call = asyncio.create_task(
            routes.preview_executor_configuration(
                routes.ExecutorConfigurationPreviewRequest(
                    executor_id=executor.id, configuration=executor.model_dump(mode="json")
                ),
                storage,
                scheduler,
            )
        )
        if cancel:
            await entered.wait()
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
        else:
            response = await call
            assert response[
                "comparison_error"
            ] == "runtime_inspection_failed" and "secret" not in str(response)
        adapter.close.assert_awaited_once()
    finally:
        await scheduler.shutdown()


async def test_preview_rejects_invalid_source_binding_before_inspect(storage, monkeypatch):
    executor, scheduler, _, adapter, _, _ = await setup(storage)
    try:
        draft = executor.model_dump(mode="json")
        draft["tracker_source_id"] = 999999
        factory = AsyncMock()
        monkeypatch.setattr(routes, "_get_runtime_adapter", factory)
        with pytest.raises(HTTPException) as exc:
            await routes.preview_executor_configuration(
                routes.ExecutorConfigurationPreviewRequest(
                    executor_id=executor.id, configuration=draft
                ),
                storage,
                scheduler,
            )
        assert exc.value.status_code == 400
        factory.assert_not_called()
    finally:
        await scheduler.shutdown()


async def setup_ssh_preview(storage, monkeypatch, *, saved=False):
    from releasetracker import executor_scheduler_ssh as ssh_scheduler
    from releasetracker.executor_scheduler import ExecutorScheduler
    from releasetracker.models import Release
    from releasetracker.services import ssh_compose_deploy, ssh_compose_ownership, ssh_transport
    from test_ssh_executor import setup as setup_ssh

    executor = await setup_ssh(storage)
    tracker = await storage.get_aggregate_tracker(executor.tracker_name)
    await storage.save_source_observations(
        tracker.id,
        tracker.sources[0],
        [
            Release(
                tracker_name=tracker.name,
                tracker_type="container",
                name="2.0.0",
                tag_name="2.0.0",
                version="2.0.0",
                published_at=datetime.now(timezone.utc),
                url="https://fixture.invalid",
                commit_sha="sha256:" + "a" * 64,
            )
        ],
    )
    proof = {"runtime_key": "isolated-engine", "project": "app", "working_dir": "/app"}
    if saved:
        executor._ssh_ownership = proof
        executor.id = await storage.create_executor_config(executor)
    scheduler = ExecutorScheduler(storage)
    rendered = {
        "services": {
            "web": {"image": "app:1.0.0", "environment": {"PASSWORD": "private-preview-value"}},
            "worker": {"image": "app:1.0.0"},
        }
    }

    @asynccontextmanager
    async def sftp():
        yield object()

    session = SimpleNamespace(sftp=sftp)

    @asynccontextmanager
    async def opened(*args):
        yield session

    async def plan(session_arg, sftp_arg, target, targets):
        return (
            SimpleNamespace(
                files={"/app/compose.yml": "private-preview-value"},
                rendered=rendered,
                targets=targets,
            ),
            None,
        )

    identify = AsyncMock(return_value=proof)
    read = AsyncMock(return_value=({}, {}, rendered, {}, None))
    make_plan = AsyncMock(side_effect=plan)
    monkeypatch.setattr(ssh_scheduler, "open_ssh_session", opened)
    monkeypatch.setattr(ssh_transport, "open_ssh_session", opened)
    monkeypatch.setattr(ssh_scheduler, "identify", identify)
    monkeypatch.setattr(ssh_compose_ownership, "identify", identify)
    monkeypatch.setattr(ssh_scheduler, "read_state", read)
    monkeypatch.setattr(ssh_compose_deploy, "make_plan", make_plan)
    return executor, scheduler, identify, read, make_plan


@pytest.mark.parametrize("saved", [False, True])
async def test_ssh_draft_previews_without_enrollment_or_saved_bindings(storage, monkeypatch, saved):
    executor, scheduler, identify, _, _ = await setup_ssh_preview(storage, monkeypatch, saved=saved)
    try:
        data = executor.model_dump(mode="json")
        service = "worker" if saved else "web"
        data["service_bindings"][0]["service"] = service
        before_configs = [c.model_dump() for c in await storage.get_all_executor_configs()]
        before_tasks = await storage.tasks.list()
        db = await storage._get_connection()
        before_owners = await (await db.execute("SELECT * FROM ssh_compose_ownership")).fetchall()
        response = await routes.preview_executor_configuration(
            routes.ExecutorConfigurationPreviewRequest(executor_id=executor.id, configuration=data),
            storage,
            scheduler,
        )
        assert response["mutation_performed"] is False
        assert response["comparison_error"] is None
        diff = response["configuration_diff"]
        assert diff["scope"] == "ssh_files"
        assert [(line["operation"], line["path"], line["value"]) for line in diff["lines"]] == [
            ("-", f"/compose/services/{service}/image", '"app:1.0.0"'),
            ("+", f"/compose/services/{service}/image", '"app:2.0.0"'),
        ]
        assert "private-preview-value" not in str(response)
        identify.assert_awaited_once()
        assert [c.model_dump() for c in await storage.get_all_executor_configs()] == before_configs
        assert await storage.tasks.list() == before_tasks
        assert (
            await (await db.execute("SELECT * FROM ssh_compose_ownership")).fetchall()
            == before_owners
        )
    finally:
        await scheduler.shutdown()


async def test_ssh_draft_still_requires_live_runtime_identity(storage, monkeypatch):
    from releasetracker.services.ssh_transport import SSHOperationError

    executor, scheduler, identify, read, plan = await setup_ssh_preview(storage, monkeypatch)
    identify.side_effect = SSHOperationError("identity", "runtime_identity_unavailable")
    try:
        response = await routes.preview_executor_configuration(
            routes.ExecutorConfigurationPreviewRequest(
                configuration=executor.model_dump(mode="json")
            ),
            storage,
            scheduler,
        )
        assert response["comparison_error"] == "runtime_inspection_failed"
        assert response["configuration_diff"] is None
        assert response["mutation_performed"] is False
        read.assert_not_awaited()
        plan.assert_not_awaited()
    finally:
        await scheduler.shutdown()


@pytest.mark.parametrize("saved", [False, True])
async def test_ssh_execution_still_requires_ownership_and_saved_configuration(
    storage, monkeypatch, saved
):
    from releasetracker.storage.sqlite_compose_ownership import ComposeOwnershipConflict

    executor, scheduler, _, read, plan = await setup_ssh_preview(storage, monkeypatch, saved=saved)
    if saved:
        executor.service_bindings[0].service = "worker"
    try:
        reason = "configuration changed" if saved else "ownership requires verification"
        with pytest.raises(ComposeOwnershipConflict, match=reason):
            await scheduler._resolve_ssh_update(executor)
        read.assert_not_awaited()
        plan.assert_not_awaited()
    finally:
        await scheduler.shutdown()
