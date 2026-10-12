-- migrate:up transaction:false
-- Rebuild without cascading deletes; retain every identity and foreign key.
PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;
-- Known missing-parent repair: same whitelist and CASCADE/SET NULL rules as
-- database_orphans.prune_orphans. Parents are processed before dependents;
-- unrelated corruption and all task records remain untouched.
CREATE TEMP TABLE release_time_orphan_cleanup (table_name TEXT, affected_rows INTEGER);
DELETE FROM "aggregate_tracker_sources" WHERE "aggregate_tracker_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_trackers" p WHERE p."id" = "aggregate_tracker_sources"."aggregate_tracker_id");
INSERT INTO release_time_orphan_cleanup VALUES ('aggregate_tracker_sources', changes());
DELETE FROM "executor_desired_state" WHERE "executor_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "executors" p WHERE p."id" = "executor_desired_state"."executor_id");
INSERT INTO release_time_orphan_cleanup VALUES ('executor_desired_state', changes());
DELETE FROM "executor_run_history" WHERE "executor_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "executors" p WHERE p."id" = "executor_run_history"."executor_id");
INSERT INTO release_time_orphan_cleanup VALUES ('executor_run_history', changes());
DELETE FROM "executor_service_bindings" WHERE "executor_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "executors" p WHERE p."id" = "executor_service_bindings"."executor_id");
INSERT INTO release_time_orphan_cleanup VALUES ('executor_service_bindings', changes());
DELETE FROM "executor_status" WHERE "executor_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "executors" p WHERE p."id" = "executor_status"."executor_id");
INSERT INTO release_time_orphan_cleanup VALUES ('executor_status', changes());
DELETE FROM "ssh_compose_ownership" WHERE "executor_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "executors" p WHERE p."id" = "ssh_compose_ownership"."executor_id");
INSERT INTO release_time_orphan_cleanup VALUES ('ssh_compose_ownership', changes());
DELETE FROM "tracker_release_history" WHERE "aggregate_tracker_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_trackers" p WHERE p."id" = "tracker_release_history"."aggregate_tracker_id");
INSERT INTO release_time_orphan_cleanup VALUES ('tracker_release_history', changes());
UPDATE "tracker_release_history" SET "merged_into_tracker_release_history_id"=NULL WHERE "merged_into_tracker_release_history_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "tracker_release_history" p WHERE p."id" = "tracker_release_history"."merged_into_tracker_release_history_id");
INSERT INTO release_time_orphan_cleanup VALUES ('tracker_release_history', changes());
DELETE FROM "tracker_release_history_tombstones" WHERE "aggregate_tracker_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_trackers" p WHERE p."id" = "tracker_release_history_tombstones"."aggregate_tracker_id");
INSERT INTO release_time_orphan_cleanup VALUES ('tracker_release_history_tombstones', changes());
DELETE FROM "executor_snapshots" WHERE "executor_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "executors" p WHERE p."id" = "executor_snapshots"."executor_id");
INSERT INTO release_time_orphan_cleanup VALUES ('executor_snapshots', changes());
UPDATE "executor_snapshots" SET "executor_run_id"=NULL WHERE "executor_run_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "executor_run_history" p WHERE p."id" = "executor_snapshots"."executor_run_id");
INSERT INTO release_time_orphan_cleanup VALUES ('executor_snapshots', changes());
DELETE FROM "repository_webhooks" WHERE "tracker_source_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_tracker_sources" p WHERE p."id" = "repository_webhooks"."tracker_source_id");
INSERT INTO release_time_orphan_cleanup VALUES ('repository_webhooks', changes());
DELETE FROM "source_fetch_runs" WHERE "tracker_source_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_tracker_sources" p WHERE p."id" = "source_fetch_runs"."tracker_source_id");
INSERT INTO release_time_orphan_cleanup VALUES ('source_fetch_runs', changes());
DELETE FROM "source_release_history" WHERE "tracker_source_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_tracker_sources" p WHERE p."id" = "source_release_history"."tracker_source_id");
INSERT INTO release_time_orphan_cleanup VALUES ('source_release_history', changes());
DELETE FROM "source_release_observations" WHERE "tracker_source_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_tracker_sources" p WHERE p."id" = "source_release_observations"."tracker_source_id");
INSERT INTO release_time_orphan_cleanup VALUES ('source_release_observations', changes());
DELETE FROM "tracker_current_releases" WHERE "tracker_release_history_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "tracker_release_history" p WHERE p."id" = "tracker_current_releases"."tracker_release_history_id")
    OR "aggregate_tracker_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_trackers" p WHERE p."id" = "tracker_current_releases"."aggregate_tracker_id");
