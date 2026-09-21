-- migrate:up
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('fetch', 'deploy', 'recover')),
    resource_key TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    target_label TEXT NOT NULL,
    payload TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN (
        'queued','running','retry_wait','succeeded','no_change','skipped',
        'failed','cancelled','superseded','needs_attention'
    )),
    max_retries INTEGER NOT NULL CHECK(max_retries BETWEEN 0 AND 10),
    attempts INTEGER NOT NULL DEFAULT 0,
    due_at REAL NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    owner TEXT,
    lease_until REAL,
    error_code TEXT,
    message TEXT,
    result TEXT
);
CREATE INDEX tasks_dispatch ON tasks(kind,state,due_at,id);
CREATE INDEX tasks_resource ON tasks(resource_key,state);
CREATE INDEX tasks_pending_dedupe ON tasks(dedupe_key)
    WHERE state IN ('queued','retry_wait');
CREATE UNIQUE INDEX tasks_running_resource ON tasks(resource_key)
    WHERE state='running';
CREATE TABLE task_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    attempt INTEGER NOT NULL,
    owner TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    state TEXT NOT NULL,
    error_code TEXT,
    message TEXT,
    result TEXT,
    UNIQUE(task_id, attempt)
);
CREATE TABLE task_triggers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    trigger_mode TEXT NOT NULL,
    trigger_key TEXT UNIQUE,
    created_at REAL NOT NULL
);
CREATE INDEX task_triggers_task ON task_triggers(task_id);
INSERT OR IGNORE INTO settings(key,value,updated_at)
VALUES ('system.fetch_retry_count','3',strftime('%Y-%m-%dT%H:%M:%f','now'));

ALTER TABLE source_refresh_requests ADD COLUMN task_id INTEGER REFERENCES tasks(id);
CREATE INDEX source_refresh_task ON source_refresh_requests(task_id);

-- migrate:down
DROP INDEX source_refresh_task;
ALTER TABLE source_refresh_requests DROP COLUMN task_id;
DROP TABLE task_triggers;
DROP TABLE task_attempts;
DROP TABLE tasks;
DELETE FROM settings WHERE key='system.fetch_retry_count';
