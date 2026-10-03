"""Durable, bounded, read-only readiness observation of an already submitted deployment.

A deployment keeps its task/run identity. Waiting never spends a retry or replays a
mutation. Only one application instance owns this dispatcher (like TaskQueue).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime

from ..config import ExecutorConfig
from ..executors.adapter_lifetime import runtime_adapter_scope
from .mutation_scope import mutation_resource_key
from ..executor_scheduler_run_lifecycle import ExecutorRunOutcome

logger = logging.getLogger(__name__)
DEFAULTS = {"timeout": 600, "interval": 5, "attempt_timeout": 10, "stable": 10}
PROBE_REASONS = frozenset(
    {
        "timeout",
        "connection_refused",
        "dns_failure",
        "tls_error",
        "network_unreachable",
        "host_unresolvable",
        "status_mismatch",
        "body_mismatch",
        "runtime_api_error",
        "other",
    }
)


def readiness_grace_seconds():
    try:
        value = int(os.environ.get("RELEASETRACKER_READINESS_PULL_GRACE_SECONDS", "1800"))
    except ValueError:
        value = 1800
    return value if 0 <= value <= 3600 else 1800


async def snapshot_readiness_profile(storage, executor):
    profile = executor.health_check.model_dump(mode="json")
    if profile.get("use_system_readiness_defaults", True):
        for name, default in DEFAULTS.items():
            key = f"readiness_{name}_seconds"
            raw = await storage.get_setting(f"system.{key}")
            try:
                value = int(raw) if raw is not None else default
            except (ValueError, TypeError):
                value = default
            profile[key] = value if (0 if name == "stable" else 1) <= value <= 86400 else default
    return profile


async def resource_scope(storage, executor, scheduler=None):
    # Only verified runtime identities may narrow the conservative global domain.
    # A connection ID is NOT a runtime identity (aliases/proxies may hit one engine).
    if executor.target_ref.get("mode") == "ssh_compose":
        db = await storage._get_connection()
        row = await (
            await db.execute(
                "SELECT runtime_key FROM ssh_compose_ownership WHERE executor_id=?", (executor.id,)
            )
        ).fetchone()
        if row and row[0]:
            return f"engine:{row[0]}"
    if scheduler is not None:
        from .deployment_readiness_probes import resolve_deployment_resource_scope

        try:
            return await asyncio.wait_for(
                resolve_deployment_resource_scope(storage, scheduler, executor), timeout=10
            )
        except Exception:
            pass
    return "*"


class DeploymentReadiness:
    def __init__(self, storage, scheduler, scheduler_host, *, probe=None, clock=time.time):
        self.storage = storage
        self.scheduler = scheduler
        self.scheduler_host = scheduler_host
        self.probe = probe
        self.clock = clock
        self.workers = {}
        self.stopping = False
        self.dispatch_lock = asyncio.Lock()

    async def initialize(self):
        self.scheduler_host.add_interval_job("readiness", "observe", self.tick, seconds=2)

    async def conflicts(self, executor, *, include_blocked=True):
        scope = await resource_scope(self.storage, executor, self.scheduler)
        db = await self.storage._get_connection()
        row = await (
            await db.execute(
                """SELECT 1 FROM deployment_observations o JOIN tasks t ON t.id=o.task_id
               WHERE (o.state IN ('waiting','finalizing') OR
                      (o.state='blocked' AND t.state='needs_attention' AND ?))
               AND (o.executor_id=? OR o.resource_scope='*' OR ?='*' OR o.resource_scope=?) LIMIT 1""",
                (include_blocked, executor.id, scope, scope),
            )
        ).fetchone()
        return bool(row)

    async def handoff(self, task, executor, run_id, baseline, **finalization):
        from .deployment_readiness_probes import capture_deployment_target

        now = self.clock()
        profile = executor.health_check.model_dump(mode="json")
        diagnostics = dict(finalization.get("diagnostics") or {})
        run = await self.storage.get_executor_run(run_id)
        update_seconds = max(0, round(now - run.started_at.timestamp(), 1)) if run else 0
        try:
            target = await asyncio.wait_for(
                capture_deployment_target(self.storage, self.scheduler, executor),
                timeout=profile.get("readiness_attempt_timeout_seconds", 10),
            )
        except Exception:
            target = {"capture_error": True}
        verification = {
            "baseline": baseline,
            "pull_grace_seconds": readiness_grace_seconds(),
            "submitted_at": now,
            "observe_pod_stability": True,
            "target": target,
            "run_id": run_id,
            "diagnostics": diagnostics,
            "services": diagnostics.get("services", []),
            "from_version": finalization.get("from_version"),
            "to_version": finalization.get("to_version"),
            "readiness_context": diagnostics.get("readiness_context", {}),
            "update_duration_seconds": update_seconds,
        }
        result = {
            "outcome": "pending",
            "services": [],
            "elapsed_seconds": 0,
            "strategy": "runtime_native",
            "performed": True,
        }
        diagnostics["health_check"] = result
        finalization["diagnostics"] = diagnostics
        scope = await resource_scope(self.storage, executor, self.scheduler)
        async with self.storage.tasks.transaction() as db:
            current = await (
                await db.execute(
                    "SELECT result FROM tasks WHERE id=? AND owner=? AND state='running' AND lease_until>?",
                    (task["id"], task["owner"], now),
                )
            ).fetchone()
            if not current:
                raise RuntimeError("Deployment lease lost before readiness handoff")
            task_result = json.loads(current[0] or "{}") | {
                "phase": "health_checking",
                "run_id": run_id,
                "health_check": result,
                "mutation_complete": True,
            }
            await db.execute(
                """INSERT INTO deployment_observations(task_id,run_id,executor_id,resource_scope,
                   executor_config,verification,finalization,started_at,deadline,due_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    task["id"],
                    run_id,
                    executor.id,
                    scope,
                    executor.model_dump_json(),
                    json.dumps(verification),
                    json.dumps(finalization),
                    now,
                    now + profile.get("readiness_timeout_seconds", 600),
                    now,
                ),
            )
            await db.execute(
                """UPDATE tasks SET resource_key=?,result=?,owner=NULL,lease_until=NULL,updated_at=?
                   WHERE id=?""",
                (f"readiness:{task['id']}", json.dumps(task_result), now, task["id"]),
            )
            await db.execute(
                """INSERT INTO executor_status(executor_id,last_result,last_run_at,updated_at) VALUES (?,'health_checking',?,?)
                ON CONFLICT(executor_id) DO UPDATE SET last_result='health_checking',last_error=NULL,updated_at=excluded.updated_at""",
                (
                    executor.id,
                    datetime.fromtimestamp(now).isoformat(),
                    datetime.fromtimestamp(now).isoformat(),
                ),
            )
            await db.execute(
                """UPDATE executor_run_history SET status='health_checking',finished_at=NULL,
                   from_version=?,to_version=?,message=?,diagnostics=? WHERE id=?""",
                (
                    finalization.get("from_version"),
                    finalization.get("to_version"),
                    "Update submitted; waiting for readiness",
                    json.dumps(diagnostics),
                    run_id,
                ),
            )
        return ExecutorRunOutcome(
            "health_checking",
            finalization.get("from_version"),
            finalization.get("to_version"),
            "Waiting for readiness",
        )

    async def enqueue_recheck(self, original_task):
        from .deploy_tasks import DeployTasks

        db = await self.storage._get_connection()
        row = await (
            await db.execute(
                "SELECT * FROM deployment_observations WHERE task_id=?", (original_task["id"],)
            )
        ).fetchone()
        if not row or row["state"] not in {"completed", "blocked"} or row["outcome"] == "healthy":
            raise ValueError("Only finished unsuccessful readiness observations can be rechecked")
        executor = await self.storage.get_executor_config(row["executor_id"])
        if (
            executor is None
            or await DeployTasks(self.storage, self.scheduler).identity(executor)
            != original_task["payload"]["config_identity"]
        ):
            raise ValueError("Target configuration changed; cannot recheck the original deployment")
        payload = {
            "executor_id": executor.id,
            "action": "readiness_recheck",
            "observation_task_id": original_task["id"],
            "config_identity": original_task["payload"]["config_identity"],
            "health_check": await snapshot_readiness_profile(self.storage, executor),
        }
        task = await self.storage.tasks.enqueue(
            kind="recover",
            resource_key=await mutation_resource_key(self.storage, self.scheduler, executor),
            dedupe_key=f"readiness-recheck:{row['run_id']}",
            target_label=executor.name,
            payload=payload,
            trigger_mode="manual",
            max_retries=0,
            join_running=True,
        )
        return {"task_id": task["id"], "status": task["state"]}

    async def begin_recheck(self, task):
        from .task_queue import TaskResult

        db = await self.storage._get_connection()
        original = await (
            await db.execute(
                "SELECT * FROM deployment_observations WHERE task_id=?",
                (task["payload"]["observation_task_id"],),
            )
        ).fetchone()
        if original is None or original["state"] not in {"completed", "blocked"}:
            return TaskResult("failed", "readiness_observation_unavailable")
        executor_data = json.loads(original["executor_config"])
        executor_data["health_check"] = task["payload"]["health_check"]
        executor = ExecutorConfig.model_validate(executor_data)
        now = self.clock()
        finalization = json.loads(original["finalization"])
        finalization["recheck_of"] = task["payload"]["observation_task_id"]
        pending = {
            "outcome": "pending",
            "services": [],
            "elapsed_seconds": 0,
            "strategy": "runtime_native",
            "performed": True,
        }
        result = {
            "phase": "health_checking",
            "run_id": original["run_id"],
            "health_check": pending,
            "readiness_recheck": True,
        }
        async with self.storage.tasks.transaction() as db:
            cursor = await db.execute(
                "UPDATE tasks SET resource_key=?,owner=NULL,lease_until=NULL,result=? WHERE id=? AND owner=? AND state='running' AND lease_until>?",
                (f"readiness:{task['id']}", json.dumps(result), task["id"], task["owner"], now),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Readiness recheck lease lost")
            await db.execute(
                """INSERT INTO deployment_observations(task_id,run_id,executor_id,resource_scope,executor_config,
                verification,finalization,started_at,deadline,due_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    task["id"],
                    original["run_id"],
                    executor.id,
                    original["resource_scope"],
                    executor.model_dump_json(),
                    original["verification"],
                    json.dumps(finalization),
                    now,
                    now + executor.health_check.readiness_timeout_seconds,
                    now,
                ),
            )
            run = await (
                await db.execute(
                    "SELECT diagnostics FROM executor_run_history WHERE id=?", (original["run_id"],)
                )
            ).fetchone()
            parent = await (
                await db.execute(
                    "SELECT result FROM tasks WHERE id=?", (finalization["recheck_of"],)
                )
            ).fetchone()
            parent_result = json.loads(parent[0] or "{}") | {"health_recheck": pending}
            await db.execute(
                "UPDATE tasks SET result=?,updated_at=? WHERE id=?",
                (json.dumps(parent_result), now, finalization["recheck_of"]),
            )
            diagnostics = json.loads(run[0] or "{}") | {"health_recheck": pending}
            await db.execute(
                "UPDATE executor_run_history SET diagnostics=? WHERE id=?",
                (json.dumps(diagnostics), original["run_id"]),
            )
        return TaskResult("observing", result=result)

    async def tick(self):
        if self.stopping or self.dispatch_lock.locked():
            return
        async with self.dispatch_lock:
            db = await self.storage._get_connection()
            rows = await (
                await db.execute(
                    "SELECT * FROM deployment_observations WHERE state IN ('waiting','finalizing') AND due_at<=? ORDER BY due_at LIMIT 32",
                    (self.clock(),),
                )
            ).fetchall()
            for row in rows:
                if len(self.workers) >= 3:
                    break
                if row["task_id"] in self.workers:
                    continue
                worker = asyncio.create_task(self._observe(dict(row)))
                self.workers[row["task_id"]] = worker
                worker.add_done_callback(lambda done, key=row["task_id"]: self._done(key, done))

    def _done(self, key, worker):
        self.workers.pop(key, None)
        if not worker.cancelled() and worker.exception():
            logger.error(
                "Readiness observer %s interrupted (%s)", key, type(worker.exception()).__name__
            )

    @runtime_adapter_scope
    async def _supplement(self, executor, verification, result, remaining):
        if result.get("outcome") != "healthy" or executor.health_check.strategy in {
            "none",
            "runtime_native",
            "helm_status",
        }:
            return result
        from ..executors.health_check.factory import ProbeFactory
        from ..executors.health_check.types import HealthCheckContext
        from .runtime_credentials import materialize_runtime_connection_credentials

        connection = await self.storage.get_runtime_connection(executor.runtime_connection_id)
        connection = await materialize_runtime_connection_credentials(self.storage, connection)
        adapter = self.scheduler._get_adapter(executor.id, connection)
        probe = ProbeFactory().build(
            executor.health_check.strategy, executor.target_ref.get("mode", "container")
        )
        ctx = HealthCheckContext(
            executor_config=executor,
            adapter=adapter,
            run_id=verification["run_id"],
            update_phase_end_at=datetime.now(),
            baseline=verification.get("baseline", {}),
        )
        extra = await asyncio.wait_for(probe.attempt(ctx), timeout=max(0.01, remaining))
        service = {
            "service": "Application probe",
            "method": executor.health_check.strategy,
            "status": "healthy" if extra.healthy else "pending",
            # Finite reason codes only: probe errors may contain URLs or headers.
            "message": (
                "probe_passed"
                if extra.healthy
                else f"probe_{extra.error_category if extra.error_category in PROBE_REASONS else 'failed'}"
            ),
        }
        status_code = (extra.detail or {}).get("http_last_status")
        if not extra.healthy and isinstance(status_code, int):
            service["status_code"] = status_code
        result = result | {"services": [*result.get("services", []), service]}
        if not extra.healthy:
            return result | {"outcome": "pending", "message": service["message"]}
        return result

    async def _observe(self, row):
        if await self.storage.get_executor_run(row["run_id"]) is None:
            now = self.clock()
            result = {"outcome": "unknown", "message": "executor_run_missing", "services": []}
            async with self.storage.tasks.transaction() as db:
                await db.execute(
                    "UPDATE deployment_observations SET state='blocked',result=?,finished_at=? WHERE task_id=?",
                    (json.dumps(result), now, row["task_id"]),
                )
                await db.execute(
                    "UPDATE tasks SET state='needs_attention',owner=NULL,lease_until=NULL,error_code='executor_run_missing',message='Readiness record missing; manual review required',updated_at=? WHERE id=? AND state='running'",
                    (now, row["task_id"]),
                )
                await db.execute(
                    "UPDATE task_attempts SET state='needs_attention',finished_at=?,error_code='executor_run_missing' WHERE task_id=? AND finished_at IS NULL",
                    (now, row["task_id"]),
                )
            return
        executor = ExecutorConfig.model_validate_json(row["executor_config"])
        if row["state"] == "finalizing":
            await self._finalize(row, executor, json.loads(row["result"]))
            return
        now = self.clock()
        verification = json.loads(row["verification"])
        profile = executor.health_check.model_dump(mode="json")
        task = await self.storage.tasks.get(row["task_id"])
        current = await self.storage.get_executor_config(executor.id)
        from .deploy_tasks import DeployTasks

        identity_changed = (
            current is None
            or await DeployTasks(self.storage, self.scheduler).identity(current)
            != task["payload"]["config_identity"]
        )
        if identity_changed:
            result = {
                "outcome": "superseded",
                "services": [],
                "message": "Executor configuration changed during readiness observation",
            }
        elif now >= row["deadline"]:
            previous = json.loads(row["result"] or "{}")
            if (
                previous.get("outcome") == "pending"
                and previous.get("retryable_pull") is True
                and verification.get("pull_grace_seconds", 0) > 0
                and executor.target_ref.get("mode") == "kubernetes_workload"
            ):
                # Never extend based on stale Pod evidence: the image may have
                # become invalid or the workload may have changed since last tick.
                try:
                    from .deployment_readiness_probes import probe_deployment

                    probe = self.probe or probe_deployment
                    budget = profile.get("readiness_attempt_timeout_seconds", 10)
                    async with asyncio.timeout(budget):
                        result = dict(
                            await probe(self.storage, self.scheduler, executor, verification)
                        )
                        result = await self._supplement(executor, verification, result, budget)
                    if result.get("outcome") == "healthy" and not verification.get(
                        "initial_deadline"
                    ):
                        verification["deadline_pull_recheck"] = True
                except asyncio.CancelledError:
                    raise
                except Exception:
                    result = {
                        "outcome": "unknown",
                        "services": [],
                        "message": "Runtime observation unavailable",
                    }
                if result.get("outcome") == "pending" and not result.get("retryable_pull"):
                    result = result | {
                        "outcome": "timeout",
                        "message": "Readiness deadline exceeded",
                    }
            else:
                result = previous | {
                    "outcome": "unknown" if previous.get("outcome") == "unknown" else "timeout",
                    "message": "Readiness deadline exceeded",
                }
        else:
            try:
                from .deployment_readiness_probes import probe_deployment

                probe = self.probe or probe_deployment
                budget = min(
                    profile.get("readiness_attempt_timeout_seconds", 10), row["deadline"] - now
                )
                async with asyncio.timeout(budget):
                    result = dict(await probe(self.storage, self.scheduler, executor, verification))
                    result = await self._supplement(executor, verification, result, budget)
            except asyncio.CancelledError:
                raise
            except Exception:
                result = {
                    "outcome": "unknown",
                    "services": [],
                    "message": "Runtime observation unavailable",
                }
        verification.update(result.pop("verification_update", {}))
        now = self.clock()
        deadline = row["deadline"]
        if (
            now >= deadline
            and result.get("outcome") in {"pending", "healthy"}
            and (
                result.get("retryable_pull") is True
                or verification.pop("deadline_pull_recheck", False)
            )
            and not verification.get("initial_deadline")
            and not task["payload"].get("observation_task_id")
            and executor.target_ref.get("mode") == "kubernetes_workload"
            and verification.get("pull_grace_seconds", 0) > 0
        ):
            verification["initial_deadline"] = deadline
            deadline = deadline + verification["pull_grace_seconds"]
            if result["outcome"] == "pending":
                result["message"] = "image_pull_retrying"
        if verification.get("initial_deadline") and result.get("outcome") == "healthy":
            result["late_recovery"] = True
            result["message"] = "readiness_recovered"
        result["elapsed_seconds"] = max(0, round(now - row["started_at"], 1))
        result["duration_seconds"] = result["elapsed_seconds"]
        if not task["payload"].get("observation_task_id"):
            result["update_duration_seconds"] = verification.get("update_duration_seconds", 0)
            result["total_duration_seconds"] = round(
                result["update_duration_seconds"] + result["elapsed_seconds"], 1
            )
        result["strategy"] = "runtime_native"
        result["performed"] = True
        result["native_health_absent"] = any(
            item.get("method") == "runtime_state" for item in result.get("services", [])
        )
        stable_since = row["stable_since"]
        pod_stability = result.get("pod_stability")
        if pod_stability is not None:
            if verification.get("pod_stability") != pod_stability:
                stable_since = None
            verification["pod_stability"] = pod_stability
        outcome = result.get("outcome", "unknown")
        if outcome == "healthy":
            stable_since = now if stable_since is None else stable_since
            if now - stable_since < profile.get("readiness_stable_seconds", 10):
                outcome = result["outcome"] = "pending"
                result["message"] = "Ready; confirming stability"
        else:
            stable_since = None
        # Transient transport failures are re-observed, never re-deployed.
        if outcome == "healthy" and now > deadline:
            outcome = result["outcome"] = "timeout"
            result["message"] = "Readiness deadline exceeded"
        terminal = outcome in {"healthy", "unhealthy", "unsupported", "superseded", "timeout"}
        if not terminal and now >= deadline:
            result["outcome"] = "unknown" if outcome == "unknown" else "timeout"
            terminal = True
        state = "finalizing" if terminal else "waiting"
        due = min(
            deadline,
            now
            + (
                min(120, max(30, profile.get("readiness_interval_seconds", 5)))
                if verification.get("initial_deadline")
                else profile.get("readiness_interval_seconds", 5)
            ),
        )
        finalization = json.loads(row["finalization"])
        diagnostics = finalization.get("diagnostics", {}) | {"health_check": result}
        async with self.storage.tasks.transaction() as db:
            await db.execute(
                "UPDATE deployment_observations SET state=?,result=?,stable_since=?,due_at=?,outcome=?,verification=?,deadline=? WHERE task_id=? AND state='waiting'",
                (
                    state,
                    json.dumps(result),
                    stable_since,
                    due,
                    result["outcome"],
                    json.dumps(verification),
                    deadline,
                    row["task_id"],
                ),
            )
            task = await (
                await db.execute("SELECT result FROM tasks WHERE id=?", (row["task_id"],))
            ).fetchone()
            task_result = json.loads(task[0] or "{}") | {
                "phase": "health_checking",
                "health_check": result,
            }
            await db.execute(
                "UPDATE tasks SET result=?,updated_at=? WHERE id=?",
                (json.dumps(task_result), now, row["task_id"]),
            )
            if finalization.get("recheck_of"):
                run = await (
                    await db.execute(
                        "SELECT diagnostics FROM executor_run_history WHERE id=?", (row["run_id"],)
                    )
                ).fetchone()
                parent = await (
                    await db.execute(
                        "SELECT result FROM tasks WHERE id=?", (finalization["recheck_of"],)
                    )
                ).fetchone()
                parent_result = json.loads(parent[0] or "{}") | {"health_recheck": result}
                await db.execute(
                    "UPDATE tasks SET result=?,updated_at=? WHERE id=?",
                    (json.dumps(parent_result), now, finalization["recheck_of"]),
                )
                diagnostics = json.loads(run[0] or "{}") | {"health_recheck": result}
                await db.execute(
                    "UPDATE executor_run_history SET diagnostics=? WHERE id=?",
                    (json.dumps(diagnostics), row["run_id"]),
                )
            else:
                await db.execute(
                    "UPDATE executor_run_history SET diagnostics=? WHERE id=? AND status='health_checking'",
                    (json.dumps(diagnostics), row["run_id"]),
                )
        if terminal:
            await self._finalize(row, executor, result)

    async def _finalize(self, row, executor, result):
        final = json.loads(row["finalization"])
        final["diagnostics"] = (final.get("diagnostics") or {}) | {"health_check": result}
        healthy = result["outcome"] == "healthy"
        partial = final.get("status") == "failed"
        status = "success" if healthy and not partial else "failed"
        final["status"] = status
        final["message"] = (
            "Deployment ready"
            if status == "success"
            else (
                final.get("message")
                if partial
                else result.get("message") or f"Readiness {result['outcome']}"
            )
        )
        degraded = (
            status == "failed"
            and not partial
            and result["outcome"] in {"unhealthy", "timeout"}
            and executor.health_check.failure_policy == "mark_degraded"
        )
        if degraded:
            # Same contract as the legacy runner: the update stays applied and is
            # reported as degraded, distinguishable from a hard readiness failure.
            result["failure_policy"] = "mark_degraded"
            final["message"] = f"degraded: {final['message']}"
        final["last_error"] = None if status == "success" else final["message"]
        if status == "success" and executor.target_ref.get("mode") == "ssh_compose":
            snapshot_id = final["diagnostics"].get("snapshot_id")
            if snapshot_id:
                await self.storage.set_executor_snapshot_locked(
                    executor.id, snapshot_id, locked=False
                )
                await self.scheduler._prune_snapshot_history(executor.id)
        if final.get("recheck_of"):
            async with self.storage.tasks.transaction() as db:
                run = await (
                    await db.execute(
                        "SELECT diagnostics FROM executor_run_history WHERE id=?", (row["run_id"],)
                    )
                ).fetchone()
                diagnostics = json.loads(run[0] or "{}")
                checks = [
                    c
                    for c in diagnostics.get("health_rechecks", [])
                    if c.get("task_id") != row["task_id"]
                ]
                checks.append({"task_id": row["task_id"], "finished_at": self.clock(), **result})
                diagnostics.update(health_recheck=result, health_rechecks=checks)
                latest = await (
                    await db.execute(
                        "SELECT MAX(id) FROM executor_run_history WHERE executor_id=?",
                        (executor.id,),
                    )
                ).fetchone()
                current_run = latest and latest[0] == row["run_id"]
                reconciled = bool(healthy and status == "success" and current_run)
                if reconciled:
                    # The original attempt failed at the deadline, but the *same*
                    # immutable target later reached readiness. Keep that evidence
                    # and the failed attempt; correct the final run/task/status.
                    parent = await (
                        await db.execute(
                            "SELECT result FROM tasks WHERE id=?", (final["recheck_of"],)
                        )
                    ).fetchone()
                    if parent:
                        parent_result = json.loads(parent[0] or "{}")
                        parent_result.setdefault(
                            "initial_readiness_failure", parent_result.get("health_check")
                        )
                        parent_result.update(health_check=result, health_recheck=result)
                        await db.execute(
                            """UPDATE tasks SET state='succeeded',result=?,error_code=NULL,
                               message='Deployment ready after recheck',updated_at=?
                               WHERE id=? AND state IN ('failed','needs_attention')""",
                            (json.dumps(parent_result), self.clock(), final["recheck_of"]),
                        )
                    await db.execute(
                        """UPDATE executor_status SET last_result='success',last_error=NULL,updated_at=?
                           WHERE executor_id=? AND last_result IN ('failed','health_checking')""",
                        (datetime.fromtimestamp(self.clock()).isoformat(), executor.id),
                    )
                    await db.execute(
                        """UPDATE executor_run_history SET status='success',finished_at=?,
                           message='Deployment ready after recheck',diagnostics=? WHERE id=?""",
                        (
                            datetime.fromtimestamp(self.clock()).isoformat(),
                            json.dumps(diagnostics),
                            row["run_id"],
                        ),
                    )
                else:
                    await db.execute(
                        "UPDATE executor_run_history SET diagnostics=? WHERE id=?",
                        (json.dumps(diagnostics), row["run_id"]),
                    )
                from .executor_notification_outbox import write_notification_intent

                if not current_run:
                    # Never send a recovered notice about an obsolete deployment.
                    pass
                else:
                    await write_notification_intent(
                        db,
                        {
                            "entity": "executor_health_recheck",
                            "executor_id": executor.id,
                            "executor_name": executor.name,
                            "run_id": row["run_id"],
                            "runtime_type": executor.runtime_type,
                            "target_mode": executor.target_ref.get("mode"),
                            "from_version": final.get("from_version"),
                            "to_version": final.get("to_version"),
                            "status": (
                                "readiness_recovered" if healthy else "readiness_recheck_failed"
                            ),
                            "health_check": result,
                        },
                        notify_health_result=executor.health_check.notify_result,
                    )
        else:
            await self.scheduler._finalize_run(executor, row["run_id"], **final)
        now = self.clock()
        blocked = result["outcome"] in {"unknown", "unsupported", "superseded"}
        task_state = (
            "succeeded" if status == "success" else ("needs_attention" if blocked else "failed")
        )
        async with self.storage.tasks.transaction() as db:
            task = await (
                await db.execute("SELECT result FROM tasks WHERE id=?", (row["task_id"],))
            ).fetchone()
            task_result = json.loads(task[0] or "{}") | {
                "phase": "completed",
                "health_check": result,
            }
            await db.execute(
                "UPDATE tasks SET state=?,result=?,error_code=?,message=?,updated_at=? WHERE id=?",
                (
                    task_state,
                    json.dumps(task_result),
                    (
                        None
                        if status == "success"
                        else "readiness_degraded" if degraded else f"readiness_{result['outcome']}"
                    ),
                    final["message"],
                    now,
                    row["task_id"],
                ),
            )
            await db.execute(
                "UPDATE task_attempts SET state=?,result=?,finished_at=? WHERE task_id=? AND finished_at IS NULL",
                (task_state, json.dumps(task_result), now, row["task_id"]),
            )
            if healthy and final.get("recheck_of"):
                await db.execute(
                    """UPDATE tasks SET state='failed',error_code='readiness_reconciled',updated_at=?
                    WHERE id IN (SELECT task_id FROM deployment_observations WHERE run_id=?)
                    AND state='needs_attention'""",
                    (now, row["run_id"]),
                )
            await db.execute(
                "UPDATE deployment_observations SET state=?,finished_at=? WHERE task_id=?",
                ("blocked" if blocked else "completed", now, row["task_id"]),
            )
        handler = getattr(self.scheduler, "deploy_tasks", None)
        if handler and not final.get("recheck_of"):
            await handler.finished(await self.storage.tasks.get(row["task_id"]))

    async def shutdown(self):
        self.stopping = True
        self.scheduler_host.remove_job("readiness", "observe")
        await asyncio.gather(*list(self.workers.values()), return_exceptions=True)
