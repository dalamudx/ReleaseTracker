-- migrate:up

-- Rebuild executor_snapshots to support multi-row history per executor.
-- The original table had UNIQUE(executor_id); SQLite does not support
-- DROP CONSTRAINT, so we rebuild the table and copy data.
-- Each existing row is preserved as a history entry with
-- trigger='pre_update' and image_at_capture extracted from
-- snapshot_data.image where present.

CREATE TABLE executor_snapshots_new (
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
    FOREIGN KEY (executor_id) REFERENCES executors(id) ON DELETE CASCADE,
    FOREIGN KEY (executor_run_id) REFERENCES executor_run_history(id) ON DELETE SET NULL
);

INSERT INTO executor_snapshots_new
    (id, executor_id, snapshot_data, trigger, image_at_capture,
     executor_run_id, unredacted_persisted, created_at, updated_at)
SELECT
    id,
    executor_id,
    snapshot_data,
    'pre_update' AS trigger,
    json_extract(snapshot_data, '$.image') AS image_at_capture,
    NULL AS executor_run_id,
    0 AS unredacted_persisted,
    created_at,
    updated_at
FROM executor_snapshots;

DROP INDEX IF EXISTS idx_executor_snapshots_executor_id;
DROP TABLE executor_snapshots;
ALTER TABLE executor_snapshots_new RENAME TO executor_snapshots;

CREATE INDEX idx_executor_snapshots_executor_id
    ON executor_snapshots(executor_id);
CREATE INDEX idx_executor_snapshots_executor_created
    ON executor_snapshots(executor_id, created_at DESC);
CREATE INDEX idx_executor_snapshots_executor_run_id
    ON executor_snapshots(executor_run_id);

-- migrate:down

-- Reduce multi-row history back to a single most-recent row per executor.
-- Last-write-wins: for each executor_id we keep the row with the newest
-- created_at (ties broken by id).

CREATE TABLE executor_snapshots_old (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    executor_id INTEGER NOT NULL UNIQUE,
    snapshot_data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (executor_id) REFERENCES executors(id) ON DELETE CASCADE
);

INSERT INTO executor_snapshots_old (executor_id, snapshot_data, created_at, updated_at)
SELECT executor_id, snapshot_data, created_at, updated_at
FROM executor_snapshots
WHERE id IN (
    SELECT id FROM executor_snapshots AS s1
    WHERE NOT EXISTS (
        SELECT 1 FROM executor_snapshots AS s2
        WHERE s2.executor_id = s1.executor_id
          AND (s2.created_at > s1.created_at
               OR (s2.created_at = s1.created_at AND s2.id > s1.id))
    )
);

DROP INDEX IF EXISTS idx_executor_snapshots_executor_id;
DROP INDEX IF EXISTS idx_executor_snapshots_executor_created;
DROP INDEX IF EXISTS idx_executor_snapshots_executor_run_id;
DROP TABLE executor_snapshots;
ALTER TABLE executor_snapshots_old RENAME TO executor_snapshots;

CREATE INDEX idx_executor_snapshots_executor_id
    ON executor_snapshots(executor_id);
