-- migrate:up
INSERT INTO settings (key, value, updated_at)
SELECT 'system.admin_user_id', CAST(id AS TEXT), CURRENT_TIMESTAMP
FROM users
WHERE username = 'admin'
  AND NOT EXISTS (
      SELECT 1 FROM settings WHERE key = 'system.admin_user_id'
  );

DROP TABLE IF EXISTS oauth_states;
CREATE TABLE oauth_states (
    state TEXT PRIMARY KEY,
    provider_slug TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    nonce TEXT NOT NULL,
    flow_type TEXT NOT NULL CHECK (flow_type IN ('login', 'bind')),
    initiating_admin_user_id INTEGER,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (initiating_admin_user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX idx_oauth_states_expires_at ON oauth_states(expires_at);

-- migrate:down
DROP INDEX IF EXISTS idx_oauth_states_expires_at;
DROP TABLE IF EXISTS oauth_states;
CREATE TABLE oauth_states (
    state TEXT PRIMARY KEY,
    provider_slug TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
DELETE FROM settings WHERE key IN (
    'system.admin_user_id',
    'system.admin_oidc_issuer',
    'system.admin_oidc_subject'
);