INSERT INTO release_time_orphan_cleanup VALUES ('tracker_current_releases', changes());
DELETE FROM "canonical_releases" WHERE "aggregate_tracker_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_trackers" p WHERE p."id" = "canonical_releases"."aggregate_tracker_id");
INSERT INTO release_time_orphan_cleanup VALUES ('canonical_releases', changes());
UPDATE "canonical_releases" SET "primary_observation_id"=NULL WHERE "primary_observation_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_release_observations" p WHERE p."id" = "canonical_releases"."primary_observation_id");
INSERT INTO release_time_orphan_cleanup VALUES ('canonical_releases', changes());
DELETE FROM "source_release_aliases" WHERE "tracker_source_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_tracker_sources" p WHERE p."id" = "source_release_aliases"."tracker_source_id")
    OR "source_release_history_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_release_history" p WHERE p."id" = "source_release_aliases"."source_release_history_id");
INSERT INTO release_time_orphan_cleanup VALUES ('source_release_aliases', changes());
DELETE FROM "source_release_run_observations" WHERE "source_release_history_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_release_history" p WHERE p."id" = "source_release_run_observations"."source_release_history_id")
    OR "source_fetch_run_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_fetch_runs" p WHERE p."id" = "source_release_run_observations"."source_fetch_run_id");
INSERT INTO release_time_orphan_cleanup VALUES ('source_release_run_observations', changes());
DELETE FROM "tracker_release_history_sources" WHERE "source_release_history_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_release_history" p WHERE p."id" = "tracker_release_history_sources"."source_release_history_id")
    OR "tracker_release_history_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "tracker_release_history" p WHERE p."id" = "tracker_release_history_sources"."tracker_release_history_id");
INSERT INTO release_time_orphan_cleanup VALUES ('tracker_release_history_sources', changes());
DELETE FROM "webhook_deliveries" WHERE "webhook_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "repository_webhooks" p WHERE p."id" = "webhook_deliveries"."webhook_id");
INSERT INTO release_time_orphan_cleanup VALUES ('webhook_deliveries', changes());
DELETE FROM "canonical_release_observations" WHERE "source_release_observation_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_release_observations" p WHERE p."id" = "canonical_release_observations"."source_release_observation_id")
    OR "canonical_release_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "canonical_releases" p WHERE p."id" = "canonical_release_observations"."canonical_release_id");
INSERT INTO release_time_orphan_cleanup VALUES ('canonical_release_observations', changes());
DELETE FROM "source_refresh_requests" WHERE "tracker_source_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "aggregate_tracker_sources" p WHERE p."id" = "source_refresh_requests"."tracker_source_id")
    OR "delivery_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "webhook_deliveries" p WHERE p."id" = "source_refresh_requests"."delivery_id");
INSERT INTO release_time_orphan_cleanup VALUES ('source_refresh_requests', changes());
UPDATE "source_refresh_requests" SET "source_fetch_run_id"=NULL WHERE "source_fetch_run_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_fetch_runs" p WHERE p."id" = "source_refresh_requests"."source_fetch_run_id");
INSERT INTO release_time_orphan_cleanup VALUES ('source_refresh_requests', changes());
DELETE FROM "source_release_alias_run_observations" WHERE "source_release_alias_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_release_aliases" p WHERE p."id" = "source_release_alias_run_observations"."source_release_alias_id")
    OR "source_fetch_run_id" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "source_fetch_runs" p WHERE p."id" = "source_release_alias_run_observations"."source_fetch_run_id");
INSERT INTO release_time_orphan_cleanup VALUES ('source_release_alias_run_observations', changes());
INSERT OR REPLACE INTO settings(key, value, updated_at)
SELECT 'internal.release_time_pruned_orphans', json_group_object(table_name, affected_rows), datetime('now')
FROM (SELECT table_name, SUM(affected_rows) AS affected_rows FROM release_time_orphan_cleanup
      GROUP BY table_name HAVING SUM(affected_rows) > 0) HAVING COUNT(*) > 0;
