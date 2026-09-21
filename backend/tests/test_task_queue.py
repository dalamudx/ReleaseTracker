import asyncio
import ssl
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from releasetracker.routers.settings import _normalize_setting_value
from releasetracker.services.task_queue import TaskQueue, TaskResult, classify_fetch_error
from releasetracker.storage.sqlite_tasks import FETCH_RETRY_SETTING

pytestmark = pytest.mark.asyncio


async def enqueue(storage, **kwargs):
    return await storage.tasks.enqueue(
        **(
            dict(
                kind="fetch",
                resource_key="tracker:1",
                dedupe_key="fetch:1",
                target_label="app",
                payload={"tracker_id": 1},
                trigger_mode="manual",
                now=100,
            )
            | kwargs
        )
    )


async def test_concurrent_enqueue_and_claim(storage):
    tasks = await asyncio.gather(*(enqueue(storage) for _ in range(8)))
    assert len({task["id"] for task in tasks}) == 1
    claims = await asyncio.gather(*(storage.tasks.claim("fetch", now=101) for _ in range(5)))
    assert sum(task is not None for task in claims) == 1
    detail = await storage.tasks.detail(tasks[0]["id"])
    assert len(detail["triggers"]) == 8


async def test_new_event_after_start_is_not_lost(storage):
    first = await enqueue(storage, trigger_key="delivery:1")
    running = await storage.tasks.claim("fetch", now=101)
    next_task = await enqueue(storage, trigger_mode="webhook", trigger_key="delivery:2")
    assert next_task["id"] != first["id"]
    assert await storage.tasks.claim("fetch", now=102) is None
    assert (await enqueue(storage, trigger_key="delivery:1"))["id"] == first["id"]
    assert await storage.tasks.finish(running, "succeeded", now=103)
    assert (await storage.tasks.claim("fetch", now=104))["id"] == next_task["id"]


async def test_manual_click_joins_running(storage):
    task = await enqueue(storage)
    await storage.tasks.claim("fetch", now=101)
    assert (await enqueue(storage, join_running=True))["id"] == task["id"]


async def test_retry_setting_snapshot_and_zero(storage):
    assert (await enqueue(storage))["max_retries"] == 3
    await storage.set_setting(FETCH_RETRY_SETTING, "0")
    assert (await enqueue(storage))["max_retries"] == 3
    assert (await enqueue(storage, dedupe_key="other"))["max_retries"] == 0


async def test_waiting_does_not_consume_attempt(storage):
    await enqueue(storage)
    task = await storage.tasks.claim("fetch", now=101)
    await storage.tasks.finish(task, "queued", code="source_cooldown", due_at=130, now=102)
    assert (await storage.tasks.get(task["id"]))["attempts"] == 0
    assert await storage.tasks.claim("fetch", now=129) is None
    assert await storage.tasks.claim("fetch", now=130)


async def test_lease_fencing_and_restart_budget(storage):
    await enqueue(storage, max_retries=0)
    task = await storage.tasks.claim("fetch", now=101)
    assert await storage.tasks.start_attempt(task, now=102)
    assert await storage.tasks.recover(now=162) == 1
    assert not await storage.tasks.finish(task, "succeeded", now=163)
    assert not await storage.tasks.heartbeat(task, now=163)
    detail = await storage.tasks.detail(task["id"])
    assert detail["state"] == "failed"
    assert detail["attempts"] == 1
    assert detail["attempt_history"][0]["state"] == "interrupted"


async def test_interrupted_deployment_blocks_target(storage):
    await enqueue(storage, kind="deploy", max_retries=0)
    task = await storage.tasks.claim("deploy", now=101)
    await storage.tasks.start_attempt(task, now=102)
    await storage.tasks.recover(startup=True, now=103)
    assert (await storage.tasks.get(task["id"]))["state"] == "needs_attention"
    await enqueue(storage, kind="deploy", dedupe_key="another")
    assert await storage.tasks.claim("deploy", now=200) is None
    assert not await storage.tasks.cancel(task["id"])


async def test_retry_budget_counts_initial_attempt(storage):
    queue = TaskQueue(storage.tasks, MagicMock())
    task = await enqueue(storage, max_retries=3)
    for attempt in range(1, 5):
        claimed = await storage.tasks.claim("fetch", now=10**12)
        assert await storage.tasks.start_attempt(claimed, now=10**12)
        # _finish's real clock is inside the synthetic lease.
        await queue._finish(claimed, TaskResult("failed", "upstream_timeout", retryable=True))
        task = await storage.tasks.get(task["id"])
        assert task["attempts"] == attempt
        assert task["state"] == ("retry_wait" if attempt < 4 else "failed")
    assert len((await storage.tasks.detail(task["id"]))["attempt_history"]) == 4


async def test_cannot_cancel_running(storage):
    task = await enqueue(storage)
    await storage.tasks.claim("fetch", now=101)
    assert not await storage.tasks.cancel(task["id"])
    queued = await enqueue(storage)
    assert await storage.tasks.cancel(queued["id"])
    assert (await storage.tasks.get(queued["id"]))["state"] == "cancelled"


async def test_dispatch_is_short_and_pools_independent(storage):
    release = asyncio.Event()
    started = asyncio.Event()
    handler = MagicMock()
    handler.prepare = AsyncMock(return_value=None)

    async def execute(task):
        started.set()
        await release.wait()
        return TaskResult()

    handler.execute = execute
    queue = TaskQueue(storage.tasks, MagicMock())
    queue.register("fetch", handler)
    queue.register("deploy", handler)
    await enqueue(storage)
    await enqueue(storage, kind="deploy", resource_key="executor:1", dedupe_key="deploy:1")
    await asyncio.wait_for(queue.tick(), timeout=1)
    await asyncio.wait_for(started.wait(), timeout=1)
    assert len(queue.workers) == 2
    release.set()
    await queue.shutdown()
    assert {task["state"] for task in await storage.tasks.list()} == {"succeeded"}


async def test_error_classification_preserves_security_and_retry_after():
    for status in (401, 403, 404, 422, 429, 503):
        response = httpx.Response(
            status,
            headers={"Retry-After": "1200"},
            request=httpx.Request("GET", "https://example.org"),
        )
        failure = classify_fetch_error(
            httpx.HTTPStatusError("secret-url", request=response.request, response=response)
        )
        assert failure.retryable == (status in (429, 503))
        assert failure.message is None
        if failure.retryable:
            assert failure.retry_after == 1200
    try:
        try:
            raise httpx.ReadTimeout("secret-url")
        except httpx.ReadTimeout as exc:
            raise ValueError("wrapper") from exc
    except ValueError as exc:
        assert classify_fetch_error(exc).retryable
    try:
        try:
            raise ssl.SSLError("bad certificate")
        except ssl.SSLError as exc:
            raise httpx.ConnectError("failed") from exc
    except httpx.ConnectError as exc:
        assert not classify_fetch_error(exc).retryable


async def test_setting_validation():
    from fastapi import HTTPException

    for value in ("-1", "11", "1.5", "true", "", "３"):
        with pytest.raises(HTTPException):
            _normalize_setting_value(FETCH_RETRY_SETTING, value)
    assert _normalize_setting_value(FETCH_RETRY_SETTING, "3") == "3"
