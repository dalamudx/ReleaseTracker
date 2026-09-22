"""Transactional admission records. Approval queues work; it never touches a runtime."""

from __future__ import annotations

import json
import time
import uuid

from ..services.deployment_plan import TargetEvidence, plan_fingerprint, public_summary

APPROVABLE = frozenset({"unmanaged", "marker_missing", "configuration_drift"})


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
    async def _leased_task(db, task, now):
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
        if (json.loads(row["result"] or "{}")).get("mutation_started"):
            raise AdmissionConflict("mutation_already_started")
        return row

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
            target_id = (
                target["target_id"]
                if target
                else uuid.uuid5(uuid.UUID(installation_id), str(executor_id)).hex
            )
            reason = evidence.ownership_reason(installation_id, target_id)
            if occupied and occupied["executor_id"] != executor_id:
                reason = "target_already_owned"
            elif target and target["identity_key"] != evidence.identity_key:
                reason = "target_identity_changed"
            elif reason not in {None, "marker_missing"}:
                pass
            elif not target or target["baseline"] is None:
                reason = "unmanaged"
            elif reason is None and target["baseline"] != evidence.evidence_hash:
                reason = "configuration_drift"

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
                marker_reason = evidence.ownership_reason(installation_id, target_id)
                if marker_reason not in {None, "marker_missing"}:
                    reason = marker_reason
            blocked = reason is not None and reason not in APPROVABLE
            approved = bool(
                old and old["state"] == "approved" and old["expires_at"] > now and not blocked
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
            row = await (
                await db.execute("SELECT * FROM deployment_plans WHERE id=?", (plan_id,))
            ).fetchone()
        return self._decode(row)

    async def approve(self, task_id, plan_id, expected_fingerprint, actor, *, now=None):
        """Authenticated explicit approval, CAS-bound to the exact unexpired plan."""
        now = time.time() if now is None else now
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
                or row["reason"] not in APPROVABLE
            ):
                raise AdmissionConflict("approval_stale_or_blocked")
            latest = await (
                await db.execute("SELECT MAX(id) FROM deployment_plans WHERE task_id=?", (task_id,))
            ).fetchone()
            if latest[0] != plan_id:
                raise AdmissionConflict("approval_stale_or_blocked")
            if row["state"] == "approved" and task["state"] in {"queued", "running"}:
                return self._decode(row)

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
                "UPDATE deployment_plans SET state='approved',approved_at=?,approved_by=? WHERE id=?",
                (now, actor, plan_id),
            )
            await db.execute(
                "UPDATE tasks SET approval_pending=0,due_at=?,updated_at=?,error_code=NULL WHERE id=?",
                (now, now, task_id),
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
            return self._decode(row)

    async def mark_applied(self, task, evidence: TargetEvidence, *, now=None):
        """Commit the post-mutation evidence as the new managed baseline."""
        now = time.time() if now is None else now
        async with self.storage.tasks.transaction() as db:
            await self._leased_task(db, task, now)
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
