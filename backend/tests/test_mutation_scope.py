import pytest

pytestmark = pytest.mark.asyncio


async def enqueue(storage, executor, scope, kind="deploy"):
    return await storage.tasks.enqueue(
        kind=kind,
        resource_key=scope,
        dedupe_key=f"{kind}:{executor}",
        payload={"executor_id": executor},
        target_label="test",
        trigger_mode="manual",
    )


async def block(storage, executor, scope):
    await enqueue(storage, executor, scope)
    task = await storage.tasks.claim("deploy")
    assert task
    assert await storage.tasks.finish(task, "needs_attention")
    return task


async def test_known_unrelated_target_can_progress(storage):
    await block(storage, 1, "deployment-mutations:kubernetes:uid:a")
    await enqueue(storage, 2, "deployment-mutations:kubernetes:uid:a")
    other = await enqueue(storage, 3, "deployment-mutations:kubernetes:uid:b")
    claimed = await storage.tasks.claim("deploy")
    assert claimed["id"] == other["id"]
    assert await storage.tasks.bind_mutation_scope(claimed, claimed["resource_key"])
    # Changing scope after queueing cannot escape the existing fence.
    assert not await storage.tasks.bind_mutation_scope(
        claimed, "deployment-mutations:kubernetes:uid:a"
    )


@pytest.mark.parametrize(
    "blocked_scope,new_scope",
    [
        ("deployment-mutations", "deployment-mutations:kubernetes:uid:b"),
        ("deployment-mutations:kubernetes:uid:a", "deployment-mutations"),
    ],
)
async def test_unknown_or_legacy_domain_remains_conservative(storage, blocked_scope, new_scope):
    await block(storage, 1, blocked_scope)
    await enqueue(storage, 2, new_scope)
    assert await storage.tasks.claim("deploy") is None
    await enqueue(storage, 1, blocked_scope, "recover")
    assert await storage.tasks.claim("recover")


async def test_same_executor_cannot_escape_fence_and_writes_stay_serial(storage):
    await block(storage, 1, "deployment-mutations:kubernetes:uid:a")
    await enqueue(storage, 1, "deployment-mutations:kubernetes:uid:b")
    assert await storage.tasks.claim("deploy") is None
    await enqueue(storage, 2, "deployment-mutations:kubernetes:uid:b")
    assert await storage.tasks.claim("deploy")
    await enqueue(storage, 3, "deployment-mutations:kubernetes:uid:c", "recover")
    assert await storage.tasks.claim("recover") is None


async def test_parallel_claims_share_mutation_budget(storage):
    import asyncio

    for executor in range(1, 5):
        await enqueue(
            storage,
            executor,
            f"deployment-mutations:kubernetes:uid:{executor}",
            "recover" if executor == 4 else "deploy",
        )
    claims = await asyncio.gather(
        *(storage.tasks.claim("deploy", mutation_capacity=2) for _ in range(4))
    )
    running = [task for task in claims if task]
    assert len(running) == 2
    assert await storage.tasks.claim("recover", mutation_capacity=2) is None
    assert await storage.tasks.finish(running[0], "succeeded")
    assert await storage.tasks.claim("recover", mutation_capacity=2)


async def test_parallel_scope_revalidation_blocks_target_change(storage):
    await enqueue(storage, 1, "deployment-mutations:kubernetes:uid:a")
    await enqueue(storage, 2, "deployment-mutations:kubernetes:uid:b", "recover")
    first = await storage.tasks.claim("deploy", mutation_capacity=2)
    second = await storage.tasks.claim("recover", mutation_capacity=2)
    assert await storage.tasks.bind_mutation_scope(first, first["resource_key"])
    assert not await storage.tasks.bind_mutation_scope(second, first["resource_key"])
    assert not await storage.tasks.bind_mutation_scope(first, "deployment-mutations")


@pytest.mark.parametrize("scope", ["deployment-mutations", "deployment-mutations:kubernetes:uid:a"])
async def test_parallel_keeps_global_and_same_target_fences(storage, scope):
    await enqueue(storage, 1, scope)
    assert await storage.tasks.claim("deploy", mutation_capacity=3)
    await enqueue(storage, 2, "deployment-mutations:kubernetes:uid:a", "recover")
    assert await storage.tasks.claim("recover", mutation_capacity=3) is None


async def test_parallel_same_executor_cannot_change_domains(storage):
    await enqueue(storage, 1, "deployment-mutations:kubernetes:uid:a")
    assert await storage.tasks.claim("deploy", mutation_capacity=2)
    await enqueue(storage, 1, "deployment-mutations:kubernetes:uid:b", "recover")
    assert await storage.tasks.claim("recover", mutation_capacity=2) is None


async def test_dispatch_really_runs_independent_mutations_in_parallel(storage, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from releasetracker.services.task_queue import TaskQueue, TaskResult

    monkeypatch.setenv("RELEASETRACKER_MUTATION_CONCURRENCY", "2")
    started = set()
    both = asyncio.Event()
    release = asyncio.Event()

    async def execute(task):
        started.add(task["id"])
        if len(started) == 2:
            both.set()
        await release.wait()
        return TaskResult()

    handler = MagicMock(prepare=AsyncMock(return_value=None), execute=execute)
    queue = TaskQueue(storage.tasks, MagicMock())
    queue.register("deploy", handler)
    queue.register("recover", handler)
    await enqueue(storage, 1, "deployment-mutations:kubernetes:uid:a")
    await enqueue(storage, 2, "deployment-mutations:kubernetes:uid:b", "recover")
    await enqueue(storage, 3, "deployment-mutations:kubernetes:uid:c")
    try:
        await queue.tick()
        await asyncio.wait_for(both.wait(), timeout=2)
        assert len(queue.workers) == 2
        assert len(await storage.tasks.list(state="queued")) == 1
    finally:
        release.set()
        await queue.shutdown()


async def test_invalid_mutation_capacity_is_rejected(storage):
    for value in [0, 4]:
        with pytest.raises(ValueError):
            await storage.tasks.claim("deploy", mutation_capacity=value)


async def test_scope_update_is_lease_fenced(storage):
    await enqueue(storage, 1, "deployment-mutations:kubernetes:uid:a")
    task = await storage.tasks.claim("deploy", lease_seconds=-1)
    with pytest.raises(RuntimeError, match="lease lost"):
        await storage.tasks.bind_mutation_scope(task, "deployment-mutations:kubernetes:uid:b")
