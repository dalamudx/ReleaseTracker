"""Time-point restore is not a worker restart: old authorizations are revoked."""

import json
import time

REVIEW_SETTING = "restore.review_required"


def quarantine_restored_intents(db):
    now = time.time()
    db.execute(
        """UPDATE tasks SET state='cancelled',approval_pending=0,owner=NULL,
        lease_until=NULL,error_code='restore_review_required',message='Intent revoked after backup restore',updated_at=?
        WHERE kind IN ('deploy','recover') AND state IN ('queued','retry_wait','running','needs_attention')""",
        (now,),
    )
    db.execute(
        "UPDATE deployment_plans SET state='cancelled' WHERE state IN ('pending','approved','blocked')"
    )
    db.execute(
        "UPDATE deployment_observations SET state='completed',outcome='superseded' WHERE state!='completed'"
    )
    db.execute(
        "UPDATE executor_run_history SET status='failed',finished_at=?,message='Observation revoked after backup restore' WHERE status IN ('queued','running','health_checking')",
        (time.strftime("%Y-%m-%dT%H:%M:%S"),),
    )
    db.execute(
        "UPDATE task_attempts SET state='interrupted',finished_at=?,error_code='restore_review_required' WHERE finished_at IS NULL AND task_id IN (SELECT id FROM tasks WHERE kind IN ('deploy','recover'))",
        (now,),
    )
    db.execute("DELETE FROM executor_snapshot_claims")
    # A restored desired intent belongs to an earlier authorization timeline.
    db.execute("""UPDATE executor_desired_state SET pending=0,next_eligible_at=NULL,
           claimed_by=NULL,claimed_at=NULL,claim_until=NULL,
           last_completed_revision=desired_state_revision,updated_at=datetime('now')""")
    db.execute(
        "UPDATE executor_notification_intents SET status='expanded',expanded_at=? WHERE status='pending'",
        (now,),
    )
    db.execute(
        "UPDATE deployment_admission_events SET expanded_at=? WHERE expanded_at IS NULL", (now,)
    )
    db.execute(
        "UPDATE source_refresh_requests SET state='ignored',reason='restore_review_required',lease_until=NULL WHERE state IN ('pending','deferred','running')"
    )
    for table in (
        "release_notification_outbox",
        "executor_notification_outbox",
        "deployment_admission_notification_outbox",
    ):
        db.execute(f"UPDATE {table} SET status='discarded' WHERE status IN ('pending','sending')")
    db.execute(
        "INSERT OR REPLACE INTO settings(key,value,updated_at) VALUES (?,?,datetime('now'))",
        (REVIEW_SETTING, json.dumps({"restored_at": now})),
    )
