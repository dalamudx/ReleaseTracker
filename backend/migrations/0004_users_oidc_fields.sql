-- depends: 0003_release_history_add_columns

ALTER TABLE users ADD COLUMN oauth_provider TEXT;
ALTER TABLE users ADD COLUMN oauth_sub TEXT;
ALTER TABLE users ADD COLUMN avatar_url TEXT;
