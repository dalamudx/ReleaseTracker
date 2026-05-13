-- migrate:up
ALTER TABLE aggregate_trackers
    ADD COLUMN release_notes_config TEXT NOT NULL DEFAULT '{"source":"release_notes"}';

-- migrate:down
ALTER TABLE aggregate_trackers DROP COLUMN release_notes_config;
