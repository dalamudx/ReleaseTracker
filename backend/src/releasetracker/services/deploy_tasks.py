"""Durable deployment admission. Mutation failures are never automatically replayed."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timedelta

from ..executor_trigger import _binding_contexts
from ..executor_scheduler_target_resolution import QUEUED_TARGETS
from .task_queue import Deferred, TaskResult
from .task_effects import MUTATION_GUARD


class DeployTasks:
    def __init__(self, storage, scheduler):
        self.storage = storage
        self.scheduler = scheduler

    async def identity(self, executor):
        connection = await self.storage.get_runtime_connection(executor.runtime_connection_id)
        sources = [
            await self.storage.get_tracker_source(binding.tracker_source_id)
            for binding in _binding_contexts(executor)
        ]
        value = {
            "executor": executor.model_dump(mode="json", exclude={"health_check"}),
            "connection": connection.model_dump(mode="json") if connection else None,
            "sources": [source.model_dump(mode="json") if source else None for source in sources],
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    async def enqueue(self, executor_id, *, manual, desired_revision=None):
        executor = await self.storage.get_executor_config(executor_id)
        if not executor or not executor.enabled:
            raise ValueError("Executor is missing or disabled")
        targets = []
        for binding in _binding_contexts(executor):
            resolved = await self.scheduler._resolve_tracker_binding_by_source_id(
                binding.tracker_source_id
            )
            if resolved is None:
                raise ValueError("Executor source is unavailable")
            name, source = resolved
            args = dict(tracker_source_id=source.id, tracker_source_type=source.source_type)
            target = await self.scheduler._resolve_tracker_latest_target(
                name, binding.channel_name, **args
            )
            chart = (
                await self.scheduler._resolve_tracker_latest_chart_version(
                    name, binding.channel_name, **args
                )
                if source.source_type == "helm"
                else None
            )
            targets.append(
                {
                    "tracker_name": name,
                    "source_id": source.id,
                    "channel": binding.channel_name,
                    "target": list(target) if target else None,
                    "chart_version": chart,
                }
            )
        if not targets:
            target = await self.scheduler._resolve_tracker_latest_target(
                executor.tracker_name, executor.channel_name
            )
            targets.append(
                {
                    "tracker_name": executor.tracker_name,
                    "source_id": None,
                    "channel": executor.channel_name,
                    "target": list(target) if target else None,
                    "chart_version": None,
                }
            )
        from .deployment_readiness import snapshot_readiness_profile

        identity = await self.identity(executor)
        payload = {
            "executor_id": executor_id,
            "manual": manual,
            "config_identity": identity,
            "desired_revision": desired_revision,
            "targets": targets,
            "health_check": await snapshot_readiness_profile(self.storage, executor),
        }
        task = await self.storage.tasks.enqueue(
            kind="deploy",
            resource_key="deployment-mutations",
            dedupe_key=f"deploy:{executor_id}",
            target_label=executor.name,
            payload=payload,
            trigger_mode="manual" if manual else "automatic",
            trigger_key=None if manual else f"desired:{executor_id}:{desired_revision}:{identity}",
            max_retries=0,
            join_running=True,
        )
        return {"task_id": task["id"], "status": task["state"]}

    async def dispatch_pending(self):
        states = await self.storage.list_pending_executor_desired_states(limit=100)
        for state in states:
            executor = await self.storage.get_executor_config(state.executor_id)
            if not executor or not executor.enabled or executor.update_mode == "manual":
                await self._defer_dispatch(state, 60)
                continue
            try:
                receipt = await self.enqueue(
                    executor.id, manual=False, desired_revision=state.desired_state_revision
                )
                # A restart may have finished an interrupted task before this handler
                # could acknowledge its desired revision. Never enqueue it again.
                task = await self.storage.tasks.get(receipt["task_id"])
                await self.finished(task)
                await self._defer_dispatch(state, 30)
            except ValueError:
                await self._defer_dispatch(state, 60)
                continue
        return len(states)

    async def _defer_dispatch(self, state, seconds):
        # Rotate admitted/disabled entries out of the bounded scan. CAS prevents a
        # concurrent completion or newer desired revision from being overwritten.
        now = datetime.now()
        db = await self.storage._get_connection()
        await db.execute(
            "UPDATE executor_desired_state SET next_eligible_at=?,updated_at=? "
            "WHERE executor_id=? AND desired_state_revision=? AND pending=1",
            (
                (now + timedelta(seconds=seconds)).isoformat(),
                now.isoformat(),
                state.executor_id,
                state.desired_state_revision,
            ),
        )
        await db.commit()

    async def prepare(self, task):
        payload = task["payload"]
        executor = await self.storage.get_executor_config(payload["executor_id"])
        if (
            not executor
            or not executor.enabled
            or await self.identity(executor) != payload["config_identity"]
        ):
            return TaskResult("superseded", "configuration_changed")
        if not payload["manual"]:
            desired = await self.storage.get_executor_desired_state(executor.id)
            if not desired or desired.desired_state_revision != payload["desired_revision"]:
                return TaskResult("superseded", "target_replaced")
            if executor.update_mode == "manual":
                return TaskResult("skipped", "manual_policy")
            if executor.update_mode == "maintenance_window":
                await self.scheduler._refresh_system_timezone()
                if not self.scheduler._within_maintenance_window(executor.maintenance_window):
                    return Deferred(
                        time.time()
                        + self.scheduler._seconds_until_next_maintenance_window(
                            executor.maintenance_window
                        ),
                        "maintenance_window",
                    )
        observer = getattr(self.scheduler, "readiness", None)
        if observer is not None and await observer.conflicts(executor):
            return Deferred(time.time() + 5, "target_awaiting_readiness")
        if executor.id in self.scheduler._running_executor_ids:
            return Deferred(time.time() + 5, "executor_running")
        latest = await self.storage.get_latest_executor_run(executor.id)
        if latest and latest.status in {"queued", "running", "health_checking"}:
            return TaskResult("needs_attention", "previous_run_interrupted")
        return None

    async def execute(self, task):
        executor = await self.storage.get_executor_config(task["payload"]["executor_id"])
        from ..config import HealthCheckProfile
        from .deployment_readiness_context import DEFER_READINESS, READINESS_FINALIZER
        from .deployment_readiness_probes import capture_deployment_baseline

        if task["payload"].get("health_check"):
            executor = executor.model_copy(
                update={
                    "health_check": HealthCheckProfile.model_validate(
                        task["payload"]["health_check"]
                    )
                }
            )
        observer = getattr(self.scheduler, "readiness", None)
        defer = observer is not None and executor.health_check.readiness_enabled
        run_id = await self.scheduler._claim_executor_run(
            executor.id, trigger="manual" if task["payload"]["manual"] else "automatic"
        )
        if run_id is None:
            return TaskResult("needs_attention", "previous_run_interrupted")
        if not await self.storage.tasks.checkpoint(
            task, {"run_id": run_id, "mutation_started": False}
        ):
            raise asyncio.CancelledError("Deployment lease lost")

        baseline = {}

        async def guard():
            nonlocal baseline
            current = await self.storage.get_executor_config(executor.id)
            if (
                current is None
                or await self.identity(current) != task["payload"]["config_identity"]
            ):
                raise asyncio.CancelledError("Deployment configuration changed")
            if defer and not baseline:
                baseline = await asyncio.wait_for(
                    capture_deployment_baseline(self.storage, self.scheduler, executor),
                    timeout=executor.health_check.readiness_attempt_timeout_seconds,
                )
                if baseline.get("error"):
                    raise ValueError("readiness_baseline_unavailable")
            if not await self.storage.tasks.checkpoint(
                task,
                {"run_id": run_id, "mutation_started": True},
                private_payload={"readiness_baseline": baseline} if defer else None,
            ):
                raise asyncio.CancelledError("Deployment lease lost")

        async def handoff(config, active_run, **finalization):
            return await observer.handoff(task, executor, active_run, baseline, **finalization)

        defer_token = DEFER_READINESS.set(defer)
        finalize_token = READINESS_FINALIZER.set(handoff if defer else None)
        mutation_token = MUTATION_GUARD.set(guard)
        token = QUEUED_TARGETS.set(task["payload"]["targets"])
        try:
            outcome = await self.scheduler._run_executor_with_overlap_guard(
                executor,
                manual=task["payload"]["manual"],
                run_id=run_id,
                desired_state_revision=task["payload"]["desired_revision"],
            )
        finally:
            QUEUED_TARGETS.reset(token)
            MUTATION_GUARD.reset(mutation_token)
            DEFER_READINESS.reset(defer_token)
            READINESS_FINALIZER.reset(finalize_token)
        if outcome.status == "health_checking":
            return TaskResult("observing", result={"run_id": run_id})
        checkpoint = await self.storage.tasks.get(task["id"])
        failure = "needs_attention" if checkpoint["result"].get("mutation_started") else "failed"
        state = {"success": "succeeded", "skipped": "skipped"}.get(outcome.status, failure)
        return TaskResult(
            state,
            "deployment_failed" if state in {"failed", "needs_attention"} else None,
            result={"run_id": run_id},
        )

    async def finished(self, task):
        if task["state"] not in {"succeeded", "skipped", "needs_attention", "failed", "cancelled"}:
            return
        revision = task["payload"].get("desired_revision")
        if revision:
            await self.storage.complete_executor_desired_state(
                task["payload"]["executor_id"],
                expected_revision=revision,
            )
