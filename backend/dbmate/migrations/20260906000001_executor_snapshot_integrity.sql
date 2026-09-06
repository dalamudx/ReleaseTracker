-- migrate:up

ALTER TABLE executor_snapshots ADD COLUMN snapshot_format_version INTEGER;
ALTER TABLE executor_snapshots ADD COLUMN snapshot_sha256 TEXT;
ALTER TABLE executor_snapshots ADD COLUMN snapshot_size_bytes INTEGER;

-- migrate:down

-- SQLite compatibility: rebuild without integrity metadata.
CREATE TABLE executor_snapshots_without_integrity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    executor_id INTEGER NOT NULL,
    snapshot_data TEXT NOT NULL DEFAULT '{}',
    trigger TEXT NOT NULL DEFAULT 'pre_update'
        CHECK (trigger IN ('pre_update', 'manual', 'pre_rollback')),
    image_at_capture TEXT,
    executor_run_id INTEGER,
    unredacted_persisted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    locked INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (executor_id) REFERENCES executors(id) ON DELETE CASCADE,
    FOREIGN KEY (executor_run_id) REFERENCES executor_run_history(id) ON DELETE SET NULL
);
INSERT INTO executor_snapshots_without_integrity
    (id, executor_id, snapshot_data, trigger, image_at_capture, executor_run_id,
     unredacted_persisted, created_at, updated_at, locked)
SELECT id, executor_id, snapshot_data, trigger, image_at_capture, executor_run_id,
       unredacted_persisted, created_at, updated_at, locked
FROM executor_snapshots;
DROP INDEX IF EXISTS idx_executor_snapshots_executor_id;
DROP INDEX IF EXISTS idx_executor_snapshots_executor_created;
DROP INDEX IF EXISTS idx_executor_snapshots_executor_run_id;
DROP TABLE executor_snapshots;
ALTER TABLE executor_snapshots_without_integrity RENAME TO executor_snapshots;
CREATE INDEX idx_executor_snapshots_executor_id ON executor_snapshots(executor_id);
CREATE INDEX idx_executor_snapshots_executor_created ON executor_snapshots(executor_id, created_at DESC);
CREATE INDEX idx_executor_snapshots_executor_run_id ON executor_snapshots(executor_run_id);
