-- migrate:up
PRAGMA foreign_keys=OFF;
CREATE TABLE source_fetch_runs_webhook (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracker_source_id INTEGER NOT NULL,
    trigger_mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    filtered_in_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (tracker_source_id) REFERENCES aggregate_tracker_sources(id) ON DELETE CASCADE,
    CHECK (trigger_mode IN ('scheduled', 'manual', 'bootstrap', 'webhook')),
    CHECK (status IN ('running', 'success', 'partial', 'failed'))
);
INSERT INTO source_fetch_runs_webhook SELECT * FROM source_fetch_runs;
DROP TABLE source_fetch_runs;
ALTER TABLE source_fetch_runs_webhook RENAME TO source_fetch_runs;
CREATE INDEX idx_source_fetch_runs_tracker_source_id ON source_fetch_runs(tracker_source_id);
CREATE INDEX idx_source_fetch_runs_tracker_source_started_at ON source_fetch_runs(tracker_source_id, started_at DESC);
CREATE TABLE repository_webhooks (
    id TEXT PRIMARY KEY,
    tracker_source_id INTEGER NOT NULL UNIQUE REFERENCES aggregate_tracker_sources(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    auth_mode TEXT NOT NULL,
    secret TEXT NOT NULL,
    config TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE webhook_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    webhook_id TEXT NOT NULL REFERENCES repository_webhooks(id) ON DELETE CASCADE,
    delivery_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    summary TEXT NOT NULL,
    state TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    duplicates INTEGER NOT NULL DEFAULT 0,
    received_at REAL NOT NULL,
    UNIQUE(webhook_id, delivery_key)
);
CREATE INDEX idx_webhook_deliveries_received ON webhook_deliveries(webhook_id, received_at DESC);
CREATE TABLE source_refresh_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id INTEGER NOT NULL REFERENCES webhook_deliveries(id) ON DELETE CASCADE,
    tracker_source_id INTEGER NOT NULL REFERENCES aggregate_tracker_sources(id) ON DELETE CASCADE,
    webhook_generation INTEGER NOT NULL,
    source_identity TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    due_at REAL NOT NULL,
    lease_until REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    source_fetch_run_id INTEGER REFERENCES source_fetch_runs(id) ON DELETE SET NULL,
    UNIQUE(delivery_id, tracker_source_id)
);
CREATE INDEX idx_source_refresh_due ON source_refresh_requests(state, due_at);

PRAGMA foreign_keys=ON;

-- migrate:down
PRAGMA foreign_keys=OFF;
DROP TABLE source_refresh_requests;
DROP TABLE webhook_deliveries;
DROP TABLE repository_webhooks;
CREATE TABLE source_fetch_runs_previous (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracker_source_id INTEGER NOT NULL,
    trigger_mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    filtered_in_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (tracker_source_id) REFERENCES aggregate_tracker_sources(id) ON DELETE CASCADE,
    CHECK (trigger_mode IN ('scheduled', 'manual', 'bootstrap')),
    CHECK (status IN ('running', 'success', 'partial', 'failed'))
);
INSERT INTO source_fetch_runs_previous SELECT id, tracker_source_id, CASE WHEN trigger_mode='webhook' THEN 'manual' ELSE trigger_mode END, started_at, finished_at, status, error_message, fetched_count, filtered_in_count, created_at FROM source_fetch_runs;
DROP TABLE source_fetch_runs;
ALTER TABLE source_fetch_runs_previous RENAME TO source_fetch_runs;
CREATE INDEX idx_source_fetch_runs_tracker_source_id ON source_fetch_runs(tracker_source_id);
CREATE INDEX idx_source_fetch_runs_tracker_source_started_at ON source_fetch_runs(tracker_source_id, started_at DESC);
PRAGMA foreign_keys=ON;
