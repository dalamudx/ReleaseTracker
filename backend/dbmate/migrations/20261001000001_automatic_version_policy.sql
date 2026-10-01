-- migrate:up
ALTER TABLE executors ADD COLUMN auto_update_policy TEXT NOT NULL DEFAULT 'all'
CHECK(auto_update_policy IN ('all','minor','patch'));

-- migrate:down
-- A rollback must not silently drop a configured safety policy.
CREATE TEMP TABLE version_policy_down_guard (active INTEGER CHECK(active=0));
INSERT INTO version_policy_down_guard SELECT COUNT(*) FROM executors WHERE auto_update_policy!='all';
DROP TABLE version_policy_down_guard;
ALTER TABLE executors DROP COLUMN auto_update_policy;
