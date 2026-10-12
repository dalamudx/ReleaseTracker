-- migrate:up transaction:false
PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;
-- Current source state and channel decisions have no foreign key to audit history.
-- Known missing-parent repair: same whitelist and CASCADE/SET NULL rules as
-- database_orphans.prune_orphans. Parents are processed before dependents;
-- unrelated corruption and all task records remain untouched.
CREATE TEMP TABLE channel_selection_orphan_cleanup (table_name TEXT, affected_rows INTEGER);
DELETE FROM "aggregate_tracker_sources" WHERE "aggregate_tracker_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_trackers" p WHERE p."id" = "aggregate_tracker_sources"."aggregate_tracker_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('aggregate_tracker_sources', changes());
DELETE FROM "executor_desired_state" WHERE "executor_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "executors" p WHERE p."id" = "executor_desired_state"."executor_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('executor_desired_state', changes());
DELETE FROM "executor_run_history" WHERE "executor_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "executors" p WHERE p."id" = "executor_run_history"."executor_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('executor_run_history', changes());
DELETE FROM "executor_service_bindings" WHERE "executor_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "executors" p WHERE p."id" = "executor_service_bindings"."executor_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('executor_service_bindings', changes());
DELETE FROM "executor_status" WHERE "executor_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "executors" p WHERE p."id" = "executor_status"."executor_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('executor_status', changes());
DELETE FROM "ssh_compose_ownership" WHERE "executor_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "executors" p WHERE p."id" = "ssh_compose_ownership"."executor_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('ssh_compose_ownership', changes());
DELETE FROM "tracker_release_history" WHERE "aggregate_tracker_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_trackers" p WHERE p."id" = "tracker_release_history"."aggregate_tracker_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('tracker_release_history', changes());
UPDATE "tracker_release_history" SET "merged_into_tracker_release_history_id"=NULL WHERE "merged_into_tracker_release_history_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "tracker_release_history" p WHERE p."id" = "tracker_release_history"."merged_into_tracker_release_history_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('tracker_release_history', changes());
DELETE FROM "tracker_release_history_tombstones" WHERE "aggregate_tracker_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_trackers" p WHERE p."id" = "tracker_release_history_tombstones"."aggregate_tracker_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('tracker_release_history_tombstones', changes());
DELETE FROM "executor_snapshots" WHERE "executor_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "executors" p WHERE p."id" = "executor_snapshots"."executor_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('executor_snapshots', changes());
UPDATE "executor_snapshots" SET "executor_run_id"=NULL WHERE "executor_run_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "executor_run_history" p WHERE p."id" = "executor_snapshots"."executor_run_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('executor_snapshots', changes());
DELETE FROM "repository_webhooks" WHERE "tracker_source_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_tracker_sources" p WHERE p."id" = "repository_webhooks"."tracker_source_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('repository_webhooks', changes());
DELETE FROM "source_fetch_runs" WHERE "tracker_source_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_tracker_sources" p WHERE p."id" = "source_fetch_runs"."tracker_source_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('source_fetch_runs', changes());
DELETE FROM "source_release_history" WHERE "tracker_source_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_tracker_sources" p WHERE p."id" = "source_release_history"."tracker_source_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('source_release_history', changes());
DELETE FROM "source_release_observations" WHERE "tracker_source_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_tracker_sources" p WHERE p."id" = "source_release_observations"."tracker_source_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('source_release_observations', changes());
DELETE FROM "tracker_current_releases" WHERE "tracker_release_history_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "tracker_release_history" p WHERE p."id" = "tracker_current_releases"."tracker_release_history_id")
    OR "aggregate_tracker_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_trackers" p WHERE p."id" = "tracker_current_releases"."aggregate_tracker_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('tracker_current_releases', changes());
DELETE FROM "canonical_releases" WHERE "aggregate_tracker_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_trackers" p WHERE p."id" = "canonical_releases"."aggregate_tracker_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('canonical_releases', changes());
UPDATE "canonical_releases" SET "primary_observation_id"=NULL WHERE "primary_observation_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_release_observations" p WHERE p."id" = "canonical_releases"."primary_observation_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('canonical_releases', changes());
DELETE FROM "source_release_aliases" WHERE "tracker_source_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_tracker_sources" p WHERE p."id" = "source_release_aliases"."tracker_source_id")
    OR "source_release_history_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_release_history" p WHERE p."id" = "source_release_aliases"."source_release_history_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('source_release_aliases', changes());
DELETE FROM "source_release_run_observations" WHERE "source_release_history_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_release_history" p WHERE p."id" = "source_release_run_observations"."source_release_history_id")
    OR "source_fetch_run_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_fetch_runs" p WHERE p."id" = "source_release_run_observations"."source_fetch_run_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('source_release_run_observations', changes());
DELETE FROM "tracker_release_history_sources" WHERE "source_release_history_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_release_history" p WHERE p."id" = "tracker_release_history_sources"."source_release_history_id")
    OR "tracker_release_history_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "tracker_release_history" p WHERE p."id" = "tracker_release_history_sources"."tracker_release_history_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('tracker_release_history_sources', changes());
