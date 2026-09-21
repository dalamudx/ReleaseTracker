"""Persistent single-instance task queue. Transactions never include remote I/O."""

from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

FETCH_RETRY_SETTING = "system.fetch_retry_count"
DEFAULT_FETCH_RETRIES = 3
TERMINAL_STATES = frozenset(
    {"succeeded", "no_change", "skipped", "failed", "cancelled", "superseded", "needs_attention"}
)


def decode(row):
    if row is None:
        return None
    data = dict(row)
    for key in ("payload", "result"):
        if key in data:
            data[key] = json.loads(data[key]) if data[key] else None
    return data


class TaskStore:
    def __init__(self, storage):
        self.storage = storage

    @asynccontextmanager
    async def transaction(self):
        db = await self.storage._get_connection()
        await db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            await db.commit()
        except BaseException:
            await db.rollback()
            raise

    async def retry_count(self):
        value = await self.storage.get_setting(FETCH_RETRY_SETTING)
        try:
            count = int(value) if value is not None else DEFAULT_FETCH_RETRIES
        except (TypeError, ValueError):
            return DEFAULT_FETCH_RETRIES
        return count if 0 <= count <= 10 else DEFAULT_FETCH_RETRIES

    async def enqueue(
        self,
        *,
        kind,
        resource_key,
        dedupe_key,
        target_label,
        payload,
        trigger_mode,
        trigger_key=None,
        due_at=None,
        max_retries=None,
        join_running=False,
        initial_attempts=0,
        now=None,
    ):
        now = time.time() if now is None else now
        retries = (
            await self.retry_count()
            if max_retries is None and kind == "fetch"
            else (max_retries or 0)
        )
        encoded = json.dumps(payload, sort_keys=True)
        async with self.transaction() as db:
            if trigger_key:
                row = await (
                    await db.execute(
                        "SELECT t.* FROM tasks t JOIN task_triggers g ON g.task_id=t.id WHERE g.trigger_key=?",
                        (trigger_key,),
                    )
                ).fetchone()
                if row:
                    return decode(row)
            states = "'queued','retry_wait','running'" if join_running else "'queued','retry_wait'"
            # Only identical immutable payloads may coalesce. Running event work never
            # absorbs a later event: that event may describe data not seen by its scan.
            row = await (
                await db.execute(
                    f"SELECT * FROM tasks WHERE dedupe_key=? AND payload=? AND state IN ({states}) ORDER BY id LIMIT 1",
                    (dedupe_key, encoded),
                )
            ).fetchone()
            if row:
                task_id = row["id"]
            else:
                cursor = await db.execute(
                    """INSERT INTO tasks(kind,resource_key,dedupe_key,target_label,payload,
                       max_retries,attempts,due_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        kind,
                        resource_key,
                        dedupe_key,
                        target_label,
                        encoded,
                        retries,
                        max(0, int(initial_attempts)),
                        now if due_at is None else due_at,
                        now,
                        now,
                    ),
                )
                task_id = cursor.lastrowid
            await db.execute(
                "INSERT INTO task_triggers(task_id,trigger_mode,trigger_key,created_at) VALUES (?,?,?,?)",
                (task_id, trigger_mode, trigger_key, now),
            )
            return decode(
                await (await db.execute("SELECT * FROM tasks WHERE id=?", (task_id,))).fetchone()
            )

    async def get(self, task_id):
        db = await self.storage._get_connection()
        return decode(
            await (await db.execute("SELECT * FROM tasks WHERE id=?", (task_id,))).fetchone()
        )

    async def clear_finished(self):
        """Dismiss settled tasks without deleting execution evidence or trigger keys."""
        async with self.transaction() as db:
            cursor = await db.execute(
                """UPDATE tasks SET cleared_at=?
                   WHERE cleared_at IS NULL AND state IN (
                       'succeeded','no_change','skipped','failed','cancelled','superseded'
                   )""",
                (time.time(),),
            )
            return cursor.rowcount

    async def list(self, *, limit=50, before=None, state=None):
        db = await self.storage._get_connection()
        rows = await (
            await db.execute(
                """SELECT * FROM tasks WHERE cleared_at IS NULL AND (? IS NULL OR id<?) AND (? IS NULL OR state=?)
               ORDER BY id DESC LIMIT ?""",
                (before, before, state, state, min(100, max(1, limit))),
            )
        ).fetchall()
        return [decode(row) for row in rows]

    async def detail(self, task_id):
        task = await self.get(task_id)
        if not task:
            return None
        db = await self.storage._get_connection()
        task["attempt_history"] = [
            decode(row)
            for row in await (
                await db.execute(
                    "SELECT * FROM task_attempts WHERE task_id=? ORDER BY attempt",
                    (task_id,),
                )
            ).fetchall()
        ]
        task["triggers"] = [
            dict(row)
            for row in await (
                await db.execute(
                    "SELECT trigger_mode,created_at FROM task_triggers WHERE task_id=? ORDER BY id",
                    (task_id,),
                )
            ).fetchall()
        ]
        return task

    async def claim(self, kind, *, lease_seconds=60, now=None):
        now = time.time() if now is None else now
        owner = uuid.uuid4().hex
        async with self.transaction() as db:
            row = await (
                await db.execute(
                    """SELECT t.* FROM tasks t WHERE kind=? AND state IN ('queued','retry_wait')
                   AND due_at<=? AND NOT EXISTS (
                     SELECT 1 FROM tasks active WHERE active.resource_key=t.resource_key
                     AND (active.state='running' OR (active.state='needs_attention' AND t.kind!='recover')))
                   ORDER BY due_at,id LIMIT 1""",
                    (kind, now),
                )
            ).fetchone()
            if not row:
                return None
            await db.execute(
                "UPDATE tasks SET state='running',owner=?,lease_until=?,updated_at=? WHERE id=?",
                (owner, now + lease_seconds, now, row["id"]),
            )
            return decode(
                await (await db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],))).fetchone()
            )

    async def start_attempt(self, task, *, now=None):
        """Called after cooldown/preconditions; waiting does not spend a retry."""
        now = time.time() if now is None else now
        async with self.transaction() as db:
            cursor = await db.execute(
                """UPDATE tasks SET attempts=attempts+1,updated_at=?
                   WHERE id=? AND owner=? AND state='running' AND lease_until>?
                   AND attempts<=max_retries
                   AND NOT EXISTS(SELECT 1 FROM task_attempts a WHERE a.task_id=tasks.id AND a.owner=tasks.owner)""",
                (now, task["id"], task["owner"], now),
            )
            if cursor.rowcount != 1:
                return False
            row = await (
                await db.execute("SELECT attempts FROM tasks WHERE id=?", (task["id"],))
            ).fetchone()
            task["attempts"] = row[0]
            await db.execute(
                "INSERT INTO task_attempts(task_id,attempt,owner,started_at,state) VALUES (?,?,?,?,'running')",
                (task["id"], row[0], task["owner"], now),
            )
            return True

    async def checkpoint(self, task, result, *, private_payload=None):
        payload = json.dumps(task["payload"] | private_payload) if private_payload else None
        async with self.transaction() as db:
            cursor = await db.execute(
                "UPDATE tasks SET result=?,payload=COALESCE(?,payload) WHERE id=? AND owner=? AND state='running' AND lease_until>?",
                (json.dumps(result), payload, task["id"], task["owner"], time.time()),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Task lease lost")
            return True

    async def heartbeat(self, task, *, lease_seconds=60, now=None):
        now = time.time() if now is None else now
        async with self.transaction() as db:
            cursor = await db.execute(
                """UPDATE tasks SET lease_until=? WHERE id=? AND owner=?
                   AND state='running' AND lease_until>?""",
                (now + lease_seconds, task["id"], task["owner"], now),
            )
            return cursor.rowcount == 1

    async def finish(
        self, task, state, *, code=None, message=None, result=None, due_at=None, now=None
    ):
        if state not in TERMINAL_STATES | {"queued", "retry_wait"}:
            raise ValueError("Invalid task completion state")
        now = time.time() if now is None else now
        encoded = json.dumps(result) if result is not None else None
        async with self.transaction() as db:
            cursor = await db.execute(
                """UPDATE tasks SET state=?,error_code=?,message=?,result=?,due_at=?,
                   updated_at=?,owner=NULL,lease_until=NULL WHERE id=? AND owner=?
                   AND state='running' AND lease_until>?""",
                (
                    state,
                    code,
                    message,
                    encoded,
                    now if due_at is None else due_at,
                    now,
                    task["id"],
                    task["owner"],
                    now,
                ),
            )
            if cursor.rowcount != 1:
                return False
            await db.execute(
                """UPDATE task_attempts SET state=?,error_code=?,message=?,result=?,finished_at=?
                   WHERE task_id=? AND owner=? AND finished_at IS NULL""",
                (state, code, message, encoded, now, task["id"], task["owner"]),
            )
            return True

    async def recover(self, *, startup=False, now=None):
        """Never replay a possibly-mutating operation after losing its owner."""
        now = time.time() if now is None else now
        async with self.transaction() as db:
            rows = await (
                await db.execute(
                    """SELECT * FROM tasks WHERE state='running' AND (? OR lease_until<=?)
                       AND NOT EXISTS (SELECT 1 FROM deployment_observations o
                           WHERE o.task_id=tasks.id AND o.state IN ('waiting','finalizing'))""",
                    (startup, now),
                )
            ).fetchall()
            for row in rows:
                result = json.loads(row["result"] or "{}")
                if row["kind"] != "fetch" and result.get("run_id"):
                    await db.execute(
                        "UPDATE executor_run_history SET status='failed',finished_at=?,message='Task worker interrupted; verification required' WHERE id=? AND status IN ('queued','running')",
                        (datetime.fromtimestamp(now).isoformat(), result["run_id"]),
                    )
                state = (
                    (
                        "failed"
                        if row["kind"] == "deploy" and result.get("mutation_started") is False
                        else "needs_attention"
                    )
                    if row["kind"] != "fetch"
                    else ("retry_wait" if row["attempts"] <= row["max_retries"] else "failed")
                )
                await db.execute(
                    """UPDATE task_attempts SET state='interrupted',error_code='worker_interrupted',
                       finished_at=? WHERE task_id=? AND finished_at IS NULL""",
                    (now, row["id"]),
                )
                await db.execute(
                    """UPDATE tasks SET state=?,error_code='worker_interrupted',owner=NULL,
                       lease_until=NULL,due_at=?,updated_at=? WHERE id=?""",
                    (state, now, now, row["id"]),
                )
            return len(rows)

    async def cancel(self, task_id, *, now=None):
        now = time.time() if now is None else now
        async with self.transaction() as db:
            cursor = await db.execute(
                "UPDATE tasks SET state='cancelled',updated_at=? WHERE id=? AND state IN ('queued','retry_wait')",
                (now, task_id),
            )
            return cursor.rowcount == 1
