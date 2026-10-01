"""Low-cardinality Prometheus gauges computed from retained operational evidence.

No credentials, URLs, task messages, target names or unbounded IDs are labels.
Historical values are gauges, not monotonic counters: retention can reduce them.
"""

import json
from datetime import datetime, timedelta
import time

KINDS = ("fetch", "deploy", "recover")
STATES = (
    "queued",
    "running",
    "retry_wait",
    "succeeded",
    "no_change",
    "skipped",
    "failed",
    "cancelled",
    "superseded",
    "needs_attention",
)


async def render_metrics(storage, backup=None):
    db = await storage._get_connection()
    now = time.time()
    lines = []

    def declare(name, help_text):
        lines.extend(
            [f"# HELP releasetracker_{name} {help_text}", f"# TYPE releasetracker_{name} gauge"]
        )

    def sample(name, value, **labels):
        suffix = (
            "{" + ",".join(f"{k}={json.dumps(v)}" for k, v in labels.items()) + "}"
            if labels
            else ""
        )
        lines.append(f"releasetracker_{name}{suffix} {value}")

    # One read transaction gives every family the same SQLite snapshot.
    await db.execute("BEGIN")
    try:
        rows = await (
            await db.execute(
                "SELECT kind,state,COUNT(*),MIN(created_at) FROM tasks GROUP BY kind,state"
            )
        ).fetchall()
        counts = {(r[0], r[1]): (r[2], r[3]) for r in rows}
        declare("tasks", "Retained tasks by kind and state, including dismissed tasks.")
        for kind in KINDS:
            for state in STATES:
                sample("tasks", counts.get((kind, state), (0, 0))[0], kind=kind, state=state)
        declare(
            "task_oldest_pending_seconds",
            "Age of oldest queued or retrying task; includes intentional maintenance waits.",
        )
        for kind in KINDS:
            timestamps = [
                counts[(kind, state)][1]
                for state in ("queued", "retry_wait")
                if (kind, state) in counts
            ]
            sample(
                "task_oldest_pending_seconds",
                max(0, now - min(timestamps)) if timestamps else 0,
                kind=kind,
            )
        declare("task_attempts_24h", "Completed attempts in the last 24 hours.")
        declare(
            "task_attempt_duration_seconds_24h",
            "Sum of completed attempt durations in the last 24 hours.",
        )
        rows = await (
            await db.execute(
                "SELECT t.kind,COUNT(*),SUM(MAX(0,a.finished_at-a.started_at)) FROM task_attempts a JOIN tasks t ON t.id=a.task_id WHERE a.finished_at>=? GROUP BY t.kind",
                (now - 86400,),
            )
        ).fetchall()
        attempts = {r[0]: (r[1], r[2]) for r in rows}
        for kind in KINDS:
            count, duration = attempts.get(kind, (0, 0))
            sample("task_attempts_24h", count, kind=kind)
            sample("task_attempt_duration_seconds_24h", duration, kind=kind)
        declare("fetch_runs_24h", "Completed source fetch runs in the last 24 hours, by result.")
        cutoff = (datetime.now() - timedelta(days=1)).isoformat()
        rows = await (
            await db.execute(
                "SELECT status,COUNT(*) FROM source_fetch_runs WHERE finished_at>=? GROUP BY status",
                (cutoff,),
            )
        ).fetchall()
        fetch_counts = {r[0]: r[1] for r in rows}
        for status in ("success", "partial", "failed", "interrupted"):
            sample("fetch_runs_24h", fetch_counts.get(status, 0), status=status)
        declare(
            "task_errors_24h",
            "Attempts with an error in the last 24 hours; unknown codes are grouped.",
        )
        codes = (
            "security_validation_failed",
            "upstream_tls_failed",
            "upstream_timeout",
            "upstream_unreachable",
            "other",
        )
        rows = await (
            await db.execute(
                "SELECT t.kind,a.error_code,COUNT(*) FROM task_attempts a JOIN tasks t ON t.id=a.task_id WHERE a.finished_at>=? AND a.error_code IS NOT NULL GROUP BY t.kind,a.error_code",
                (now - 86400,),
            )
        ).fetchall()
        errors = {}
        for kind, code, count in rows:
            key = (kind, code if code in codes else "other")
            errors[key] = errors.get(key, 0) + count
        for kind in KINDS:
            for code in codes:
                sample("task_errors_24h", errors.get((kind, code), 0), kind=kind, code=code)
        declare("notification_outbox", "Retained notification outbox entries by queue and status.")
        declare(
            "notification_oldest_pending_seconds", "Age of oldest pending or sending notification."
        )
        for queue, table in (
            ("executor", "executor_notification_outbox"),
            ("admission", "deployment_admission_notification_outbox"),
        ):
            rows = await (
                await db.execute(
                    f"SELECT status,COUNT(*),MIN(created_at) FROM {table} GROUP BY status"
                )
            ).fetchall()
            counts = {r[0]: (r[1], r[2]) for r in rows}
            for status in ("pending", "sending", "delivered", "failed", "discarded"):
                sample(
                    "notification_outbox", counts.get(status, (0, 0))[0], queue=queue, status=status
                )
            timestamps = [counts[s][1] for s in ("pending", "sending") if s in counts]
            sample(
                "notification_oldest_pending_seconds",
                max(0, now - min(timestamps)) if timestamps else 0,
                queue=queue,
            )
    finally:
        await db.rollback()
    declare(
        "backup_last_success_timestamp_seconds",
        "Last successful backup in this process, or zero if none.",
    )
    sample("backup_last_success_timestamp_seconds", backup.last_success if backup else 0)
    declare(
        "backup_failures",
        "Failed backup requests in this process, including busy or invalid requests.",
    )
    sample("backup_failures", backup.failures if backup else 0)
    return "\n".join(lines) + "\n"
