CREATE TABLE schema_migrations (version TEXT PRIMARY KEY);
CREATE TABLE releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracker_name TEXT NOT NULL,
    name TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    version TEXT NOT NULL,
    published_at TEXT NOT NULL,
    url TEXT NOT NULL,
    prerelease INTEGER DEFAULT 0,
    created_at TEXT NOT NULL, body TEXT, commit_sha TEXT, republish_count INTEGER DEFAULT 0, channel_name TEXT,
    UNIQUE(tracker_name, tag_name)
);
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE tracker_status (
    name TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    last_check TEXT,
    last_version TEXT,
    error TEXT
);
CREATE TABLE credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    token TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
, secrets TEXT NOT NULL DEFAULT '{}');
CREATE TABLE notifiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    url TEXT NOT NULL,
    events TEXT DEFAULT '["new_release"]',
    enabled INTEGER DEFAULT 1,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
, language TEXT NOT NULL DEFAULT 'en');
CREATE TABLE trackers (
    name TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    repo TEXT,
    project TEXT,
    instance TEXT,
    chart TEXT,
    credential_name TEXT,
    channels TEXT DEFAULT '[]',
    interval INTEGER DEFAULT 60,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
, image TEXT, registry TEXT, version_sort_mode TEXT DEFAULT 'published_at', fetch_limit INTEGER DEFAULT 10, fallback_tags INTEGER DEFAULT 0, fetch_timeout INTEGER DEFAULT 15, github_fetch_mode TEXT DEFAULT 'rest_first');
CREATE TABLE release_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id INTEGER NOT NULL,
    commit_sha TEXT NOT NULL,
    published_at TEXT NOT NULL,
    body TEXT,
    recorded_at TEXT NOT NULL, name TEXT, channel_name TEXT,
    FOREIGN KEY (release_id) REFERENCES releases(id) ON DELETE CASCADE
);
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_login_at TEXT
, oauth_provider TEXT, oauth_sub TEXT, avatar_url TEXT);
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    refresh_token_hash TEXT,
    user_agent TEXT,
    ip_address TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE oauth_providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    issuer_url TEXT,
    discovery_enabled INTEGER DEFAULT 1,
    client_id TEXT NOT NULL,
    client_secret TEXT,
    authorization_url TEXT,
    token_url TEXT,
    userinfo_url TEXT,
    jwks_uri TEXT,
    scopes TEXT DEFAULT 'openid email profile',
    enabled INTEGER DEFAULT 1,
    icon_url TEXT,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE oauth_states (
    state TEXT PRIMARY KEY,
    provider_slug TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE runtime_connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    config TEXT NOT NULL DEFAULT '{}',
    secrets TEXT,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
, credential_id INTEGER);
CREATE TABLE executors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    runtime_type TEXT NOT NULL,
    runtime_connection_id INTEGER NOT NULL,
    tracker_name TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    update_mode TEXT NOT NULL,
    target_ref TEXT NOT NULL DEFAULT '{}',
    maintenance_window TEXT,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, image_selection_mode TEXT NOT NULL DEFAULT 'replace_tag_on_current_image', channel_name TEXT, tracker_source_id INTEGER, image_reference_mode TEXT NOT NULL DEFAULT 'digest',
    FOREIGN KEY (runtime_connection_id) REFERENCES runtime_connections(id) ON DELETE CASCADE
);
CREATE TABLE executor_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    executor_id INTEGER NOT NULL UNIQUE,
    last_run_at TEXT,
    last_result TEXT,
    last_error TEXT,
    last_version TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (executor_id) REFERENCES executors(id) ON DELETE CASCADE
);
CREATE TABLE executor_run_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    executor_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    from_version TEXT,
    to_version TEXT,
    message TEXT,
    created_at TEXT NOT NULL, diagnostics TEXT,
    FOREIGN KEY (executor_id) REFERENCES executors(id) ON DELETE CASCADE
);
CREATE INDEX idx_executors_runtime_connection_id ON executors(runtime_connection_id);
CREATE INDEX idx_executors_tracker_name ON executors(tracker_name);
CREATE INDEX idx_executor_status_executor_id ON executor_status(executor_id);
CREATE INDEX idx_executor_run_history_executor_id ON executor_run_history(executor_id);
CREATE TABLE executor_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    executor_id INTEGER NOT NULL UNIQUE,
    snapshot_data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (executor_id) REFERENCES executors(id) ON DELETE CASCADE
);
CREATE INDEX idx_executor_snapshots_executor_id ON executor_snapshots(executor_id);
CREATE TABLE aggregate_trackers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    enabled INTEGER DEFAULT 1,
    changelog_policy TEXT NOT NULL DEFAULT 'primary_source',
    primary_changelog_source_id INTEGER,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (primary_changelog_source_id) REFERENCES aggregate_tracker_sources(id) ON DELETE SET NULL
);
CREATE TABLE aggregate_tracker_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aggregate_tracker_id INTEGER NOT NULL,
    source_key TEXT NOT NULL,
    source_type TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    credential_name TEXT,
    source_config TEXT NOT NULL DEFAULT '{}',
    source_rank INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (aggregate_tracker_id) REFERENCES aggregate_trackers(id) ON DELETE CASCADE,
    UNIQUE(aggregate_tracker_id, source_key)
);
CREATE TABLE source_release_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracker_source_id INTEGER NOT NULL,
    source_release_key TEXT NOT NULL,
    name TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    version TEXT NOT NULL,
    published_at TEXT NOT NULL,
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
CREATE TABLE canonical_releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aggregate_tracker_id INTEGER NOT NULL,
    canonical_key TEXT NOT NULL,
    version TEXT NOT NULL,
    name TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    published_at TEXT NOT NULL,
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
CREATE TABLE canonical_release_observations (
    canonical_release_id INTEGER NOT NULL,
    source_release_observation_id INTEGER NOT NULL,
    contribution_kind TEXT NOT NULL DEFAULT 'supporting',
    created_at TEXT NOT NULL,
    PRIMARY KEY (canonical_release_id, source_release_observation_id),
    FOREIGN KEY (canonical_release_id) REFERENCES canonical_releases(id) ON DELETE CASCADE,
    FOREIGN KEY (source_release_observation_id) REFERENCES source_release_observations(id) ON DELETE CASCADE
);
CREATE INDEX idx_aggregate_trackers_primary_changelog_source_id
    ON aggregate_trackers(primary_changelog_source_id);
