-- migrate:up
-- Retention-pruned tracker history identities. Upstream sources keep listing
-- old releases; without a tombstone every fetch re-inserted them and the next
-- cleanup deleted them again.
CREATE TABLE tracker_release_history_tombstones (
    aggregate_tracker_id INTEGER NOT NULL,
    identity_key TEXT NOT NULL,
    retention_count INTEGER NOT NULL,
    pruned_at TEXT NOT NULL,
    PRIMARY KEY (aggregate_tracker_id, identity_key),
    FOREIGN KEY (aggregate_tracker_id) REFERENCES aggregate_trackers(id) ON DELETE CASCADE
);

-- migrate:down
DROP TABLE tracker_release_history_tombstones;
