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


async def test_scope_update_is_lease_fenced(storage):
    await enqueue(storage, 1, "deployment-mutations:kubernetes:uid:a")
    task = await storage.tasks.claim("deploy", lease_seconds=-1)
    with pytest.raises(RuntimeError, match="lease lost"):
        await storage.tasks.bind_mutation_scope(task, "deployment-mutations:kubernetes:uid:b")
