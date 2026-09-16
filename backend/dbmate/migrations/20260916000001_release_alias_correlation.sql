-- migrate:up

CREATE TABLE source_release_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_release_history_id INTEGER NOT NULL,
    tracker_source_id INTEGER NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    channel_name TEXT,
    first_source_fetch_run_id INTEGER NOT NULL,
    last_source_fetch_run_id INTEGER NOT NULL,
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_release_history_id) REFERENCES source_release_history(id) ON DELETE CASCADE,
    FOREIGN KEY (tracker_source_id) REFERENCES aggregate_tracker_sources(id) ON DELETE CASCADE,
    FOREIGN KEY (first_source_fetch_run_id) REFERENCES source_fetch_runs(id) ON DELETE RESTRICT,
    FOREIGN KEY (last_source_fetch_run_id) REFERENCES source_fetch_runs(id) ON DELETE RESTRICT,
    UNIQUE(tracker_source_id, source_release_history_id, normalized_alias)
);

CREATE TABLE source_release_alias_run_observations (
    source_fetch_run_id INTEGER NOT NULL,
    source_release_alias_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_fetch_run_id, source_release_alias_id),
    FOREIGN KEY (source_fetch_run_id) REFERENCES source_fetch_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (source_release_alias_id) REFERENCES source_release_aliases(id) ON DELETE CASCADE
);

-- Existing history retained one representative tag per artifact. Backfill that known
-- alias; a subsequent successful source fetch discovers the complete alias set.
INSERT INTO source_release_aliases (
    source_release_history_id, tracker_source_id, alias, normalized_alias, channel_name,
    first_source_fetch_run_id, last_source_fetch_run_id, first_observed_at,
    last_observed_at, created_at, updated_at
)
SELECT srh.id, srh.tracker_source_id,
       COALESCE(NULLIF(TRIM(srh.tag_name), ''), srh.version),
       LOWER(
           CASE
               WHEN LOWER(COALESCE(NULLIF(TRIM(srh.tag_name), ''), srh.version)) LIKE 'release/%'
                   THEN SUBSTR(COALESCE(NULLIF(TRIM(srh.tag_name), ''), srh.version), 9)
               WHEN COALESCE(NULLIF(TRIM(srh.tag_name), ''), srh.version) GLOB 'v[0-9]*'
                   THEN SUBSTR(COALESCE(NULLIF(TRIM(srh.tag_name), ''), srh.version), 2)
               ELSE COALESCE(NULLIF(TRIM(srh.tag_name), ''), srh.version)
           END
       ),
       json_extract(srh.raw_payload, '$.channel_name'),
       srh.first_source_fetch_run_id, srh.first_source_fetch_run_id,
       srh.first_observed_at, srh.first_observed_at, srh.created_at, srh.created_at
FROM source_release_history srh
WHERE COALESCE(NULLIF(TRIM(srh.tag_name), ''), NULLIF(TRIM(srh.version), '')) IS NOT NULL;

INSERT INTO source_release_alias_run_observations (
    source_fetch_run_id, source_release_alias_id, observed_at, created_at
)
SELECT sra.first_source_fetch_run_id, sra.id, sra.first_observed_at, sra.created_at
FROM source_release_aliases sra;

ALTER TABLE tracker_release_history
    ADD COLUMN merged_into_tracker_release_history_id INTEGER
    REFERENCES tracker_release_history(id) ON DELETE SET NULL;

CREATE INDEX idx_source_release_aliases_history_id
    ON source_release_aliases(source_release_history_id);
CREATE INDEX idx_source_release_aliases_source_normalized
    ON source_release_aliases(tracker_source_id, normalized_alias);
CREATE INDEX idx_source_release_aliases_last_run
    ON source_release_aliases(last_source_fetch_run_id);
CREATE INDEX idx_source_release_alias_run_observations_alias
    ON source_release_alias_run_observations(source_release_alias_id, observed_at DESC);
CREATE INDEX idx_tracker_release_history_merged_into
    ON tracker_release_history(merged_into_tracker_release_history_id);

-- migrate:down

DROP INDEX IF EXISTS idx_tracker_release_history_merged_into;
ALTER TABLE tracker_release_history DROP COLUMN merged_into_tracker_release_history_id;
DROP INDEX IF EXISTS idx_source_release_alias_run_observations_alias;
DROP INDEX IF EXISTS idx_source_release_aliases_last_run;
DROP INDEX IF EXISTS idx_source_release_aliases_source_normalized;
DROP INDEX IF EXISTS idx_source_release_aliases_history_id;
DROP TABLE source_release_alias_run_observations;
DROP TABLE source_release_aliases;