DROP TABLE release_time_orphan_cleanup;
-- Known orphan repair must leave a clean database. Unknown/RESTRICT violations
-- still abort the entire migration, including the cleanup above.
CREATE TEMP TABLE release_time_preflight_fk_check (
    violations INTEGER CONSTRAINT release_time_unrepairable_foreign_keys_run_audit_database
    CHECK (violations = 0)
);
INSERT INTO release_time_preflight_fk_check SELECT COUNT(*) FROM pragma_foreign_key_check;
DROP TABLE release_time_preflight_fk_check;
CREATE TEMP TABLE release_time_task_relations_check (
    violations INTEGER CONSTRAINT release_time_invalid_deployment_observation_relations
    CHECK (violations = 0)
);
INSERT INTO release_time_task_relations_check
SELECT COUNT(*) FROM deployment_observations o
LEFT JOIN tasks t ON t.id=o.task_id
LEFT JOIN executor_run_history h ON h.id=o.run_id
WHERE t.id IS NULL OR h.id IS NULL OR h.executor_id!=o.executor_id
   OR (o.state IN ('waiting','finalizing') AND t.state!='running');
DROP TABLE release_time_task_relations_check;
CREATE TABLE source_release_observations_time_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracker_source_id INTEGER NOT NULL,
    source_release_key TEXT NOT NULL,
    name TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    version TEXT NOT NULL,
    published_at TEXT,
    url TEXT NOT NULL,
    changelog_url TEXT,
    prerelease INTEGER DEFAULT 0,
    body TEXT,
    commit_sha TEXT,
    raw_payload TEXT NOT NULL DEFAULT '{}',
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (tracker_source_id) REFERENCES aggregate_tracker_sources(id) ON DELETE CASCADE,
    UNIQUE(tracker_source_id, source_release_key)
);
INSERT INTO source_release_observations_time_v2 SELECT * FROM source_release_observations;
DROP TABLE source_release_observations;
ALTER TABLE source_release_observations_time_v2 RENAME TO source_release_observations;

CREATE TABLE canonical_releases_time_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aggregate_tracker_id INTEGER NOT NULL,
    canonical_key TEXT NOT NULL,
    version TEXT NOT NULL,
    name TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    published_at TEXT,
    url TEXT NOT NULL,
    changelog_url TEXT,
    prerelease INTEGER DEFAULT 0,
    body TEXT,
    primary_observation_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (aggregate_tracker_id) REFERENCES aggregate_trackers(id) ON DELETE CASCADE,
    FOREIGN KEY (primary_observation_id) REFERENCES source_release_observations(id) ON DELETE SET NULL,
    UNIQUE(aggregate_tracker_id, canonical_key)
);
INSERT INTO canonical_releases_time_v2 SELECT * FROM canonical_releases;
DROP TABLE canonical_releases;
ALTER TABLE canonical_releases_time_v2 RENAME TO canonical_releases;

CREATE TABLE source_release_history_time_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracker_source_id INTEGER NOT NULL,
    first_source_fetch_run_id INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    source_release_key TEXT NOT NULL,
    version TEXT NOT NULL,
    digest TEXT,
    digest_algorithm TEXT,
    digest_media_type TEXT,
    digest_platform TEXT,
    identity_key TEXT NOT NULL,
    name TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    published_at TEXT,
    url TEXT NOT NULL,
    changelog_url TEXT,
    prerelease INTEGER DEFAULT 0,
    body TEXT,
    commit_sha TEXT,
    raw_payload TEXT NOT NULL DEFAULT '{}',
    first_observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL, immutable_key TEXT,
    FOREIGN KEY (tracker_source_id) REFERENCES aggregate_tracker_sources(id) ON DELETE CASCADE,
    FOREIGN KEY (first_source_fetch_run_id) REFERENCES source_fetch_runs(id) ON DELETE RESTRICT,
    UNIQUE(tracker_source_id, identity_key)
);
INSERT INTO source_release_history_time_v2 SELECT * FROM source_release_history;
DROP TABLE source_release_history;
ALTER TABLE source_release_history_time_v2 RENAME TO source_release_history;

