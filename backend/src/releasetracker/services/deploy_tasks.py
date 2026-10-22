"""Durable deployment admission. Mutation failures are never automatically replayed."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from dataclasses import replace

from ..executor_trigger import _binding_contexts
from ..executors.adapter_lifetime import runtime_adapter_scope
from ..executor_scheduler_target_resolution import QUEUED_TARGETS
from .task_queue import Deferred, TaskResult
from .mutation_scope import mutation_resource_key
from .deployment_targets import resolve_deployment_targets
from .task_effects import MUTATION_GUARD
from .deployment_plan import MANAGED_MARKERS, TargetEvidence, fingerprint, managed_markers
from ..storage.sqlite_deployment_admission import AdmissionConflict, DeploymentAdmissionStore
from .deployment_version_evidence import FORCE_IMAGE_UPDATES, targets_verified

logger = logging.getLogger(__name__)
PRE_MUTATION_RETRIES = 2
# Consecutive read-only inspection failures (30 s apart, only counted while the
# task is otherwise eligible to run) before an operator must look at the target.
ADMISSION_EVIDENCE_PATIENCE = 120
PRE_MUTATION_RETRY_SECONDS = 300
# Executors for these modes skip a write exactly when the image/chart references
# compared by the review diff are already in place. Single containers also
# compare image digests, so their empty diff does not prove a same-tag rebuild
# is current.
_REFERENCE_COMPARED_MODES = frozenset(
    {"docker_compose", "portainer_stack", "kubernetes_workload", "helm_release"}
)


def _already_current(executor, evidence, task=None) -> bool:
    diff = evidence.configuration_diff
    if not (diff is not None and not diff.get("truncated") and not diff.get("lines")):
        return False
    if (executor.target_ref or {}).get("mode") == "helm_release":
        return True
    return task is not None and targets_verified(task, evidence)


class DeployTasks:
    def __init__(self, storage, scheduler):
        self.storage = storage
        self.scheduler = scheduler
        self.admission = DeploymentAdmissionStore(storage)

    async def identity(self, executor):
        connection = await self.storage.get_runtime_connection(executor.runtime_connection_id)
        sources = [
            await self.storage.get_tracker_source(binding.tracker_source_id)
            for binding in _binding_contexts(executor)
        ]
        executor_payload = executor.model_dump(mode="json", exclude={"health_check"})
        target_ref = executor_payload.get("target_ref")
        # An updated container ID points to the replacement created by this deployment;
        # the stable container name defines the operator-configured target identity.
        if isinstance(target_ref, dict) and target_ref.get("container_name"):
            target_ref = dict(target_ref)
            target_ref.pop("container_id", None)
            executor_payload["target_ref"] = target_ref
        value = {
            "executor": executor_payload,
            "connection": connection.model_dump(mode="json") if connection else None,
            "sources": [source.model_dump(mode="json") if source else None for source in sources],
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    @runtime_adapter_scope
    async def _collect_admission_evidence(self, executor, task=None, *, _baseline_proof=True):
        """Collect read-only runtime evidence before a deployment attempt."""
        connection = await self.storage.get_runtime_connection(executor.runtime_connection_id)
        if connection is None or not connection.enabled:
            raise ValueError("runtime_connection_unavailable")
        target_ref = dict(executor.target_ref or {})
        mode = target_ref.get("mode", "container")
        if mode == "container" and target_ref.get("container_name"):
            target_ref.pop("container_id", None)
        runtime_public = {
            "type": connection.type,
            "config": dict(connection.config or {}),
            "credential_id": connection.credential_id,
        }
        runtime_identity = f"{connection.type}:{fingerprint(runtime_public)}"
        target_identity = fingerprint({"mode": mode, "target": target_ref})
        configuration: dict = {"mode": mode, "target": target_ref}
        configuration_hashes = {}
        recovery = {
            "container": "container_config",
            "docker_compose": "compose_config",
            "portainer_stack": "stack_config",
            "kubernetes_workload": "workload_images",
            "helm_release": "helm_revision",
            "ssh_compose": "ssh_files",
        }.get(mode)
        if recovery is None:
            raise ValueError("unsupported_admission_target")

        if mode == "ssh_compose":
            from .deployment_diff import frozen_targets, ssh_review

            version_evidence, current_services = [], {}
            with frozen_targets(task):
                _, target, targets = await self.scheduler._resolve_ssh_update(executor)
                state_hash, diff = await ssh_review(
                    self.storage,
                    executor,
                    connection,
                    target,
                    targets,
                    task=task,
                    version_evidence=version_evidence,
                    current_services=current_services,
                    configuration_hashes=configuration_hashes,
                )
            from .ssh_compose_deploy import read_managed_markers

            markers = await read_managed_markers(self.storage, connection, target, services=targets)
            configuration["target"] = target.model_dump(mode="json")
            configuration["current_services"] = dict(sorted(current_services.items()))
            configuration["runtime_configuration"] = state_hash
            return TargetEvidence(
                runtime_identity,
                target_identity,
                configuration,
                tuple(markers),
                recovery,
                diff,
                tuple(version_evidence),
                configuration_hash=configuration_hashes.get("current"),
                desired_configuration_hash=configuration_hashes.get("desired"),
            )

        from .runtime_credentials import materialize_runtime_connection_credentials

        connection = await materialize_runtime_connection_credentials(self.storage, connection)
        adapter = self.scheduler._get_adapter(executor.id or -1, connection)
        await adapter.validate_target_ref(target_ref)
        markers = await adapter.get_managed_markers(target_ref)
        if mode == "container":
            configuration["current_image"] = await adapter.get_current_image(target_ref)
            configuration["current_digest"] = await adapter.get_current_image_digest(target_ref)
        elif mode == "docker_compose":
            configuration["current_services"] = dict(
                sorted((await adapter.fetch_compose_service_images(target_ref)).items())
            )
        elif mode == "portainer_stack":
            configuration["current_services"] = dict(
                sorted((await adapter.fetch_stack_service_images(target_ref)).items())
            )
        elif mode == "kubernetes_workload":
            configuration["current_services"] = dict(
                sorted((await adapter.fetch_workload_service_images(target_ref)).items())
            )
        elif mode == "helm_release":
            configuration["current_chart_version"] = await adapter.get_helm_release_version(
                target_ref
            )
        from .deployment_diff import frozen_targets, native_review

        version_evidence, pre_rollout_states = [], []
        with frozen_targets(task):
            state_hash, diff = await native_review(
                self.storage,
                self.scheduler,
                executor,
                adapter,
                task,
                version_evidence=version_evidence,
                pre_rollout_states=pre_rollout_states,
                configuration_hashes=configuration_hashes,
            )
        configuration["runtime_configuration"] = state_hash
        evidence = TargetEvidence(
            runtime_identity,
            target_identity,
            configuration,
            tuple(markers),
            recovery,
            diff,
            tuple(version_evidence),
            pre_rollout_hash=(
                fingerprint(configuration | {"runtime_configuration": pre_rollout_states[0]})
                if pre_rollout_states
                else None
            ),
            configuration_hash=configuration_hashes.get("current"),
            desired_configuration_hash=configuration_hashes.get("desired"),
        )
        if _baseline_proof:
            db = await self.storage._get_connection()
            candidate = await (
                await db.execute(
                    """SELECT COALESCE(last_task_id, (SELECT t.id FROM tasks t
                   WHERE t.kind='deploy' AND json_extract(t.payload,'$.executor_id')=managed_targets.executor_id
                   AND json_extract(t.result,'$.mutation_started')=1 ORDER BY t.id DESC LIMIT 1))
                   FROM managed_targets WHERE executor_id=? AND identity_key=? AND (baseline IS NULL OR baseline=?)""",
                    (executor.id, evidence.identity_key, evidence.pre_rollout_hash),
                )
            ).fetchone()
            if candidate and candidate[0] and (not task or candidate[0] != task["id"]):
                original = await self.storage.tasks.get(candidate[0])
                if original and original["state"] == "succeeded":
                    proof = await self._collect_admission_evidence(
                        executor, original, _baseline_proof=False
                    )
                    if proof.evidence_hash == evidence.evidence_hash and targets_verified(
                        original, proof
                    ):
                        evidence = replace(evidence, baseline_task_id=original["id"])
        return evidence

    async def _mark_admission_applied(self, task, executor):
        """Persist post-mutation evidence without changing the runtime outcome."""
        saved = await self.storage.tasks.get(task["id"])
        await self.storage.tasks.checkpoint(
            task,
            {
                **(saved["result"] or {}),
                "baseline_status": "pending",
                "baseline_next_attempt": time.time(),
            },
        )
        try:
            evidence = await self._collect_admission_evidence(executor, task, _baseline_proof=False)
            await self.admission.mark_applied(task, evidence)
        except Exception as exc:
            # The remote result is already authoritative; a failed read only keeps
            # the local target conservative for the next deployment.
            logger.warning(
                "Deployment admission baseline not recorded for task %s: %s%s",
                task["id"],
                type(exc).__name__,
                f" ({exc})" if isinstance(exc, AdmissionConflict) else "",
            )
            return False
        await self._baseline_receipt(task["id"], True)
        return True

    async def _baseline_receipt(self, task_id, verified):
        now = time.time()
        async with self.storage.tasks.transaction() as db:
            row = await (
                await db.execute("SELECT result FROM tasks WHERE id=?", (task_id,))
            ).fetchone()
            if row is None:
                return
            result = json.loads(row["result"] or "{}")
            if not verified and result.get("baseline_status") == "verified":
                return
            attempts = int(result.get("baseline_attempts", 0)) + (not verified)
            stalled = not verified and attempts >= ADMISSION_EVIDENCE_PATIENCE
            result.update(
                baseline_status=(
                    "verified" if verified else "needs_attention" if stalled else "pending"
                ),
                baseline_attempts=attempts,
                baseline_next_attempt=now + 30,
            )
            await db.execute(
                "UPDATE tasks SET result=?,updated_at=? WHERE id=?",
                (json.dumps(result), now, task_id),
            )
            if stalled:
                await db.execute(
                    "UPDATE tasks SET state='needs_attention',error_code='deployment_baseline_unverified',message=? WHERE id=? AND state='succeeded'",
                    (
                        "Deployment completed, but its managed baseline could not be verified",
                        task_id,
                    ),
                )

    async def refresh_admission_baseline(self, task_id, executor_id):
        """Read-only, idempotent completion; failures remain durably retryable."""
        verified = False
        try:
            executor = await self.storage.get_executor_config(executor_id)
            original = await self.storage.tasks.get(task_id)
            if (
                executor
                and original
                and await self.identity(executor) == original["payload"]["config_identity"]
            ):
                evidence = await self._collect_admission_evidence(
                    executor, original, _baseline_proof=False
                )
                verified = await self.admission.refresh_applied(task_id, evidence)
        except Exception as exc:
            logger.warning(
                "Deployment admission baseline not refreshed for task %s: %s",
                task_id,
                type(exc).__name__,
            )
        await self._baseline_receipt(task_id, verified)
        return verified

    async def _reconcile_baselines(self):
        db = await self.storage._get_connection()
        rows = await (
            await db.execute(
                """SELECT id,json_extract(payload,'$.executor_id') executor_id FROM tasks
               WHERE kind='deploy' AND state='succeeded'
               AND json_extract(result,'$.baseline_status')='pending'
               AND json_extract(result,'$.baseline_next_attempt')<=? ORDER BY updated_at LIMIT 3""",
                (time.time(),),
            )
        ).fetchall()
        for row in rows:
            await self.refresh_admission_baseline(row["id"], row["executor_id"])

    async def enqueue(self, executor_id, *, manual, desired_revision=None):
        executor = await self.storage.get_executor_config(executor_id)
        if not executor or not executor.enabled:
            raise ValueError("Executor is missing or disabled")
        targets = await resolve_deployment_targets(
            self.storage, executor, manual=manual, desired_revision=desired_revision
        )
        from .deployment_readiness import snapshot_readiness_profile

        identity = await self.identity(executor)
        trigger_key = None
        if not manual:
            base_key = f"desired:{executor_id}:{desired_revision}:{identity}"
            retries = await self._pre_mutation_failures(base_key)
            trigger_key = base_key if retries == 0 else f"{base_key}:retry{retries}"
        from ..versioning import SELECTION_RULES_VERSION

        payload = {
            "selection_revision": SELECTION_RULES_VERSION,
            "executor_id": executor_id,
            "manual": manual,
            "auto_update_policy": executor.auto_update_policy,
            "policy_bindings": [
                binding.model_dump(mode="json") for binding in executor.service_bindings
            ],
            "config_identity": identity,
            "desired_revision": desired_revision,
            "targets": targets,
            "health_check": await snapshot_readiness_profile(self.storage, executor),
        }
        task = await self.storage.tasks.enqueue(
            kind="deploy",
            resource_key=await mutation_resource_key(self.storage, self.scheduler, executor),
            dedupe_key=f"deploy:{executor_id}",
            target_label=executor.name,
            payload=payload,
            trigger_mode="manual" if manual else "automatic",
            trigger_key=trigger_key,
            max_retries=0,
            join_running=True,
            # Only the current desired state may wait for a maintenance window;
            # older automatic work for this executor is already obsolete.
            supersede_pending=not manual,
        )
        return {"task_id": task["id"], "status": task["state"]}

    async def _pre_mutation_failures(self, base_key):
        """Automatic attempts for this exact desired target that never mutated."""
        db = await self.storage._get_connection()
        row = await (
            await db.execute(
                """SELECT COUNT(DISTINCT t.id) FROM tasks t JOIN task_triggers g ON g.task_id=t.id
                   WHERE (g.trigger_key=? OR g.trigger_key LIKE ? ESCAPE '\\')
                     AND t.kind='deploy' AND t.state='failed'
                     AND json_extract(t.result,'$.mutation_started')=0""",
                (
                    base_key,
                    base_key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    + ":retry%",
                ),
            )
        ).fetchone()
        return int(row[0])

    async def _schedule_pre_mutation_retry(self, task):
        """Retry automatic work only if the runtime was never touched.

        Deployment admission is bound to an unstarted task, so a retry is a new
        task for the same desired revision rather than a second attempt.
        """
        payload = task["payload"]
        if (
            payload.get("manual", True)
            or task["state"] != "failed"
            or (task.get("result") or {}).get("mutation_started") is not False
        ):
            return False
        base_key = f"desired:{payload['executor_id']}:{payload['desired_revision']}:{payload['config_identity']}"
        failures = await self._pre_mutation_failures(base_key)
        if failures > PRE_MUTATION_RETRIES:
            return False
        delay = PRE_MUTATION_RETRY_SECONDS * failures
        now = datetime.now()
        db = await self.storage._get_connection()
        cursor = await db.execute(
            "UPDATE executor_desired_state SET next_eligible_at=?,updated_at=? "
            "WHERE executor_id=? AND desired_state_revision=? AND pending=1",
            (
                (now + timedelta(seconds=delay)).isoformat(),
                now.isoformat(),
                payload["executor_id"],
                payload["desired_revision"],
            ),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def dispatch_pending(self):
        await self._reconcile_baselines()
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
        from ..versioning import SELECTION_RULES_VERSION

        if payload["manual"] and payload.get("selection_revision") != SELECTION_RULES_VERSION:
            # Manual targets were resolved under older rules; re-request instead of
            # leaving an unapprovable plan in the queue.
            return TaskResult("superseded", "selection_rules_changed")
        if not payload["manual"]:
            desired = await self.storage.get_executor_desired_state(executor.id)
            if not desired or desired.desired_state_revision != payload["desired_revision"]:
                return TaskResult("superseded", "target_replaced")
            if executor.update_mode == "manual":
                return TaskResult("skipped", "manual_policy")
            if executor.update_mode == "maintenance_window":
                await self.scheduler._refresh_system_timezone()
                if not self.scheduler._within_maintenance_window(executor.maintenance_window):
                    # A read-only check settles no-op work now instead of
                    # leaving it queued until the window opens.
                    if await self._already_current_now(executor, task):
                        return TaskResult("no_change", "already_current")
                    return Deferred(
                        time.time()
                        + self.scheduler._seconds_until_next_maintenance_window(
                            executor.maintenance_window
                        ),
                        "maintenance_window",
                    )
        if not await self.storage.tasks.bind_mutation_scope(
            task, await mutation_resource_key(self.storage, self.scheduler, executor)
        ):
            return Deferred(time.time() + 30, "target_awaiting_readiness")
        observer = getattr(self.scheduler, "readiness", None)
        if observer is not None and await observer.conflicts(executor):
            return Deferred(time.time() + 5, "target_awaiting_readiness")
        if executor.id in self.scheduler._running_executor_ids:
            return Deferred(time.time() + 5, "executor_running")
        latest = await self.storage.get_latest_executor_run(executor.id)
        if latest and latest.status in {"queued", "running", "health_checking"}:
            return TaskResult("needs_attention", "previous_run_interrupted")
        try:
            evidence = await self._collect_admission_evidence(executor, task)
            if _already_current(executor, evidence, task):
                # Nothing would be written, so there is nothing to review or adopt.
                return TaskResult("no_change", "already_current")
            plan = await self.admission.stage(task, evidence)
        except AdmissionConflict as exc:
            return TaskResult(
                "needs_attention", str(exc), message="deployment admission could not be established"
            )
        except Exception:
            # Remote inspection is read-only and retry-neutral; do not consume a
            # deployment attempt while a target is temporarily unavailable, but
            # never wait silently forever for an unreachable target.
            previous = task.get("result") or {}
            failures = int(previous.get("admission_evidence_failures", 0)) + 1
            if failures >= ADMISSION_EVIDENCE_PATIENCE:
                return TaskResult(
                    "needs_attention",
                    "admission_evidence_stalled",
                    message="target could not be inspected for an extended period",
                )
            return Deferred(
                time.time() + 30,
                "admission_evidence_unavailable",
                result=previous | {"admission_evidence_failures": failures},
            )
        if plan["state"] != "approved":
            return TaskResult(
                "awaiting_approval",
                (
                    "deployment_approval_required"
                    if plan["state"] == "pending"
                    else "deployment_blocked"
                ),
                message=plan["reason"],
                result={"deployment_plan_id": plan["id"], "reason": plan["reason"]},
            )
        return None

    async def _already_current_now(self, executor, task) -> bool:
        try:
            evidence = await self._collect_admission_evidence(executor, task)
        except Exception:
            return False
        return _already_current(executor, evidence, task)

    async def _verify_mutation_window(self, task, executor):
        if not task["payload"]["manual"] and executor.update_mode == "maintenance_window":
            fresh = await self.storage.tasks.get(task["id"])
            if fresh and fresh["result"].get("mutation_started"):
                return  # Never interrupt a multi-step mutation already in progress.
            await self.scheduler._refresh_system_timezone()
            if not self.scheduler._within_maintenance_window(executor.maintenance_window):
                raise ValueError("maintenance window closed before mutation")

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
        admission_plan = await self.admission.latest(task["id"])
        marker_token = None
        if admission_plan is not None and admission_plan["state"] == "approved":
            installation_id = await self.admission.installation_id()
            marker_token = MANAGED_MARKERS.set(
                managed_markers(installation_id, admission_plan["target_id"], task["id"])
            )
        if not await self.storage.tasks.checkpoint(
            task, {"run_id": run_id, "mutation_started": False}
        ):
            raise asyncio.CancelledError("Deployment lease lost")

        from .deployment_diff import EXPECTED_UPDATE_STATE, INSPECTED_UPDATE_STATE, UPDATE_REFUSAL

        write_state_token = EXPECTED_UPDATE_STATE.set(None)
        refusal = {}
        refusal_token = UPDATE_REFUSAL.set(refusal)
        baseline = {}

        async def guard():
            nonlocal baseline
            current = await self.storage.get_executor_config(executor.id)
            if (
                current is None
                or await self.identity(current) != task["payload"]["config_identity"]
            ):
                raise asyncio.CancelledError("Deployment configuration changed")
            if not await self.storage.tasks.bind_mutation_scope(
                task, await mutation_resource_key(self.storage, self.scheduler, executor)
            ):
                raise asyncio.CancelledError("Target requires operator verification")
            # Re-read runtime evidence immediately before the first mutation.
            # The plan fingerprint binds approval to this exact read-only state.
            live_evidence = await self._collect_admission_evidence(executor, task)
            await self.admission.verify_before_write(task, live_evidence)
            EXPECTED_UPDATE_STATE.set(INSPECTED_UPDATE_STATE.get())
            if defer and not baseline:
                baseline = await asyncio.wait_for(
                    capture_deployment_baseline(self.storage, self.scheduler, executor),
                    timeout=executor.health_check.readiness_attempt_timeout_seconds,
                )
                if baseline.get("error"):
                    raise ValueError("readiness_baseline_unavailable")
            await self._verify_mutation_window(task, current)
            if not await self.storage.tasks.checkpoint(
                task,
                {"run_id": run_id, "mutation_started": True},
                private_payload={"readiness_baseline": baseline} if defer else None,
            ):
                raise asyncio.CancelledError("Deployment lease lost")

        async def handoff(config, active_run, **finalization):
            await self._mark_admission_applied(task, executor)
            return await observer.handoff(task, executor, active_run, baseline, **finalization)

        defer_token = DEFER_READINESS.set(defer)
        finalize_token = READINESS_FINALIZER.set(handoff if defer else None)
        mutation_token = MUTATION_GUARD.set(guard)
        token = QUEUED_TARGETS.set(task["payload"]["targets"])
        services = (
            {None}
            if executor.target_ref.get("mode", "container") == "container"
            else {b["service"] for b in task["payload"].get("policy_bindings", [])}
        )
        unchanged = (admission_plan or {}).get("summary", {}).get("unchanged_services", [])
        force_token = FORCE_IMAGE_UPDATES.set(frozenset(services - set(unchanged)))
        try:
            outcome = await self.scheduler._run_executor_with_overlap_guard(
                executor,
                manual=task["payload"]["manual"],
                run_id=run_id,
                desired_state_revision=task["payload"]["desired_revision"],
            )
        finally:
            QUEUED_TARGETS.reset(token)
            FORCE_IMAGE_UPDATES.reset(force_token)
            MUTATION_GUARD.reset(mutation_token)
            EXPECTED_UPDATE_STATE.reset(write_state_token)
            UPDATE_REFUSAL.reset(refusal_token)
            DEFER_READINESS.reset(defer_token)
            READINESS_FINALIZER.reset(finalize_token)
            if marker_token is not None:
                MANAGED_MARKERS.reset(marker_token)
        if refusal.get("blocked"):
            await self.storage.tasks.checkpoint(task, {"mutation_started": False})
            return TaskResult(
                "superseded", "deployment_configuration_changed", result={"run_id": run_id}
            )
        if outcome.status == "health_checking":
            return TaskResult("observing", result={"run_id": run_id})
        if outcome.status in {"success", "skipped"}:
            await self._mark_admission_applied(task, executor)
        checkpoint = await self.storage.tasks.get(task["id"])
        failure = "needs_attention" if checkpoint["result"].get("mutation_started") else "failed"
        state = {"success": "succeeded", "skipped": "skipped"}.get(outcome.status, failure)
        return TaskResult(
            state,
            "deployment_failed" if state in {"failed", "needs_attention"} else None,
            result={"run_id": run_id},
        )

    async def finished(self, task):
        if task["state"] not in {
            "succeeded",
            "skipped",
            "no_change",
            "needs_attention",
            "failed",
            "cancelled",
        }:
            return
        revision = task["payload"].get("desired_revision")
        if revision and await self._schedule_pre_mutation_retry(task):
            return
        if revision:
            await self.storage.complete_executor_desired_state(
                task["payload"]["executor_id"],
                expected_revision=revision,
            )
        elif task["payload"].get("manual") and task["state"] in {
            "succeeded",
            "skipped",
            "no_change",
        }:
            await self._settle_matching_desired_state(task)

    async def _settle_matching_desired_state(self, task):
        """A manual deployment of the pending desired target also fulfils it."""
        executor_id = task["payload"]["executor_id"]
        desired = await self.storage.get_executor_desired_state(executor_id)
        if desired is None or not desired.pending:
            return
        wanted = {
            (
                item.get("tracker_source_id"),
                item.get("channel_name"),
                item.get("deploy_alias"),
                item.get("digest"),
                item.get("chart_version"),
            )
            for item in desired.desired_target.get("binding_targets") or []
        }
        applied = {
            (
                item.get("source_id"),
                item.get("channel"),
                *(item.get("target") or (None, None)),
                item.get("chart_version"),
            )
            for item in task["payload"].get("targets") or []
        }
        if not wanted or wanted != applied:
            return
        revision = desired.desired_state_revision
        await self.storage.complete_executor_desired_state(executor_id, expected_revision=revision)
        await self.storage.tasks.supersede_automatic_deploys(
            executor_id, revision, "target_applied"
        )
