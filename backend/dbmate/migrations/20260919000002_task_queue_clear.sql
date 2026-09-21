-- migrate:up
-- Clear queue visibility only; retain deduplication, receipt links and audit records.
ALTER TABLE tasks ADD COLUMN cleared_at REAL;
CREATE INDEX tasks_visible ON tasks(id DESC) WHERE cleared_at IS NULL;

-- migrate:down
DROP INDEX tasks_visible;
ALTER TABLE tasks DROP COLUMN cleared_at;
