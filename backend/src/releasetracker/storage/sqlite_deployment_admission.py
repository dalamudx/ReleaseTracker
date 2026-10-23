"""Transactional admission records. Approval queues work; it never touches a runtime."""

from __future__ import annotations

import json
import logging
import time
import uuid

from ..services.version_policy import version_policy_reason
from ..services.deployment_version_evidence import targets_verified
from ..services.deployment_plan import (
    PREFIX,
    TargetEvidence,
    plan_fingerprint,
    public_summary,
)

logger = logging.getLogger(__name__)

APPROVABLE = frozenset(
    {
        "unmanaged",
        "marker_missing",
        "configuration_drift",
        "baseline_review_required",
        "version_policy_requires_approval",
        "version_downgrade_requires_approval",
        "version_unknown_requires_approval",
        "manual_update_review",
    }
)


class AdmissionConflict(ValueError):
    pass


class DeploymentAdmissionStore:
    def __init__(self, storage):
        self.storage = storage

    async def installation_id(self):
        db = await self.storage._get_connection()
        row = await (
            await db.execute(
                "SELECT installation_id FROM managed_deployment_identity WHERE singleton=1"
            )
        ).fetchone()
        if row is None:
            raise AdmissionConflict("installation_identity_missing")
        return row[0]

    async def latest(self, task_id):
        db = await self.storage._get_connection()
        row = await (
            await db.execute(
                "SELECT * FROM deployment_plans WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (task_id,),
            )
        ).fetchone()
        return self._decode(row)

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        result = dict(row)
        result["summary"] = json.loads(result["summary"])
        return result

    @staticmethod
    async def _leased_task(db, task, now, *, allow_started=False):
        if task.get("approval_pending") and task.get("state") in ("queued", "awaiting_approval"):
            row = await (
                await db.execute(
                    "SELECT * FROM tasks WHERE id=? AND kind='deploy' AND approval_pending=1",
                    (task["id"],),
                )
            ).fetchone()
        else:
            row = await (
                await db.execute(
                    "SELECT * FROM tasks WHERE id=? AND kind='deploy' AND state='running' AND owner=? AND lease_until>?",
                    (task["id"], task["owner"], now),
                )
            ).fetchone()
        if row is None:
            raise AdmissionConflict("task_lease_lost")
        if row["payload"] != json.dumps(task["payload"], sort_keys=True):
            # Checkpoint payloads need not use sorted serialization.
            if json.loads(row["payload"]) != task["payload"]:
                raise AdmissionConflict("task_payload_changed")
        if not allow_started and (json.loads(row["result"] or "{}")).get("mutation_started"):
            raise AdmissionConflict("mutation_already_started")
        return row

    async def _ownership_reason(self, db, evidence, installation_id, target_id):
        reason = evidence.ownership_reason(installation_id, target_id)
        if reason != "target_marker_conflict":
            return reason

        # A stale marker is reclaimable only with local evidence of an approved
        # write to this exact runtime/target by an executor that no longer exists.
        # Never infer ownership from a copied marker or a merely previewed plan.
        stale = set()
        for marker in evidence.markers:
            if marker.get("releasetracker.io/managed-by") not in {None, installation_id}:
                return "foreign_owner"
            if marker.get("releasetracker.io/schema") not in {None, "1"}:
                return "marker_schema_unsupported"
            previous_id = marker.get("releasetracker.io/target-id")
            if previous_id is None or previous_id == target_id:
                continue
            if (
                marker.get("releasetracker.io/managed-by") != installation_id
                or marker.get("releasetracker.io/schema") != "1"
            ):
                return reason
            previous = await (
                await db.execute(
                    """SELECT 1 FROM deployment_plans p JOIN tasks t ON t.id=p.task_id
                       WHERE p.target_id=? AND p.identity_key=? AND p.approved_at IS NOT NULL
                       AND json_extract(t.result, '$.mutation_started')=1
                       AND (? IS NULL OR CAST(p.task_id AS TEXT)=?)
                       AND NOT EXISTS (SELECT 1 FROM executors e WHERE e.id=p.executor_id)
                       LIMIT 1""",
                    (
                        previous_id,
                        evidence.identity_key,
                        marker.get("releasetracker.io/deployment-id"),
                        marker.get("releasetracker.io/deployment-id"),
                    ),
                )
            ).fetchone()
            if previous is None:
                return reason
            stale.add(previous_id)
        # Mixed owners in one grouped target must not silently become one claim.
        return "marker_missing" if len(stale) == 1 else reason

    @staticmethod
    async def _restore_lost_baseline(db, target, evidence, now):
        """Restore a lost baseline or complete one missing only Kubernetes Pod digests.

        The latest started deployment must have succeeded under an approved plan
        for this target, with all live markers naming it. An existing baseline
        must match every configuration field except the previously absent digests;
        once completed, changes to those digests are drift again.
        """
        if target["baseline"] is not None and target["baseline"] != evidence.pre_rollout_hash:
            return False
        deployment_ids = {marker.get(PREFIX + "deployment-id") for marker in evidence.markers}
        if len(deployment_ids) != 1 or None in deployment_ids:
            return False
        latest = await (
            await db.execute(
                """SELECT id,state FROM tasks WHERE kind='deploy'
                   AND json_extract(payload, '$.executor_id')=?
                   AND json_extract(result, '$.mutation_started')=1
                   ORDER BY id DESC LIMIT 1""",
                (target["executor_id"],),
            )
        ).fetchone()
        if (
            latest is None
            or latest["state"] != "succeeded"
            or evidence.baseline_task_id != latest["id"]
            or str(latest["id"]) not in deployment_ids
        ):
            return False
        approved = await (
            await db.execute(
                """SELECT state,summary FROM deployment_plans WHERE task_id=? AND executor_id=?
                   AND target_id=? AND identity_key=? AND approved_at IS NOT NULL ORDER BY id DESC LIMIT 1""",
                (
                    latest["id"],
                    target["executor_id"],
                    target["target_id"],
                    evidence.identity_key,
                ),
            )
        ).fetchone()
        if approved is None:
            return False
        if target["baseline"] is None and (
            not evidence.configuration_hash
            or json.loads(approved["summary"]).get("desired_configuration_hash")
            != evidence.configuration_hash
        ):
            return False
        if target["baseline"] is not None and (
            target["last_task_id"] != latest["id"] or approved["state"] != "applied"
        ):
            return False
        cursor = await db.execute(
            """UPDATE managed_targets SET baseline=?,last_task_id=?,updated_at=?
               WHERE target_id=? AND executor_id=? AND baseline IS ?""",
            (
                evidence.evidence_hash,
                latest["id"],
                now,
                target["target_id"],
                target["executor_id"],
                target["baseline"],
            ),
        )
        if cursor.rowcount != 1:
            return False
        logger.info(
            "Restored admission baseline for executor %s from deployment %s",
            target["executor_id"],
            latest["id"],
        )
        return True

    async def stage(self, task, evidence: TargetEvidence, *, now=None, ttl=1800):
        """Before start_attempt: atomically park and emit intent, or authorize a plan.

        The same read-only evidence must be collected again immediately before the
        first write. A copied marker alone never establishes database ownership.
        """
        now = time.time() if now is None else now
        if not 1 <= ttl <= 3600:
            raise ValueError("invalid_approval_lifetime")
        installation_id = await self.installation_id()
        executor_id = task["payload"]["executor_id"]
        plan_hash = plan_fingerprint(task, evidence)
        async with self.storage.tasks.transaction() as db:
            live = await self._leased_task(db, task, now)
            if live["attempts"]:
                raise AdmissionConflict("admission_requires_unstarted_task")
            target = await (
                await db.execute(
                    "SELECT * FROM managed_targets WHERE executor_id=?", (executor_id,)
                )
            ).fetchone()
            occupied = await (
                await db.execute(
                    "SELECT * FROM managed_targets WHERE identity_key=?", (evidence.identity_key,)
                )
            ).fetchone()
            if occupied and occupied["executor_id"] != executor_id:
                active_owner = await (
                    await db.execute(
                        "SELECT 1 FROM executors WHERE id=?", (occupied["executor_id"],)
                    )
                ).fetchone()
                if not active_owner:
                    await db.execute(
                        "DELETE FROM managed_targets WHERE executor_id=?",
                        (occupied["executor_id"],),
                    )
                    occupied = None
            target_id = (
                target["target_id"]
                if target
                else uuid.uuid5(uuid.UUID(installation_id), str(executor_id)).hex
            )
            reason = await self._ownership_reason(db, evidence, installation_id, target_id)
            if occupied and occupied["executor_id"] != executor_id:
                reason = "target_already_owned"
            elif target and target["identity_key"] != evidence.identity_key:
                reason = "target_identity_changed"
            elif reason not in {None, "marker_missing"}:
                pass
            elif (
                target
                and target["baseline"] != evidence.evidence_hash
                and reason is None
                and await self._restore_lost_baseline(db, target, evidence, now)
            ):
                pass
            elif not target or target["baseline"] is None:
                reason = "unmanaged"
            elif reason is None and target["baseline"] != evidence.evidence_hash:
                reason = "configuration_drift"

            if reason is None:
                previous = await (
                    await db.execute(
                        """SELECT result FROM tasks WHERE kind='deploy' AND id<?
                       AND json_extract(payload,'$.executor_id')=?
                       AND json_extract(result,'$.mutation_started')=1 ORDER BY id DESC LIMIT 1""",
                        (task["id"], executor_id),
                    )
                ).fetchone()
                if previous and json.loads(previous["result"] or "{}").get("baseline_status") in {
                    "pending",
                    "review_required",
                    "needs_attention",
                }:
                    reason = "baseline_review_required"

            policy_reason = version_policy_reason(task, evidence)
            if policy_reason and reason in {None, "unmanaged", "configuration_drift"}:
                reason = policy_reason
            if evidence.configuration_diff is not None:
                if evidence.configuration_diff.get("truncated"):
                    reason = "configuration_diff_incomplete"
                elif task["payload"].get("manual") and reason is None:
                    reason = "manual_update_review"

            from ..versioning import SELECTION_RULES_VERSION

            # Automatic work is bound to its desired revision, which only moves with
            # deployment coordinates. Manual targets were resolved by the rules
            # in force when they were requested.
            if (
                task["payload"].get("manual")
                and task["payload"].get("selection_revision") != SELECTION_RULES_VERSION
            ):
                reason = "selection_rules_changed"

            old = await (
                await db.execute(
                    "SELECT * FROM deployment_plans WHERE task_id=? ORDER BY id DESC LIMIT 1",
                    (task["id"],),
                )
            ).fetchone()
            if old and (
                old["fingerprint"] != plan_hash
                or old["state"] in {"superseded", "cancelled"}
                or old["expires_at"] <= now
            ):
                old = None
            # Preserve a target-id reserved by a prior approval preview.
            if not target and old:
                target_id = old["target_id"]
                marker_reason = await self._ownership_reason(
                    db, evidence, installation_id, target_id
                )
                if marker_reason not in {None, "marker_missing"}:
                    reason = marker_reason
            blocked = reason is not None and reason not in APPROVABLE
            approved = bool(
                old
                and old["state"] == "approved"
                and old["expires_at"] > now
                and not blocked
                and (
                    not (policy_reason or reason == "baseline_review_required")
                    or old["approved_by"] != "managed_baseline"
                )
            )
            authorized = reason is None or approved
            state = "approved" if authorized else ("blocked" if blocked else "pending")
            if old and old["state"] == "applied":
                raise AdmissionConflict("plan_already_applied")
            await db.execute(
                "UPDATE deployment_plans SET state='superseded' WHERE task_id=? AND id!=? AND state IN ('pending','approved')",
                (task["id"], old["id"] if old else -1),
            )
            summary = public_summary(task, evidence)
            if old:
                plan_id = old["id"]
                expires_at = old["expires_at"] if approved else now + ttl
                await db.execute(
                    "UPDATE deployment_plans SET state=?,reason=?,expires_at=?,approved_at=?,approved_by=? WHERE id=?",
                    (
                        state,
                        reason or "managed",
                        expires_at,
                        old["approved_at"] if approved else (now if authorized else None),
                        (
                            old["approved_by"]
                            if approved
                            else ("managed_baseline" if authorized else None)
                        ),
                        plan_id,
                    ),
                )
            else:
                cursor = await db.execute(
                    """INSERT INTO deployment_plans(task_id,executor_id,target_id,fingerprint,identity_key,evidence_hash,
                       summary,state,reason,created_at,expires_at,approved_at,approved_by)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        task["id"],
                        executor_id,
                        target_id,
                        plan_hash,
                        evidence.identity_key,
                        evidence.evidence_hash,
                        json.dumps(summary),
                        state,
                        reason or "managed",
                        now,
                        now + ttl,
                        now if authorized else None,
                        "managed_baseline" if authorized else None,
                    ),
                )
                plan_id = cursor.lastrowid
            if not authorized:
                await db.execute(
                    """UPDATE tasks SET state='queued',approval_pending=1,owner=NULL,lease_until=NULL,
                       updated_at=?,error_code=?,result=? WHERE id=?""",
                    (
                        now,
                        reason,
                        json.dumps({"mutation_started": False, "deployment_plan_id": plan_id}),
                        task["id"],
                    ),
                )
                event = "executor_deployment_blocked" if blocked else "executor_approval_required"
                await db.execute(
                    "INSERT OR IGNORE INTO deployment_admission_events(plan_id,event,payload,created_at,due_at) VALUES (?,?,?,?,?)",
                    (
                        plan_id,
                        event,
                        json.dumps(
                            {
                                "task_id": task["id"],
                                "executor_id": executor_id,
                                "executor_name": task["target_label"],
                                "reason": reason,
                                "plan_id": plan_id,
                            }
                        ),
                        now,
                        now,
                    ),
                )
            elif live["approval_pending"]:
                # A refreshed parked plan that no longer needs review (for example a
                # restored baseline) leaves the approval queue instead of waiting.
                await db.execute(
                    "UPDATE tasks SET approval_pending=0,due_at=?,updated_at=?,error_code=NULL WHERE id=?",
                    (now, now, task["id"]),
                )
            row = await (
                await db.execute("SELECT * FROM deployment_plans WHERE id=?", (plan_id,))
            ).fetchone()
        return self._decode(row)

    async def approve(
        self, task_id, plan_id, expected_fingerprint, actor, *, now=None, not_before=None
    ):
        """Authenticated explicit approval, CAS-bound to the exact unexpired plan."""
        now = time.time() if now is None else now
        if not_before is not None and not now <= not_before <= now + 8 * 86400:
            raise AdmissionConflict("invalid_approval_window")
        if not actor:
            raise AdmissionConflict("approval_actor_required")
        async with self.storage.tasks.transaction() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM deployment_plans WHERE id=? AND task_id=?", (plan_id, task_id)
                )
            ).fetchone()
            task = await (await db.execute("SELECT * FROM tasks WHERE id=?", (task_id,))).fetchone()
            if (
                row is None
                or task is None
                or row["fingerprint"] != expected_fingerprint
                or row["expires_at"] <= now
            ):
                raise AdmissionConflict("approval_stale_or_blocked")
            latest = await (
                await db.execute("SELECT MAX(id) FROM deployment_plans WHERE task_id=?", (task_id,))
            ).fetchone()
            if latest[0] != plan_id:
                raise AdmissionConflict("approval_stale_or_blocked")
            if row["state"] == "approved" and task["state"] in {"queued", "running"}:
                return self._decode(row)
            if row["reason"] not in APPROVABLE:
                raise AdmissionConflict("approval_stale_or_blocked")

            if (
                row["state"] != "pending"
                or task["state"] != "queued"
                or not task["approval_pending"]
            ):
                raise AdmissionConflict("task_not_awaiting_approval")
            # Reserve identity and executor ownership in the same transaction. Never
            # transfer another executor's claim, even after its original task ended.
            target = await (
                await db.execute(
                    "SELECT * FROM managed_targets WHERE identity_key=? OR executor_id=?",
                    (row["identity_key"], row["executor_id"]),
                )
            ).fetchall()
            if any(t["target_id"] != row["target_id"] for t in target):
                raise AdmissionConflict("target_already_owned")
            await db.execute(
                "INSERT OR IGNORE INTO managed_targets(target_id,executor_id,identity_key,created_at,updated_at) VALUES (?,?,?,?,?)",
                (row["target_id"], row["executor_id"], row["identity_key"], now, now),
            )
            await db.execute(
                "UPDATE deployment_plans SET state='approved',approved_at=?,approved_by=?,expires_at=? WHERE id=?",
                (
                    now,
                    actor,
                    max(row["expires_at"], not_before + 1800) if not_before else row["expires_at"],
                    plan_id,
                ),
            )
            await db.execute(
                "UPDATE tasks SET approval_pending=0,due_at=?,updated_at=?,error_code=NULL WHERE id=?",
                (max(now, not_before or now), now, task_id),
            )
            return self._decode(
                await (
                    await db.execute("SELECT * FROM deployment_plans WHERE id=?", (plan_id,))
                ).fetchone()
            )

    async def verify_before_write(self, task, evidence, *, now=None):
        """Read-only authorization check; caller persists mutation checkpoint next.

        Runtime adapters must also enforce native concurrency preconditions in the
        write itself. This check does not pretend to lock external controllers.
        """
        now = time.time() if now is None else now
        async with self.storage.tasks.transaction() as db:
            await self._leased_task(db, task, now)
            row = await (
                await db.execute(
                    "SELECT * FROM deployment_plans WHERE task_id=? ORDER BY id DESC LIMIT 1",
                    (task["id"],),
                )
            ).fetchone()
            if (
                row is None
                or row["state"] != "approved"
                or row["expires_at"] <= now
                or row["fingerprint"] != plan_fingerprint(task, evidence)
            ):
                raise AdmissionConflict("deployment_plan_changed")
            target = await (
                await db.execute(
                    "SELECT * FROM managed_targets WHERE target_id=? AND executor_id=? AND identity_key=?",
                    (row["target_id"], row["executor_id"], evidence.identity_key),
                )
            ).fetchone()
            if target is None:
                raise AdmissionConflict("managed_target_missing")
            reason = await self._ownership_reason(
                db, evidence, await self.installation_id(), row["target_id"]
            )
            if reason not in {None, "marker_missing"}:
                raise AdmissionConflict(reason)
            return self._decode(row)

    async def mark_applied(self, task, evidence: TargetEvidence, *, now=None):
        """Commit the post-mutation evidence as the new managed baseline."""
        now = time.time() if now is None else now
        async with self.storage.tasks.transaction() as db:
            await self._leased_task(db, task, now, allow_started=True)
            row = await (
                await db.execute(
                    "SELECT * FROM deployment_plans WHERE task_id=? ORDER BY id DESC LIMIT 1",
                    (task["id"],),
                )
            ).fetchone()
            if row is None or row["state"] != "approved":
                raise AdmissionConflict("deployment_plan_not_approvable")
            if row["identity_key"] != evidence.identity_key:
                raise AdmissionConflict("managed_target_changed")
            expected = json.loads(row["summary"]).get("desired_configuration_hash")
            if (
                not expected
                or expected != evidence.configuration_hash
                or not targets_verified(task, evidence)
            ):
                raise AdmissionConflict("deployment_result_unverified")
            await db.execute(
                "UPDATE deployment_plans SET state='applied',applied_at=? WHERE id=?",
                (now, row["id"]),
            )
            await db.execute(
                "UPDATE managed_targets SET baseline=?,last_task_id=?,updated_at=? WHERE target_id=? AND executor_id=?",
                (evidence.evidence_hash, task["id"], now, row["target_id"], row["executor_id"]),
            )
            updated = await (
                await db.execute("SELECT * FROM deployment_plans WHERE id=?", (row["id"],))
            ).fetchone()
            return self._decode(updated)

    async def refresh_applied(self, task_id, evidence: TargetEvidence, *, now=None):
        """Commit approved evidence, preserving why completion cannot proceed."""
        from ..services.deployment_baseline import BaselineResult

        now = time.time() if now is None else now
        async with self.storage.tasks.transaction() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM deployment_plans WHERE task_id=? ORDER BY id DESC LIMIT 1",
                    (task_id,),
                )
            ).fetchone()
            if row is None:
                return BaselineResult("review_required", "plan_missing")
            original = await (
                await db.execute("SELECT payload,state FROM tasks WHERE id=?", (task_id,))
            ).fetchone()
            latest = await (
                await db.execute(
                    """SELECT id,state FROM tasks WHERE kind='deploy'
                   AND json_extract(payload,'$.executor_id')=?
                   AND json_extract(result,'$.mutation_started')=1 ORDER BY id DESC LIMIT 1""",
                    (row["executor_id"],),
                )
            ).fetchone()
            managed = await (
                await db.execute(
                    "SELECT * FROM managed_targets WHERE target_id=? AND executor_id=?",
                    (row["target_id"], row["executor_id"]),
                )
            ).fetchone()
            if (
                (latest and latest["id"] > task_id)
                or (managed and (managed["last_task_id"] or 0) > task_id)
                or row["state"] in {"superseded", "cancelled"}
            ):
                return BaselineResult("superseded", "newer_deployment")
            if original is None or original["state"] not in {"succeeded", "skipped"}:
                return BaselineResult("review_required", "deployment_not_successful")
            if row["state"] not in {"approved", "applied"}:
                return BaselineResult("review_required", "plan_not_approved")
            if row["identity_key"] != evidence.identity_key:
                return BaselineResult("review_required", "target_identity_changed")
            task = {"payload": json.loads(original["payload"])}
            expected = json.loads(row["summary"]).get("desired_configuration_hash")
            if expected:
                if evidence.configuration_hash != expected:
                    return BaselineResult("review_required", "configuration_changed")
            elif not (
                managed
                and evidence.pre_rollout_hash
                and managed["baseline"] == evidence.pre_rollout_hash
                and managed["last_task_id"] == task_id
            ):
                return BaselineResult("review_required", "legacy_plan_missing_proof")
            if not targets_verified(task, evidence):
                return BaselineResult("pending", "artifact_unverified")
            deployment_ids = {marker.get(PREFIX + "deployment-id") for marker in evidence.markers}
            if deployment_ids - {None, str(task_id)}:
                return BaselineResult("review_required", "deployment_marker_changed")
            reason = await self._ownership_reason(
                db, evidence, await self.installation_id(), row["target_id"]
            )
            if reason is not None:
                return BaselineResult("review_required", reason)
            if row["state"] == "approved":
                if (
                    latest is None
                    or latest["id"] != task_id
                    or latest["state"] not in {"succeeded", "skipped"}
                    or deployment_ids != {str(task_id)}
                    or row["approved_at"] is None
                ):
                    return BaselineResult("review_required", "deployment_receipt_missing")
                cursor = await db.execute(
                    """UPDATE managed_targets SET baseline=?,last_task_id=?,updated_at=?
                       WHERE target_id=? AND executor_id=? AND identity_key=?
                       AND (last_task_id IS NULL OR last_task_id<=?)""",
                    (
                        evidence.evidence_hash,
                        task_id,
                        now,
                        row["target_id"],
                        row["executor_id"],
                        evidence.identity_key,
                        task_id,
                    ),
                )
                if cursor.rowcount != 1:
                    return BaselineResult("review_required", "target_identity_changed")
                await db.execute(
                    "UPDATE deployment_plans SET state='applied',applied_at=? WHERE id=?",
                    (now, row["id"]),
                )
                return BaselineResult("verified")
            cursor = await db.execute(
                """UPDATE managed_targets SET baseline=?,updated_at=?
                   WHERE target_id=? AND executor_id=? AND identity_key=? AND last_task_id=?""",
                (
                    evidence.evidence_hash,
                    now,
                    row["target_id"],
                    row["executor_id"],
                    evidence.identity_key,
                    task_id,
                ),
            )
            return (
                BaselineResult("verified")
                if cursor.rowcount == 1
                else BaselineResult("review_required", "baseline_owner_changed")
            )
