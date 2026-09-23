"""Registry diagnostics use local responses, never real registries or credentials."""

import asyncio
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from releasetracker.services.registry_errors import RegistryTagListError
from releasetracker.services.task_queue import TaskQueue, classify_fetch_error
from releasetracker.trackers.docker import DockerTracker

pytestmark = pytest.mark.asyncio


def timeout_chain(wait_type=ssl.SSLWantReadError):
    errors = [
        ValueError("private wrapper"),
        httpx.ReadTimeout("private request URL"),
        TimeoutError(),
        asyncio.CancelledError(),
        wait_type(),
    ]
    for outer, inner in zip(errors, errors[1:]):
        outer.__context__ = inner
    return errors[0]


@pytest.mark.parametrize("wait_type", [ssl.SSLWantReadError, ssl.SSLWantWriteError])
async def test_tls_io_wait_does_not_hide_timeout(wait_type):
    result = classify_fetch_error(timeout_chain(wait_type))
    assert result.code == "upstream_timeout"
    assert result.retryable is True
    assert result.message is None


@pytest.mark.parametrize("error_type", [ssl.SSLCertVerificationError, ssl.SSLError])
async def test_real_tls_failure_still_wins_over_timeout(error_type):
    error = timeout_chain(error_type)
    result = classify_fetch_error(error)
    assert result.code == "security_validation_failed"
    assert result.retryable is False


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "registry_authentication_failed"),
        (403, "registry_access_denied"),
        (404, "registry_repository_not_found"),
    ],
)
@pytest.mark.parametrize("json_body", [True, False])
async def test_tracker_classifies_tag_list_failures_without_exposing_body(
    monkeypatch, status, code, json_body
):
    tracker = DockerTracker("sample", "team/service-a", registry="registry.example.test")
    body = (
        b'{"errors":[{"code":"NAME_UNKNOWN","message":"private-token"}]}'
        if json_body
        else b"private-token"
    )
    response = httpx.Response(
        status,
        content=body,
        request=httpx.Request("GET", "https://registry.example.test/v2/team/service-a/tags/list"),
    )
    monkeypatch.setattr(tracker, "_get_bearer_token", AsyncMock(return_value=None))
    monkeypatch.setattr(tracker, "_registry_request", AsyncMock(return_value=response))
    with pytest.raises(RegistryTagListError) as caught:
        await tracker.fetch_all()
    result = classify_fetch_error(caught.value)
    assert result.code == code
    assert result.retryable is False
    assert result.message is None
    assert "private-token" not in str(caught.value)
    assert "registry.example.test" not in str(caught.value)
    if status == 404:
        assert "build and push" in str(caught.value)


@pytest.mark.parametrize(
    "error,code,state",
    [
        (RegistryTagListError(404), "registry_repository_not_found", "failed"),
        (timeout_chain(), "upstream_timeout", "retry_wait"),
    ],
)
async def test_aggregate_preserves_error_and_blocks_partial_projection(
    storage, monkeypatch, error, code, state
):
    from test_task_queue_integration import sources, run_one

    tracker, scheduler, adapters, handler, queue = await sources(storage, monkeypatch)
    adapters["container"].fetch_all = AsyncMock(side_effect=error)
    await scheduler.check_tracker_now_v2(tracker.name)
    task = await run_one(storage, queue)
    assert task["state"] == state
    assert task["error_code"] == code
    assert task["result"]["source_failures"][0]["code"] == code
    assert task["attempts"] == 1
    assert adapters["container"].fallback_calls == 0
    assert await storage.get_tracker_current_releases(tracker.id) == []


async def test_generic_upstream_404_is_not_assumed_to_be_an_image():
    response = httpx.Response(
        404, request=httpx.Request("GET", "https://git.example.test/releases")
    )
    result = classify_fetch_error(
        httpx.HTTPStatusError("not found", request=response.request, response=response)
    )
    assert result.code == "upstream_http_404"
    assert not result.retryable


async def test_timeout_retries_then_missing_repository_stops_with_actionable_code(storage):
    queued = await storage.tasks.enqueue(
        kind="fetch",
        resource_key="tracker:1",
        dedupe_key="fetch:1",
        payload={"tracker_id": 1},
        target_label="sample",
        trigger_mode="manual",
        max_retries=3,
    )
    queue = TaskQueue(storage.tasks, MagicMock())
    handler = SimpleNamespace(
        prepare=AsyncMock(return_value=None),
        execute=AsyncMock(side_effect=[timeout_chain(), RegistryTagListError(404)]),
    )
    queue.register("fetch", handler)
    await queue._run(await storage.tasks.claim("fetch"))
    waiting = await storage.tasks.get(queued["id"])
    assert waiting["state"] == "retry_wait"
    assert waiting["error_code"] == "upstream_timeout"
    assert waiting["attempts"] == 1
    async with storage.tasks.transaction() as db:
        await db.execute("UPDATE tasks SET due_at=0 WHERE id=?", (queued["id"],))
    await queue._run(await storage.tasks.claim("fetch"))
    failed = await storage.tasks.detail(queued["id"])
    assert failed["state"] == "failed"
    assert failed["attempts"] == 2
    assert failed["error_code"] == "registry_repository_not_found"
    assert {item["error_code"] for item in failed["attempt_history"]} == {
        "upstream_timeout",
        "registry_repository_not_found",
    }
