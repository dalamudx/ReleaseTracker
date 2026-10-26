"""Settle only older, unstarted requests fulfilled by the same frozen deployment."""

import json
import time


def _target_coordinates(payload):
    targets = payload.get("targets")
    if not isinstance(targets, list) or not targets:
        return None
    fields = (
        "tracker_name",
        "source_id",
        "channel",
        "deploy_alias",
        "target",
        "chart_version",
        "chart_digest",
    )
    if any(
        not isinstance(t, dict) or not isinstance(t.get("target"), list) or len(t["target"]) != 2
        for t in targets
    ):
        return None
    return sorted(json.dumps({k: t.get(k) for k in fields}, sort_keys=True) for t in targets)


async def settle_fulfilled_requests(storage, task):
    """Keep audit history; never mark an unexecuted request as a successful write."""
    async with storage.tasks.transaction() as db:
        source = await (
            await db.execute("SELECT * FROM tasks WHERE id=?", (task["id"],))
        ).fetchone()
        if (
            source is None
            or source["kind"] != "deploy"
            or source["state"] != "succeeded"
            or json.loads(source["result"] or "{}").get("mutation_started") is not True
        ):
            return 0
        payload = json.loads(source["payload"])
        approved = await (
            await db.execute(
                "SELECT 1 FROM deployment_plans WHERE task_id=? AND executor_id=? AND state IN ('approved','applied') LIMIT 1",
                (source["id"], payload["executor_id"]),
            )
        ).fetchone()
        if approved is None:
            return 0
        coordinates = _target_coordinates(payload)
        if not coordinates or not payload.get("config_identity"):
            return 0
        # A newer write makes the old success insufficient to settle any more requests.
        newer = await (
            await db.execute(
                """SELECT 1 FROM tasks WHERE kind='deploy' AND id>? AND json_extract(payload,'$.executor_id')=?
               AND json_extract(result,'$.mutation_started')=1 LIMIT 1""",
                (source["id"], payload["executor_id"]),
            )
        ).fetchone()
        if newer:
            return 0
        rows = await (
            await db.execute(
                """SELECT id,payload,result FROM tasks WHERE kind='deploy' AND id<?
               AND state IN ('queued','retry_wait') AND owner IS NULL
               AND json_extract(payload,'$.executor_id')=?
               AND COALESCE(json_extract(result,'$.mutation_started'),0)=0""",
                (source["id"], payload["executor_id"]),
            )
        ).fetchall()
        count = 0
        for row in rows:
            pending = json.loads(row["payload"])
            if (
                pending.get("config_identity") != payload["config_identity"]
                or _target_coordinates(pending) != coordinates
            ):
                continue
            result = json.loads(row["result"] or "{}") | {"fulfilled_by_task_id": source["id"]}
            await db.execute(
                """UPDATE tasks SET state='superseded',approval_pending=0,error_code='target_applied',
                   message='Target fulfilled by a later completed deployment',result=?,updated_at=? WHERE id=?""",
                (json.dumps(result), time.time(), row["id"]),
            )
            await db.execute(
                "UPDATE deployment_plans SET state='superseded' WHERE task_id=? AND state IN ('pending','approved')",
                (row["id"],),
            )
            count += 1
        return count


async def reconcile_fulfilled_requests(storage):
    """Resume settlement after a restart, including previously parked approvals."""
    db = await storage._get_connection()
    rows = await (
        await db.execute(
            """SELECT MAX(t.id) id FROM tasks t WHERE t.kind='deploy' AND t.state='succeeded'
           AND json_extract(t.result,'$.mutation_started')=1
           AND EXISTS (SELECT 1 FROM tasks q WHERE q.kind='deploy' AND q.id<t.id
             AND q.state IN ('queued','retry_wait')
             AND json_extract(q.payload,'$.executor_id')=json_extract(t.payload,'$.executor_id'))
           GROUP BY json_extract(t.payload,'$.executor_id') LIMIT 32"""
        )
    ).fetchall()
    for row in rows:
        await settle_fulfilled_requests(storage, {"id": row["id"]})
