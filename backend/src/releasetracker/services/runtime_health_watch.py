"""Opt-in, read-only monitoring of a last verified successful deployment.

A monitoring incident is not a failed deployment. Never rewrite task/history,
execute mutations or serialize raw remote diagnostics into the alert/state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from .deployment_readiness_probes import probe_deployment
from .release_notification_outbox import enqueue_system_alert

logger = logging.getLogger(__name__)
STATE_PREFIX = "runtime_health."
OUTCOMES = frozenset({"healthy", "pending", "unhealthy", "unknown", "superseded"})


def watch_interval():
    try:
        value = int(os.environ.get("RELEASETRACKER_RUNTIME_HEALTH_INTERVAL_SECONDS", "0"))
    except ValueError as exc:
        raise ValueError("Invalid runtime health interval") from exc
    if value != 0 and not 300 <= value <= 86400:
        raise ValueError("Runtime health interval must be 0 or 300–86400 seconds")
    return value


class RuntimeHealthWatch:
    def __init__(self, storage, scheduler, observer, host=None, *, clock=time.time, probe=None):
        self.storage = storage
        self.scheduler = scheduler
        self.observer = observer
        self.host = host
        self.clock = clock
        self.probe = probe or probe_deployment
        self.interval = watch_interval()
        self.worker = None
        self.stopping = False

    async def initialize(self):
        if self.interval and self.host:
            self.host.add_interval_job("runtime_health", "tick", self.tick, seconds=60)

    async def tick(self):
        if not self.interval or self.stopping or self.worker and not self.worker.done():
            return
        self.worker = asyncio.create_task(self._work())

    async def _work(self):
        try:
            db = await self.storage._get_connection()
            rows = await (
                await db.execute(
                    """SELECT o.*,s.value AS watch_state,t.payload AS task_payload FROM deployment_observations o
                   JOIN executor_run_history h ON h.id=o.run_id
                   JOIN tasks t ON t.id=o.task_id
                   LEFT JOIN settings s ON s.key=? || o.executor_id
                   WHERE o.state='completed' AND o.outcome='healthy' AND h.status='success'
                   AND o.run_id=(SELECT MAX(id) FROM executor_run_history WHERE executor_id=o.executor_id)
                   AND o.task_id=(SELECT MAX(task_id) FROM deployment_observations
                       WHERE run_id=o.run_id AND state='completed' AND outcome='healthy')
                   ORDER BY CASE WHEN json_valid(s.value) THEN json_extract(s.value,'$.checked_at') ELSE 0 END
                   """,
                    (STATE_PREFIX,),
                )
            ).fetchall()
            checked = 0
            for row in rows:
                if self.stopping:
                    break
                try:
                    checked += bool(await self.check(dict(row)))
                    if checked >= 16:
                        break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.error("Runtime health monitoring interrupted")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Runtime health monitoring interrupted")
        finally:
            await self.storage.close_current_task_connection()

    async def _is_current(self, executor, row):
        if executor.id in self.scheduler._running_executor_ids:
            return False
        # Respect other executors' in-flight/blocked mutations on this target.
        # A legacy or unknown lock stays global; the stored scope belongs to the
        # very deployment whose configuration identity is checked below.
        scope = row["resource_scope"]
        db = await self.storage._get_connection()
        conflict = await (
            await db.execute(
                """SELECT 1 FROM tasks t LEFT JOIN deployment_observations o ON o.task_id=t.id
               WHERE t.kind IN ('deploy','recover') AND t.state IN ('running','needs_attention')
               AND (json_extract(t.payload,'$.executor_id')=? OR ?='*'
                 OR t.resource_key='deployment-mutations' OR t.resource_key=?
                 OR (o.state IN ('waiting','finalizing','blocked')
                     AND (o.resource_scope='*' OR o.resource_scope=?))
                 OR (t.resource_key NOT LIKE 'deployment-mutations:%'
                     AND t.resource_key NOT LIKE 'readiness:%')) LIMIT 1""",
                (executor.id, scope, f"deployment-mutations:{scope}", scope),
            )
        ).fetchone()
        if conflict:
            return False
        latest = await self.storage.get_latest_executor_run(executor.id)
        current = await self.storage.get_executor_config(executor.id)
        return bool(
            latest
            and latest.id == row["run_id"]
            and latest.status == "success"
            and current
            and current.enabled
            and current.health_check.readiness_enabled
            and await self.scheduler.deploy_tasks.identity(current)
            == json.loads(row["task_payload"]).get("config_identity")
        )

    async def check(self, row):
        executor = await self.storage.get_executor_config(row["executor_id"])
        if not executor or not await self._is_current(executor, row):
            return False
        try:
            state = json.loads(row.get("watch_state") or "{}")
        except ValueError:
            state = {}
        if not isinstance(state, dict) or state.get("run_id") != row["run_id"]:
            state = {}
        now = self.clock()
        if now < float(state.get("checked_at") or 0) + self.interval:
            return False
        verification = json.loads(row["verification"])
        result = {}
        try:
            budget = executor.health_check.readiness_attempt_timeout_seconds
            async with asyncio.timeout(budget):
                result = dict(
                    await self.probe(self.storage, self.scheduler, executor, verification)
                )
                result = await self.observer._supplement(executor, verification, result, budget)
            outcome = result.get("outcome")
            if outcome not in OUTCOMES:
                outcome = "unknown"
        except asyncio.CancelledError:
            raise
        except Exception:
            outcome = "unknown"
        # A new deployment/config change during network I/O invalidates evidence.
        if not await self._is_current(executor, row):
            return False
        stability = result.get("pod_stability")
        if (
            outcome == "healthy"
            and stability
            and state.get("pod_stability") not in (None, stability)
        ):
            outcome = "pending"  # Restart/Pod replacement since the previous check.
        failure_count = 0 if outcome == "healthy" else int(state.get("failures") or 0) + 1
        incident = None if outcome == "healthy" else state.get("incident") or now
        new_state = {
            "run_id": row["run_id"],
            "checked_at": now,
            "outcome": outcome,
            "failures": failure_count,
            "incident": incident,
            "pod_stability": stability,
        }
        # Persist the incident before enqueue: restart can retry a deterministic
        # outbox key, while a recovered-then-failed episode gets a different key.
        await self.storage.set_setting(STATE_PREFIX + str(executor.id), json.dumps(new_state))
        if failure_count >= 3:
            await enqueue_system_alert(
                self.storage,
                f"runtime_health:{executor.id}:{row['run_id']}:{incident}",
                {
                    "tracker_name": "ReleaseTracker",
                    "entity": "runtime_health",
                    "executor_name": executor.name,
                    "error": "Runtime health could not be confirmed after deployment",
                    "reason": f"runtime_{outcome}",
                    "consecutive_failures": failure_count,
                },
            )
        return True

    async def shutdown(self):
        self.stopping = True
        if self.host and self.interval:
            self.host.remove_job("runtime_health", "tick")
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