DELETE FROM "webhook_deliveries" WHERE "webhook_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "repository_webhooks" p WHERE p."id" = "webhook_deliveries"."webhook_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('webhook_deliveries', changes());
DELETE FROM "canonical_release_observations" WHERE "source_release_observation_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_release_observations" p WHERE p."id" = "canonical_release_observations"."source_release_observation_id")
    OR "canonical_release_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "canonical_releases" p WHERE p."id" = "canonical_release_observations"."canonical_release_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('canonical_release_observations', changes());
DELETE FROM "source_refresh_requests" WHERE "tracker_source_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_tracker_sources" p WHERE p."id" = "source_refresh_requests"."tracker_source_id")
    OR "delivery_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "webhook_deliveries" p WHERE p."id" = "source_refresh_requests"."delivery_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('source_refresh_requests', changes());
UPDATE "source_refresh_requests" SET "source_fetch_run_id"=NULL WHERE "source_fetch_run_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_fetch_runs" p WHERE p."id" = "source_refresh_requests"."source_fetch_run_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('source_refresh_requests', changes());
DELETE FROM "source_release_alias_run_observations" WHERE "source_release_alias_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_release_aliases" p WHERE p."id" = "source_release_alias_run_observations"."source_release_alias_id")
    OR "source_fetch_run_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_fetch_runs" p WHERE p."id" = "source_release_alias_run_observations"."source_fetch_run_id");
INSERT INTO channel_selection_orphan_cleanup VALUES ('source_release_alias_run_observations', changes());
INSERT OR REPLACE INTO settings(key, value, updated_at)
SELECT 'internal.channel_selection_pruned_orphans', json_group_object(table_name, affected_rows), datetime('now')
FROM (SELECT table_name, SUM(affected_rows) AS affected_rows FROM channel_selection_orphan_cleanup
      GROUP BY table_name HAVING SUM(affected_rows) > 0) HAVING COUNT(*) > 0;
DROP TABLE channel_selection_orphan_cleanup;
-- Known orphan repair must leave a clean database. Unknown/RESTRICT violations
-- still abort the entire migration, including the cleanup above.
CREATE TEMP TABLE channel_selection_preflight_fk_check (
    violations INTEGER CONSTRAINT channel_selection_unrepairable_foreign_keys_run_audit_database
    CHECK (violations = 0)
);
INSERT INTO channel_selection_preflight_fk_check SELECT COUNT(*) FROM pragma_foreign_key_check;
DROP TABLE channel_selection_preflight_fk_check;
CREATE TEMP TABLE channel_selection_task_relations_check (
    violations INTEGER CONSTRAINT channel_selection_invalid_deployment_observation_relations
    CHECK (violations = 0)
);
INSERT INTO channel_selection_task_relations_check
SELECT COUNT(*) FROM deployment_observations o
LEFT JOIN tasks t ON t.id=o.task_id
LEFT JOIN executor_run_history h ON h.id=o.run_id
WHERE t.id IS NULL OR h.id IS NULL OR h.executor_id!=o.executor_id
   OR (o.state IN ('waiting','finalizing') AND t.state!='running');
DROP TABLE channel_selection_task_relations_check;
CREATE TABLE source_current_candidates (
    tracker_source_id INTEGER NOT NULL REFERENCES aggregate_tracker_sources(id) ON DELETE CASCADE,
    tag_name TEXT NOT NULL,
    payload TEXT NOT NULL,
    listed INTEGER NOT NULL DEFAULT 1,
    confirmed INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tracker_source_id, tag_name)
);
CREATE TABLE source_inventory_scans (
    tracker_source_id INTEGER PRIMARY KEY REFERENCES aggregate_tracker_sources(id) ON DELETE CASCADE,
    origin TEXT NOT NULL,
    listed_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE tracker_channel_selections (
    aggregate_tracker_id INTEGER NOT NULL REFERENCES aggregate_trackers(id) ON DELETE CASCADE,
    tracker_source_id INTEGER NOT NULL REFERENCES aggregate_tracker_sources(id) ON DELETE CASCADE,
    channel_key TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    inventory_revision TEXT NOT NULL,
    state TEXT NOT NULL,
    reason TEXT,
    release_payload TEXT,
    config_hash TEXT NOT NULL,
    revision TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tracker_source_id, channel_key)
);
CREATE INDEX idx_channel_selection_tracker ON tracker_channel_selections(aggregate_tracker_id);
INSERT OR REPLACE INTO settings(key,value,updated_at)
VALUES ('internal.release_selection_rebuild_pending','3',strftime('%Y-%m-%dT%H:%M:%f','now'));
-- Old plans must be reviewed under the new candidate and channel rules.
-- Existing tasks and approved immutable targets are not rewritten here.

COMMIT;
PRAGMA foreign_keys = ON;

-- migrate:down
DROP TABLE tracker_channel_selections;
DROP TABLE source_current_candidates;
DROP TABLE source_inventory_scans;
DELETE FROM settings WHERE key='internal.release_selection_rebuild_pending' AND value='3';