CREATE TABLE tracker_current_releases_time_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aggregate_tracker_id INTEGER NOT NULL,
    identity_key TEXT NOT NULL,
    version TEXT NOT NULL,
    digest TEXT,
    tracker_release_history_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    published_at TEXT,
    url TEXT NOT NULL,
    changelog_url TEXT,
    prerelease INTEGER DEFAULT 0,
    body TEXT,
    projected_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, immutable_key TEXT,
    FOREIGN KEY (aggregate_tracker_id) REFERENCES aggregate_trackers(id) ON DELETE CASCADE,
    FOREIGN KEY (tracker_release_history_id) REFERENCES tracker_release_history(id) ON DELETE CASCADE,
    UNIQUE(aggregate_tracker_id, identity_key)
);
INSERT INTO tracker_current_releases_time_v2 SELECT * FROM tracker_current_releases;
DROP TABLE tracker_current_releases;
ALTER TABLE tracker_current_releases_time_v2 RENAME TO tracker_current_releases;

CREATE INDEX idx_source_release_observations_source_id
    ON source_release_observations(tracker_source_id);
CREATE INDEX idx_source_release_observations_version
    ON source_release_observations(version);
CREATE INDEX idx_canonical_releases_tracker_id
    ON canonical_releases(aggregate_tracker_id);
CREATE INDEX idx_canonical_releases_primary_observation_id
    ON canonical_releases(primary_observation_id);
CREATE INDEX idx_source_release_history_source_published_at
    ON source_release_history(tracker_source_id, published_at DESC);
CREATE INDEX idx_source_release_history_digest
    ON source_release_history(digest);
CREATE INDEX idx_tracker_current_releases_tracker_published_at
    ON tracker_current_releases(aggregate_tracker_id, published_at DESC);
CREATE UNIQUE INDEX idx_source_release_history_immutable_key
    ON source_release_history(tracker_source_id, immutable_key);
CREATE UNIQUE INDEX idx_tracker_current_releases_immutable_key
    ON tracker_current_releases(aggregate_tracker_id, immutable_key);

-- Preserve the old value for audit, never promote a synthetic time to publication.
UPDATE source_release_history SET raw_payload = json_set(raw_payload,
 '$.legacy_published_at', published_at,
 '$.artifact_created_at', CASE WHEN json_extract(raw_payload, '$.published_at_source') = 'artifact_created' THEN published_at ELSE NULL END)
WHERE source_type = 'container';
UPDATE source_release_history SET published_at = NULL WHERE source_type = 'container';
UPDATE source_release_observations SET raw_payload = json_set(raw_payload, '$.legacy_published_at', published_at), published_at = NULL
WHERE tracker_source_id IN (SELECT id FROM aggregate_tracker_sources WHERE source_type IN ('container', 'helm'));
-- Other legacy timestamps may have been synthesized from tags or fetch time.
-- Only explicit native-publication provenance is sufficient to retain them.
UPDATE source_release_history SET raw_payload = json_set(raw_payload, '$.legacy_published_at', published_at), published_at = NULL
WHERE source_type != 'container' AND (source_type = 'helm' OR COALESCE(json_extract(raw_payload, '$.published_at_source'), '') NOT IN ('source', 'upstream_release'));
UPDATE source_release_observations SET raw_payload = json_set(raw_payload, '$.legacy_published_at', published_at), published_at = NULL
WHERE published_at IS NOT NULL AND COALESCE(json_extract(raw_payload, '$.published_at_source'), '') NOT IN ('source', 'upstream_release');
UPDATE canonical_releases SET published_at = (SELECT published_at FROM source_release_observations WHERE id = canonical_releases.primary_observation_id);
UPDATE tracker_current_releases SET published_at = (SELECT srh.published_at FROM tracker_release_history trh JOIN source_release_history srh ON srh.id = trh.primary_source_release_history_id WHERE trh.id = tracker_current_releases.tracker_release_history_id);
UPDATE trackers SET version_sort_mode = 'semver';
UPDATE settings SET value = 'semver' WHERE key = 'version_sort_mode';
INSERT OR REPLACE INTO settings(key, value, updated_at)
VALUES ('internal.release_selection_rebuild_pending', '2', datetime('now'));
-- Fail the migration if rebuilding damaged referential integrity.
CREATE TEMP TABLE release_time_fk_check (violations INTEGER CONSTRAINT release_time_rebuild_foreign_key_integrity CHECK (violations = 0));
INSERT INTO release_time_fk_check SELECT COUNT(*) FROM pragma_foreign_key_check;
DROP TABLE release_time_fk_check;
COMMIT;
PRAGMA foreign_keys = ON;

-- migrate:down
-- Unknown upstream times cannot be reconstructed. Keep the compatible nullable
-- schema and audit metadata; restoring old application semantics requires backup.
SELECT 1;
