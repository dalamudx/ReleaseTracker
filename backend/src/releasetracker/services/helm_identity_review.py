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
    if old_identity != target["identity_key"]:
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
