-- depends: 0001_initial_schema

ALTER TABLE releases ADD COLUMN body TEXT;
ALTER TABLE releases ADD COLUMN commit_sha TEXT;
ALTER TABLE releases ADD COLUMN republish_count INTEGER DEFAULT 0;
ALTER TABLE releases ADD COLUMN channel_name TEXT;
