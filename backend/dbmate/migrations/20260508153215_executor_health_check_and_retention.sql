-- migrate:up

-- Add per-executor health check profile. The default JSON value represents
-- the zero-change "strategy=none" profile so existing executors behave
-- identically to pre-feature behavior until an operator opts in.
ALTER TABLE executors
ADD COLUMN health_check TEXT NOT NULL DEFAULT '{"strategy":"none","use_default_strategy":false,"failure_policy":"mark_failed","grace_period_seconds":0,"attempt_timeout_seconds":0,"interval_seconds":0,"probe_window_seconds":0,"services":null,"http":null,"tcp":null}';

-- Seed the snapshot retention cap. INSERT OR IGNORE keeps operators who
-- have already customized this setting (e.g., via a future data import)
-- from being reset to the default.
INSERT OR IGNORE INTO settings (key, value, updated_at)
VALUES (
    'system.executor_snapshot_retention_count',
    '10',
    CURRENT_TIMESTAMP
);

-- migrate:down

DELETE FROM settings
WHERE key = 'system.executor_snapshot_retention_count';

-- SQLite supports DROP COLUMN on 3.35+; the repo targets Python 3.10+ which
-- bundles SQLite >= 3.35, so this is safe.
ALTER TABLE executors DROP COLUMN health_check;