CREATE INDEX idx_aggregate_tracker_sources_tracker_id
    ON aggregate_tracker_sources(aggregate_tracker_id);
CREATE INDEX idx_aggregate_tracker_sources_type
    ON aggregate_tracker_sources(source_type);
CREATE INDEX idx_source_release_observations_source_id
    ON source_release_observations(tracker_source_id);
CREATE INDEX idx_source_release_observations_version
    ON source_release_observations(version);
CREATE INDEX idx_canonical_releases_tracker_id
    ON canonical_releases(aggregate_tracker_id);
CREATE INDEX idx_canonical_releases_primary_observation_id
    ON canonical_releases(primary_observation_id);
CREATE INDEX idx_canonical_release_observations_source_observation_id
    ON canonical_release_observations(source_release_observation_id);
CREATE INDEX idx_executors_tracker_source_id ON executors(tracker_source_id);
CREATE TABLE source_fetch_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracker_source_id INTEGER NOT NULL,
    trigger_mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    filtered_in_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (tracker_source_id) REFERENCES aggregate_tracker_sources(id) ON DELETE CASCADE,
    CHECK (trigger_mode IN ('scheduled', 'manual', 'bootstrap')),
    CHECK (status IN ('running', 'success', 'partial', 'failed'))
);
CREATE TABLE source_release_history (
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
    published_at TEXT NOT NULL,
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
CREATE TABLE source_release_run_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_fetch_run_id INTEGER NOT NULL,
    source_release_history_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_fetch_run_id) REFERENCES source_fetch_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (source_release_history_id) REFERENCES source_release_history(id) ON DELETE CASCADE,
    UNIQUE(source_fetch_run_id, source_release_history_id)
);
CREATE TABLE tracker_release_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aggregate_tracker_id INTEGER NOT NULL,
    identity_key TEXT NOT NULL,
    version TEXT NOT NULL,
    digest TEXT,
    digest_algorithm TEXT,
    digest_media_type TEXT,
    digest_platform TEXT,
    primary_source_release_history_id INTEGER NOT NULL,
    created_at TEXT NOT NULL, immutable_key TEXT,
    FOREIGN KEY (aggregate_tracker_id) REFERENCES aggregate_trackers(id) ON DELETE CASCADE,
    FOREIGN KEY (primary_source_release_history_id) REFERENCES source_release_history(id) ON DELETE RESTRICT,
    UNIQUE(aggregate_tracker_id, identity_key)
);
CREATE TABLE tracker_release_history_sources (
    tracker_release_history_id INTEGER NOT NULL,
    source_release_history_id INTEGER NOT NULL,
    contribution_kind TEXT NOT NULL DEFAULT 'supporting',
    created_at TEXT NOT NULL,
    PRIMARY KEY (tracker_release_history_id, source_release_history_id),
    FOREIGN KEY (tracker_release_history_id) REFERENCES tracker_release_history(id) ON DELETE CASCADE,
    FOREIGN KEY (source_release_history_id) REFERENCES source_release_history(id) ON DELETE CASCADE,
    CHECK (contribution_kind IN ('primary', 'supporting'))
);
CREATE TABLE tracker_current_releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aggregate_tracker_id INTEGER NOT NULL,
    identity_key TEXT NOT NULL,
    version TEXT NOT NULL,
    digest TEXT,
    tracker_release_history_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    published_at TEXT NOT NULL,
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
CREATE INDEX idx_source_fetch_runs_tracker_source_id
    ON source_fetch_runs(tracker_source_id);
