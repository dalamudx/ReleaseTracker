"""Unsaved executor preview is read-only, secret-safe and owns its native client."""

import asyncio
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
