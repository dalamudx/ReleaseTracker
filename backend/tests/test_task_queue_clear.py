import sqlite3

import pytest
from db_helpers import dbmate_migrations_dir, iter_dbmate_up_sql


async def enqueue(storage, state="queued", kind="fetch", key="event"):
    task = await storage.tasks.enqueue(
        kind=kind,
        resource_key=key,
        dedupe_key=key,
        target_label=key,
        payload={"tracker_id": 1},
        trigger_mode="webhook",
        trigger_key=key,
    )
    async with storage.tasks.transaction() as db:
        await db.execute("UPDATE tasks SET state=? WHERE id=?", (state, task["id"]))
    return task


@pytest.mark.asyncio
async def test_clear_retains_active_and_attention_and_all_audit_data(storage):
    states = [
        "queued",
        "running",
        "retry_wait",
        "needs_attention",
        "succeeded",
        "no_change",
        "skipped",
        "failed",
        "cancelled",
        "superseded",
    ]
    tasks = {}
    for state in states:
        tasks[state] = await enqueue(storage, state, key=state)
    assert await storage.tasks.clear_finished() == 6
    assert {t["state"] for t in await storage.tasks.list()} == set(states[:4])
    assert await storage.tasks.clear_finished() == 0
    for state, task in tasks.items():
        detail = await storage.tasks.detail(task["id"])
        assert detail["state"] == state
        assert len(detail["triggers"]) == 1
        assert bool(detail["cleared_at"]) == (state in states[4:])
    # Clearing must not remove webhook/automatic trigger deduplication.
    duplicate = await storage.tasks.enqueue(
        kind="fetch",
        resource_key="succeeded",
        dedupe_key="succeeded",
        target_label="succeeded",
        payload={"tracker_id": 1},
        trigger_mode="webhook",
        trigger_key="succeeded",
    )
    assert duplicate["id"] == tasks["succeeded"]["id"]
    assert await storage.tasks.list(state="succeeded") == []
    # A task completing after the clear stays visible.
    async with storage.tasks.transaction() as db:
        await db.execute("UPDATE tasks SET state='succeeded' WHERE id=?", (tasks["running"]["id"],))
    assert [t["id"] for t in await storage.tasks.list(state="succeeded")] == [
        tasks["running"]["id"]
    ]


@pytest.mark.asyncio
async def test_clear_api_preserves_attempts_and_protected_deployment(storage, authed_client):
    finished = await enqueue(storage, key="finished")
    claimed = await storage.tasks.claim("fetch")
    await storage.tasks.start_attempt(claimed)
    await storage.tasks.finish(claimed, "failed")
    attention = await enqueue(storage, "needs_attention", "deploy", "protected")
    response = authed_client.post("/api/tasks/clear")
    assert response.status_code == 200
    assert response.json() == {"cleared": 1}
    assert [t["id"] for t in authed_client.get("/api/tasks").json()] == [attention["id"]]
    detail = authed_client.get(f"/api/tasks/{finished['id']}").json()
    assert len(detail["attempt_history"]) == 1
    assert detail["state"] == "failed"
    assert authed_client.post("/api/tasks/clear").json() == {"cleared": 0}
    await enqueue(storage, kind="deploy", key="next")
    async with storage.tasks.transaction() as db:
        await db.execute("UPDATE tasks SET resource_key='protected' WHERE dedupe_key='next'")
    assert await storage.tasks.claim("deploy") is None


def test_clear_api_requires_authentication(client):
    assert client.post("/api/tasks/clear").status_code == 401


def test_clear_migration_preserves_old_tasks_and_can_rollback(tmp_path):
    db = sqlite3.connect(tmp_path / "queue.db")
    try:
        migrations = iter_dbmate_up_sql(dbmate_migrations_dir())
        migration = next(
            (p, sql) for p, sql in migrations if p.name == "20260919000002_task_queue_clear.sql"
        )
        for path, sql in migrations:
            if path == migration[0]:
                break
            db.executescript(sql)
        db.execute(
            "INSERT INTO tasks(kind,resource_key,dedupe_key,target_label,payload,state,max_retries,due_at,created_at,updated_at) VALUES ('fetch','r','d','app','{}','succeeded',3,1,1,1)"
        )
        db.executescript(migration[1])
        assert db.execute("SELECT state,cleared_at FROM tasks").fetchone() == ("succeeded", None)
        db.executescript(migration[0].read_text().split("-- migrate:down", 1)[1])
        assert db.execute("SELECT state FROM tasks").fetchone() == ("succeeded",)
    finally:
        db.close()
