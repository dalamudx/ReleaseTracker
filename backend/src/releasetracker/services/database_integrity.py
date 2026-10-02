"""Read-only relational checks. SQLite integrity_check alone does not check FKs.

Foreign key enforcement remains a staged migration: reject invalid recovery
points now, rather than silently cascading deletes in legacy live databases.
"""


def validate_relations(db):
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Backup database foreign key check failed")
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"deployment_observations", "tasks", "executor_run_history"} <= tables:
        return  # Older schema, checked separately against migration versions.
    invalid = db.execute("""
        SELECT 1 FROM deployment_observations o
        LEFT JOIN tasks t ON t.id=o.task_id
        LEFT JOIN executor_run_history h ON h.id=o.run_id
        WHERE t.id IS NULL OR h.id IS NULL OR h.executor_id!=o.executor_id
        OR (o.state IN ('waiting','finalizing') AND t.state!='running') LIMIT 1
    """).fetchone()
    if invalid:
        raise ValueError("Backup deployment observation relations are inconsistent")