CREATE INDEX idx_source_fetch_runs_tracker_source_started_at
    ON source_fetch_runs(tracker_source_id, started_at DESC);
CREATE INDEX idx_source_release_history_source_published_at
    ON source_release_history(tracker_source_id, published_at DESC);
CREATE INDEX idx_source_release_history_digest
    ON source_release_history(digest);
CREATE INDEX idx_source_release_run_observations_history_observed_at
    ON source_release_run_observations(source_release_history_id, observed_at DESC);
CREATE INDEX idx_tracker_release_history_tracker_created_at
    ON tracker_release_history(aggregate_tracker_id, created_at DESC);
CREATE INDEX idx_tracker_release_history_tracker_version
    ON tracker_release_history(aggregate_tracker_id, version);
CREATE INDEX idx_tracker_release_history_sources_source_release_history_id
    ON tracker_release_history_sources(source_release_history_id);
CREATE INDEX idx_tracker_current_releases_tracker_published_at
    ON tracker_current_releases(aggregate_tracker_id, published_at DESC);
CREATE TABLE executor_service_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    executor_id INTEGER NOT NULL,
    service TEXT NOT NULL,
    tracker_source_id INTEGER NOT NULL,
    channel_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (executor_id) REFERENCES executors(id) ON DELETE CASCADE,
    FOREIGN KEY (tracker_source_id) REFERENCES aggregate_tracker_sources(id) ON DELETE RESTRICT,
    UNIQUE(executor_id, service)
);
CREATE INDEX idx_executor_service_bindings_executor_id
    ON executor_service_bindings(executor_id);
CREATE INDEX idx_executor_service_bindings_tracker_source_id
    ON executor_service_bindings(tracker_source_id);
CREATE TABLE executor_desired_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    executor_id INTEGER NOT NULL UNIQUE,
    desired_state_revision TEXT NOT NULL,
    desired_target TEXT NOT NULL DEFAULT '{}',
    desired_target_fingerprint TEXT NOT NULL,
    pending INTEGER NOT NULL DEFAULT 1,
    next_eligible_at TEXT,
    claimed_by TEXT,
    claimed_at TEXT,
    claim_until TEXT,
    last_completed_revision TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (executor_id) REFERENCES executors(id) ON DELETE CASCADE
);
CREATE INDEX idx_executor_desired_state_pending_claim
    ON executor_desired_state(pending, claim_until, next_eligible_at);
CREATE INDEX idx_executor_desired_state_revision
    ON executor_desired_state(desired_state_revision);
CREATE UNIQUE INDEX idx_source_release_history_immutable_key
    ON source_release_history(tracker_source_id, immutable_key);
CREATE UNIQUE INDEX idx_tracker_release_history_immutable_key
    ON tracker_release_history(aggregate_tracker_id, immutable_key);
CREATE UNIQUE INDEX idx_tracker_current_releases_immutable_key
    ON tracker_current_releases(aggregate_tracker_id, immutable_key);
-- Dbmate schema migrations
INSERT INTO "schema_migrations" (version) VALUES
  ('20000101000001');
