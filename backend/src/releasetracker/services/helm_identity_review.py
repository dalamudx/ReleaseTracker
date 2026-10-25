"""Prove a legacy Helm identity changed only because deployment receipts were persisted."""

import json

from .deployment_baseline import configured_target
from .deployment_plan import PREFIX, fingerprint, plan_fingerprint


def review_fingerprint(task, evidence, review=None):
    value = plan_fingerprint(task, evidence)
    return fingerprint([value, review]) if review else value


async def legacy_helm_review(db, target, evidence, installation_id):
    current = evidence.configuration.get("target")
    if (
        not isinstance(current, dict)
        or current.get("mode") != "helm_release"
        or configured_target(current) != current
        or evidence.ownership_reason(installation_id, target["target_id"]) is not None
        or not target["last_task_id"]
    ):
        return None
    row = await (
        await db.execute(
            """SELECT o.executor_config,t.result,t.state,t.error_code,t.payload
               FROM deployment_observations o JOIN tasks t ON t.id=o.task_id
               JOIN deployment_plans p ON p.task_id=t.id
               WHERE t.id=? AND o.executor_id=? AND p.executor_id=?
                 AND p.target_id=? AND p.identity_key=? AND p.state='applied'
                 AND p.approved_at IS NOT NULL
                 AND t.kind='deploy'
                 AND t.id=(SELECT MAX(id) FROM tasks WHERE kind='deploy'
                   AND json_extract(payload,'$.executor_id')=?
                   AND json_extract(result,'$.mutation_started')=1)
               ORDER BY p.id DESC LIMIT 1""",
            (
                target["last_task_id"],
                target["executor_id"],
                target["executor_id"],
                target["target_id"],
                target["identity_key"],
                target["executor_id"],
            ),
        )
    ).fetchone()
    if row is None:
        return None
    old = json.loads(row["executor_config"]).get("target_ref")
    result = json.loads(row["result"] or "{}")
    if (
        not isinstance(old, dict)
        or configured_target(old) != current
        or not result.get("mutation_started")
        or not result.get("mutation_complete")
        or not (
            row["state"] == "succeeded"
            or row["state"] == "failed"
            and row["error_code"] == "operator_reconciled"
        )
    ):
        return None
    old_identity = fingerprint(
        [evidence.runtime_identity, fingerprint({"mode": "helm_release", "target": old})]
    )
    previous_target = {
        k: v for k, v in old.items() if k not in {"chart_version", "chart_digest", "chart_revision"}
    }
    previous_identity = fingerprint(
        [
            evidence.runtime_identity,
            fingerprint({"mode": "helm_release", "target": previous_target}),
        ]
    )
    if target["identity_key"] not in {old_identity, previous_identity}:
        return None
    if {m.get(PREFIX + "deployment-id") for m in evidence.markers} != {str(target["last_task_id"])}:
        return None
    versions = {t.get("chart_version") for t in json.loads(row["payload"]).get("targets", [])}
    current_version = evidence.configuration.get("current_chart_version")
    if len(versions) != 1 or None in versions or not current_version:
        return None
    review = {
        "previous_identity_key": target["identity_key"],
        "task_id": target["last_task_id"],
        "baseline": target["baseline"],
    }
    if versions != {current_version}:
        # Coordinates and markers prove the release; an outside upgrade is drift for human review.
        review["external_chart_version"] = {"approved": versions.pop(), "current": current_version}
    return review


async def reconciled_helm_review(db, target, evidence, installation_id):
    """A manually settled write may renew its already-approved identity review."""
    current = evidence.configuration.get("target")
    if (
        not isinstance(current, dict)
        or current.get("mode") != "helm_release"
        or configured_target(current) != current
        or not evidence.configuration.get("current_chart_version")
        or evidence.ownership_reason(installation_id, target["target_id"]) is not None
    ):
        return None
    row = await (
        await db.execute(
            """SELECT t.id,t.result,p.identity_key,p.summary FROM tasks t
               JOIN deployment_plans p ON p.task_id=t.id
               WHERE t.id=(SELECT MAX(id) FROM tasks WHERE kind='deploy'
                 AND json_extract(payload,'$.executor_id')=?
                 AND json_extract(result,'$.mutation_started')=1)
                 AND t.state='failed' AND t.error_code='operator_reconciled'
                 AND p.executor_id=? AND p.target_id=? AND p.identity_key=?
                 AND p.state='approved' AND p.approved_at IS NOT NULL
                 AND p.approved_by IS NOT NULL AND p.approved_by!='managed_baseline'
               ORDER BY p.id DESC LIMIT 1""",
            (
                target["executor_id"],
                target["executor_id"],
                target["target_id"],
                target["identity_key"],
            ),
        )
    ).fetchone()
    if row is None:
        return None
    result = json.loads(row["result"] or "{}")
    resolution = result.get("manual_resolution") or {}
    review = json.loads(row["summary"]).get("helm_identity_review")
    if (
        not resolution.get("actor")
        or not resolution.get("at")
        or not review
        or review.get("task_id") != target["last_task_id"]
        or review.get("baseline") != target["baseline"]
    ):
        return None
    original = await (
        await db.execute(
            """SELECT o.executor_config FROM deployment_observations o
               JOIN deployment_plans p ON p.task_id=o.task_id
               WHERE o.task_id=? AND o.executor_id=? AND p.executor_id=?
                 AND p.target_id=? AND p.identity_key=? AND p.state='applied'
                 AND p.approved_at IS NOT NULL LIMIT 1""",
            (
                target["last_task_id"],
                target["executor_id"],
                target["executor_id"],
                target["target_id"],
                review.get("origin_identity_key", review.get("previous_identity_key")),
            ),
        )
    ).fetchone()
    if original is None:
        return None
    old = json.loads(original["executor_config"]).get("target_ref")
    if not isinstance(old, dict) or configured_target(old) != current:
        return None

    def identity(ref):
        return fingerprint(
            [evidence.runtime_identity, fingerprint({"mode": "helm_release", "target": ref})]
        )

    origin_identity = review.get("origin_identity_key", review.get("previous_identity_key"))
    previous_target = {
        k: v for k, v in old.items() if k not in {"chart_version", "chart_digest", "chart_revision"}
    }
    if origin_identity not in {identity(old), identity(previous_target)}:
        return None
    if target["identity_key"] not in {identity(previous_target), identity(current)}:
        return None
    marker_tasks = {m.get(PREFIX + "deployment-id") for m in evidence.markers}
    if marker_tasks not in ({str(target["last_task_id"])}, {str(row["id"])}):
        return None
    return {
        "previous_identity_key": target["identity_key"],
        "task_id": target["last_task_id"],
        "baseline": target["baseline"],
        "last_write_task_id": row["id"],
        "reconciled_task_id": row["id"],
        "origin_identity_key": origin_identity,
    }
