import pytest

from releasetracker.storage.sqlite_tasks import TASK_CATEGORIES, TERMINAL_STATES
from test_task_queue import enqueue

pytestmark = pytest.mark.asyncio


async def test_categories_partition_states_and_paginate_on_server(storage, authed_client):
    seen = set()
    for category, states in TASK_CATEGORIES.items():
        assert not seen.intersection(states)
        seen.update(states)
        for state in states:
            task = await enqueue(
                storage,
                resource_key=f"tracker:{state}",
                dedupe_key=state,
                target_label=f"sample-{state}",
            )
            db = await storage._get_connection()
            await db.execute(
                "UPDATE tasks SET state=?,approval_pending=? WHERE id=?",
                (
                    "queued" if state == "awaiting_approval" else state,
                    int(state == "awaiting_approval"),
                    task["id"],
                ),
            )
            await db.commit()
    assert seen == TERMINAL_STATES | {"queued", "awaiting_approval", "running", "retry_wait"}
    for category, states in TASK_CATEGORIES.items():
        response = authed_client.get(
            "/api/tasks", params={"category": category, "include_total": True, "limit": 2}
        )
        assert response.status_code == 200, response.text
        assert int(response.headers["x-total-count"]) == len(states)
        rows = response.json()
        assert len(rows) == 2 and {row["state"] for row in rows} <= set(states)
        rest = await storage.tasks.list(category=category, offset=2)
        assert {row["state"] for row in rows + rest} == set(states)
        assert await storage.tasks.count(category=category, search="sample-") == len(states)
        assert await storage.tasks.count(category=category, search="absent") == 0
    # Existing exact-state queries and unfiltered notification requests stay compatible.
    assert len(authed_client.get("/api/tasks").json()) == len(seen)
    assert (await storage.tasks.list(state="awaiting_approval"))[0]["approval_pending"]
    assert authed_client.get("/api/tasks", params={"category": "unknown"}).status_code == 422
    await storage.tasks.clear_finished()
    assert await storage.tasks.count(category="completed") == 0
    assert await storage.tasks.count(category="failed") == 1
    assert (await storage.tasks.list(category="failed"))[0]["state"] == "needs_attention"
