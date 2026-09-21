"""Explicit operator recovery shares the mutation queue, never an automatic retry."""

from __future__ import annotations

import hashlib
import json

from .deploy_tasks import DeployTasks
from .task_queue import Deferred, TaskResult
import time


class RecoveryTasks(DeployTasks):
    async def enqueue_recovery(self, executor, snapshot_id, action, actor=None):
        if snapshot_id is None:
            db = await self.storage._get_connection()
            row = await (
                await db.execute(
                    "SELECT id FROM executor_snapshots WHERE executor_id=? ORDER BY id DESC LIMIT 1",
                    (executor.id,),
                )
            ).fetchone()
            if row is None:
                raise ValueError("No recovery snapshot")
            snapshot_id = row[0]
        payload = {
            "executor_id": executor.id,
            "snapshot_id": snapshot_id,
            "action": action,
            "actor": actor,
            "config_identity": await self.identity(executor),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        task = await self.storage.tasks.enqueue(
            kind="recover",
            resource_key="deployment-mutations",
            dedupe_key=f"recover:{digest}",
            target_label=executor.name,
            payload=payload,
            trigger_mode="manual",
            max_retries=0,
            join_running=True,
        )
        return {"task_id": task["id"], "status": task["state"]}

    async def prepare(self, task):
        executor = await self.storage.get_executor_config(task["payload"]["executor_id"])
        if executor is None or await self.identity(executor) != task["payload"]["config_identity"]:
            return TaskResult("superseded", "configuration_changed")
        observer = getattr(self.scheduler, "readiness", None)
        if observer is not None and await observer.conflicts(executor, include_blocked=False):
            return Deferred(time.time() + 5, "target_awaiting_readiness")
        if executor.id in self.scheduler._running_executor_ids:
            return TaskResult("failed", "executor_running")
        return None

    async def execute(self, task):
        payload = task["payload"]
        if payload["action"] == "readiness_recheck":
            return await self.scheduler.readiness.begin_recheck(task)
        executor = await self.storage.get_executor_config(payload["executor_id"])
        if not await self.scheduler._try_acquire_executor_run(executor.id):
            return TaskResult("failed", "executor_running")
        try:
            if payload["action"] in {"restore_files", "verify_and_unlock"}:
                from .ssh_compose_recovery import recover_project

                result = await recover_project(
                    self.storage, executor, payload["snapshot_id"], payload["action"]
                )
                verified = result.get("status") == "verified"
            else:
                from .rollback_service import RollbackService
                from .runtime_credentials import materialize_runtime_connection_credentials

                connection = await self.storage.get_runtime_connection(
                    executor.runtime_connection_id
                )
                connection = await materialize_runtime_connection_credentials(
                    self.storage, connection
                )
                adapter = self.scheduler._get_adapter(executor.id, connection)
                outcome = await RollbackService(
                    self.storage, self.scheduler.snapshot_service
                ).rollback(
                    executor_config=executor,
                    adapter=adapter,
                    snapshot_id=payload["snapshot_id"],
                    actor=payload["actor"],
                )
                result = {"run_id": outcome.run.id, "recovery_outcome": outcome.recovery_outcome}
                verified = outcome.run.status == "success"
                if not verified:
                    return TaskResult("needs_attention", "recovery_failed", result=result)
            if verified:
                # Only a verified recovery of this executor releases its uncertain jobs.
                async with self.storage.tasks.transaction() as db:
                    await db.execute(
                        """UPDATE tasks SET state='failed',error_code='operator_reconciled'
                           WHERE state='needs_attention' AND json_extract(payload,'$.executor_id')=?
                           AND id<?""",
                        (executor.id, task["id"]),
                    )
            return TaskResult(result=result)
        finally:
            await self.scheduler._release_executor_run(executor.id)

    async def finished(self, task):
        pass
