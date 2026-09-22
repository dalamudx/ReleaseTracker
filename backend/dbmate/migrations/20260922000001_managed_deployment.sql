-- migrate:up
-- Keep the original queue state CHECK/FKs intact: a parked deployment is queued
-- with approval_pending=1; storage/API expose it as awaiting_approval.
ALTER TABLE tasks ADD COLUMN approval_pending INTEGER NOT NULL DEFAULT 0 CHECK(approval_pending IN (0,1));
CREATE TABLE managed_deployment_identity (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    installation_id TEXT NOT NULL
);
INSERT INTO managed_deployment_identity VALUES (1, lower(hex(randomblob(16))));
CREATE TABLE managed_targets (
    target_id TEXT PRIMARY KEY,
    executor_id INTEGER NOT NULL UNIQUE,
    identity_key TEXT NOT NULL UNIQUE,
    baseline TEXT,
    last_task_id INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE deployment_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    executor_id INTEGER NOT NULL,
    target_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    summary TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','approved','applied','superseded','blocked','cancelled')),
    reason TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    approved_at REAL,
    approved_by TEXT,
    applied_at REAL
);
CREATE INDEX deployment_plans_task ON deployment_plans(task_id,id);
CREATE TABLE deployment_admission_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES deployment_plans(id),
    event TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    expanded_at REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    due_at REAL NOT NULL,
    UNIQUE(plan_id,event)
);

-- migrate:down
-- Never remove admission while its writes may still be in flight/uncertain.
CREATE TEMP TABLE admission_down_guard (active INTEGER CHECK(active=0));
INSERT INTO admission_down_guard SELECT COUNT(*) FROM tasks t
WHERE t.state IN ('running','needs_attention') AND EXISTS (
    SELECT 1 FROM deployment_plans p WHERE p.task_id=t.id
);
DROP TABLE admission_down_guard;
-- Neither pending nor approved work may silently lose its write-time checks.
UPDATE tasks SET state='cancelled',error_code='admission_schema_downgraded'
WHERE state IN ('queued','retry_wait') AND (approval_pending=1 OR EXISTS (
    SELECT 1 FROM deployment_plans p WHERE p.task_id=tasks.id
));
DROP TABLE deployment_admission_events;
DROP TABLE deployment_plans;
DROP TABLE managed_targets;
DROP TABLE managed_deployment_identity;
ALTER TABLE tasks DROP COLUMN approval_pending;
