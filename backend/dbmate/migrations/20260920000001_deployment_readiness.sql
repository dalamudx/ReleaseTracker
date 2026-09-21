-- migrate:up
CREATE TABLE deployment_observations (
    task_id INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    run_id INTEGER NOT NULL REFERENCES executor_run_history(id) ON DELETE CASCADE,
    executor_id INTEGER NOT NULL,
    resource_scope TEXT NOT NULL,
    executor_config TEXT NOT NULL,
    verification TEXT NOT NULL,
    finalization TEXT NOT NULL,
    started_at REAL NOT NULL,
    deadline REAL NOT NULL,
    due_at REAL NOT NULL,
    stable_since REAL,
    state TEXT NOT NULL DEFAULT 'waiting' CHECK(state IN ('waiting','finalizing','completed','blocked')),
    outcome TEXT,
    result TEXT,
    finished_at REAL
);
CREATE INDEX deployment_observations_due ON deployment_observations(state,due_at);
INSERT OR IGNORE INTO settings(key,value,updated_at) VALUES
('system.readiness_timeout_seconds','600',strftime('%Y-%m-%dT%H:%M:%f','now')),
('system.readiness_interval_seconds','5',strftime('%Y-%m-%dT%H:%M:%f','now')),
('system.readiness_attempt_timeout_seconds','10',strftime('%Y-%m-%dT%H:%M:%f','now')),
('system.readiness_stable_seconds','10',strftime('%Y-%m-%dT%H:%M:%f','now'));

-- migrate:down
DROP TABLE deployment_observations;
DELETE FROM settings WHERE key IN ('system.readiness_timeout_seconds','system.readiness_interval_seconds','system.readiness_attempt_timeout_seconds','system.readiness_stable_seconds');
