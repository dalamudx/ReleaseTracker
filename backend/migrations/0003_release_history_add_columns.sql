-- depends: 0002_releases_add_columns

ALTER TABLE release_history ADD COLUMN name TEXT;
ALTER TABLE release_history ADD COLUMN channel_name TEXT;
