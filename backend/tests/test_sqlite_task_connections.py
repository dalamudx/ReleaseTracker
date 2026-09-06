from __future__ import annotations

import asyncio
from datetime import datetime

import pytest


@pytest.mark.asyncio
async def test_concurrent_tasks_cannot_commit_each_others_transaction(storage):
    transaction_opened = asyncio.Event()
    release_transaction = asyncio.Event()

    async def rolled_back_transaction() -> None:
        connection = await storage._get_connection()
        await connection.execute("BEGIN IMMEDIATE")
        await connection.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("task-local-rollback", "must-not-persist", datetime.now().isoformat()),
        )
        transaction_opened.set()
        await release_transaction.wait()
        await connection.rollback()

    first_task = asyncio.create_task(rolled_back_transaction())
    await transaction_opened.wait()

    second_task = asyncio.create_task(storage.set_setting("concurrent-write", "must-persist"))
    await asyncio.sleep(0)
    release_transaction.set()
    await asyncio.gather(first_task, second_task)
    await asyncio.sleep(0)

    assert await storage.get_setting("task-local-rollback") is None
    assert await storage.get_setting("concurrent-write") == "must-persist"
    assert first_task not in storage._task_connections
    assert second_task not in storage._task_connections
