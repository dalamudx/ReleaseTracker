-- migrate:up

-- OAuth transactions are ephemeral. Rebuilding the table discards any transaction
-- created before browser binding was available and enforces the binding at schema level.
DROP INDEX IF EXISTS idx_oauth_states_expires_at;
DROP TABLE oauth_states;
CREATE TABLE oauth_states (
    state TEXT PRIMARY KEY,
    provider_slug TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    nonce TEXT NOT NULL,
    flow_type TEXT NOT NULL CHECK (flow_type IN ('login', 'bind')),
    initiating_admin_user_id INTEGER,
    browser_binding_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (initiating_admin_user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX idx_oauth_states_expires_at ON oauth_states(expires_at);

CREATE TABLE executor_snapshot_claims (
    snapshot_id INTEGER PRIMARY KEY,
    executor_id INTEGER NOT NULL,
    executor_run_id INTEGER NOT NULL UNIQUE,
    claimed_at TEXT NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES executor_snapshots(id) ON DELETE RESTRICT,
    FOREIGN KEY (executor_id) REFERENCES executors(id) ON DELETE RESTRICT,
    FOREIGN KEY (executor_run_id) REFERENCES executor_run_history(id) ON DELETE RESTRICT
);
CREATE INDEX idx_executor_snapshot_claims_executor_id
    ON executor_snapshot_claims(executor_id);
CREATE INDEX idx_executor_snapshot_claims_claimed_at
    ON executor_snapshot_claims(claimed_at);

-- migrate:down

DROP INDEX IF EXISTS idx_executor_snapshot_claims_claimed_at;
DROP INDEX IF EXISTS idx_executor_snapshot_claims_executor_id;
DROP TABLE IF EXISTS executor_snapshot_claims;

DROP INDEX IF EXISTS idx_oauth_states_expires_at;
DROP TABLE oauth_states;
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
