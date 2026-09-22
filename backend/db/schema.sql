CREATE TABLE schema_migrations (version TEXT PRIMARY KEY);
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
, language TEXT NOT NULL DEFAULT 'en', template_id INTEGER REFERENCES notification_templates(id));
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
    updated_at TEXT NOT NULL, image_selection_mode TEXT NOT NULL DEFAULT 'replace_tag_on_current_image', channel_name TEXT, tracker_source_id INTEGER, image_reference_mode TEXT NOT NULL DEFAULT 'digest', health_check TEXT NOT NULL DEFAULT '{"strategy":"none","use_default_strategy":false,"failure_policy":"mark_failed","grace_period_seconds":0,"attempt_timeout_seconds":0,"interval_seconds":0,"probe_window_seconds":0,"services":null,"http":null,"tcp":null}',
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
CREATE TABLE aggregate_trackers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    enabled INTEGER DEFAULT 1,
    changelog_policy TEXT NOT NULL DEFAULT 'primary_source',
    primary_changelog_source_id INTEGER,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, release_notes_config TEXT NOT NULL DEFAULT '{"source":"release_notes"}',
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
    created_at TEXT NOT NULL, immutable_key TEXT, merged_into_tracker_release_history_id INTEGER
    REFERENCES tracker_release_history(id) ON DELETE SET NULL,
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
CREATE INDEX idx_executors_runtime_connection_id ON executors(runtime_connection_id);
CREATE INDEX idx_executors_tracker_name ON executors(tracker_name);
CREATE INDEX idx_executor_status_executor_id ON executor_status(executor_id);
CREATE INDEX idx_executor_run_history_executor_id ON executor_run_history(executor_id);
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
CREATE INDEX idx_executor_service_bindings_executor_id
    ON executor_service_bindings(executor_id);
CREATE INDEX idx_executor_service_bindings_tracker_source_id
    ON executor_service_bindings(tracker_source_id);
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
CREATE TABLE IF NOT EXISTS "executor_snapshots" (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    executor_id INTEGER NOT NULL,
    snapshot_data TEXT NOT NULL DEFAULT '{}',
    trigger TEXT NOT NULL DEFAULT 'pre_update'
        CHECK (trigger IN ('pre_update', 'manual', 'pre_rollback')),
    image_at_capture TEXT,
    executor_run_id INTEGER,
    unredacted_persisted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, locked INTEGER NOT NULL DEFAULT 0, snapshot_format_version INTEGER, snapshot_sha256 TEXT, snapshot_size_bytes INTEGER,
    FOREIGN KEY (executor_id) REFERENCES executors(id) ON DELETE CASCADE,
    FOREIGN KEY (executor_run_id) REFERENCES executor_run_history(id) ON DELETE SET NULL
);
CREATE INDEX idx_executor_snapshots_executor_id
    ON executor_snapshots(executor_id);
CREATE INDEX idx_executor_snapshots_executor_created
    ON executor_snapshots(executor_id, created_at DESC);
CREATE INDEX idx_executor_snapshots_executor_run_id
    ON executor_snapshots(executor_run_id);
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
CREATE TABLE IF NOT EXISTS "source_fetch_runs" (
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
    CHECK (trigger_mode IN ('scheduled', 'manual', 'bootstrap', 'webhook')),
    CHECK (status IN ('running', 'success', 'partial', 'failed'))
);
CREATE INDEX idx_source_fetch_runs_tracker_source_id ON source_fetch_runs(tracker_source_id);
CREATE INDEX idx_source_fetch_runs_tracker_source_started_at ON source_fetch_runs(tracker_source_id, started_at DESC);
CREATE TABLE repository_webhooks (
    id TEXT PRIMARY KEY,
    tracker_source_id INTEGER NOT NULL UNIQUE REFERENCES aggregate_tracker_sources(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    auth_mode TEXT NOT NULL,
    secret TEXT NOT NULL,
    config TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE webhook_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    webhook_id TEXT NOT NULL REFERENCES repository_webhooks(id) ON DELETE CASCADE,
    delivery_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    summary TEXT NOT NULL,
    state TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    duplicates INTEGER NOT NULL DEFAULT 0,
    received_at REAL NOT NULL,
    UNIQUE(webhook_id, delivery_key)
);
CREATE INDEX idx_webhook_deliveries_received ON webhook_deliveries(webhook_id, received_at DESC);
CREATE TABLE source_refresh_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id INTEGER NOT NULL REFERENCES webhook_deliveries(id) ON DELETE CASCADE,
    tracker_source_id INTEGER NOT NULL REFERENCES aggregate_tracker_sources(id) ON DELETE CASCADE,
    webhook_generation INTEGER NOT NULL,
    source_identity TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    due_at REAL NOT NULL,
    lease_until REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    source_fetch_run_id INTEGER REFERENCES source_fetch_runs(id) ON DELETE SET NULL, task_id INTEGER REFERENCES tasks(id),
    UNIQUE(delivery_id, tracker_source_id)
);
CREATE INDEX idx_source_refresh_due ON source_refresh_requests(state, due_at);
CREATE TABLE ssh_compose_ownership (
    executor_id INTEGER PRIMARY KEY REFERENCES executors(id) ON DELETE CASCADE,
    runtime_key TEXT,
    project TEXT NOT NULL,
    working_dir TEXT NOT NULL,
    UNIQUE(runtime_key, project)
);
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('fetch', 'deploy', 'recover')),
    resource_key TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    target_label TEXT NOT NULL,
    payload TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN (
        'queued','running','retry_wait','succeeded','no_change','skipped',
        'failed','cancelled','superseded','needs_attention'
    )),
    max_retries INTEGER NOT NULL CHECK(max_retries BETWEEN 0 AND 10),
    attempts INTEGER NOT NULL DEFAULT 0,
    due_at REAL NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    owner TEXT,
    lease_until REAL,
    error_code TEXT,
    message TEXT,
    result TEXT
, cleared_at REAL, approval_pending INTEGER NOT NULL DEFAULT 0 CHECK(approval_pending IN (0,1)));
CREATE INDEX tasks_dispatch ON tasks(kind,state,due_at,id);
CREATE INDEX tasks_resource ON tasks(resource_key,state);
CREATE INDEX tasks_pending_dedupe ON tasks(dedupe_key)
    WHERE state IN ('queued','retry_wait');
CREATE UNIQUE INDEX tasks_running_resource ON tasks(resource_key)
    WHERE state='running';
CREATE TABLE task_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    attempt INTEGER NOT NULL,
    owner TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    state TEXT NOT NULL,
    error_code TEXT,
    message TEXT,
    result TEXT,
    UNIQUE(task_id, attempt)
);
CREATE TABLE task_triggers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    trigger_mode TEXT NOT NULL,
    trigger_key TEXT UNIQUE,
    created_at REAL NOT NULL
);
CREATE INDEX task_triggers_task ON task_triggers(task_id);
CREATE INDEX source_refresh_task ON source_refresh_requests(task_id);
CREATE INDEX tasks_visible ON tasks(id DESC) WHERE cleared_at IS NULL;
CREATE TABLE deployment_observations (
    task_id INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    run_id INTEGER NOT NULL REFERENCES executor_run_history(id) ON DELETE CASCADE,
    executor_id INTEGER NOT NULL,
    resource_scope TEXT NOT NULL,
    executor_config TEXT NOT NULL,
    verification TEXT NOT NULL,
    finalization TEXT NOT NULL,
    started_at REAL NOT NULL,
    deadline REAL NOT NULL,
    due_at REAL NOT NULL,
    stable_since REAL,
    state TEXT NOT NULL DEFAULT 'waiting' CHECK(state IN ('waiting','finalizing','completed','blocked')),
    outcome TEXT,
    result TEXT,
    finished_at REAL
);
CREATE INDEX deployment_observations_due ON deployment_observations(state,due_at);
CREATE TABLE executor_notification_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    notifier_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    final_result TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sending', 'delivered', 'failed', 'discarded')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL,
    created_at REAL NOT NULL,
    delivered_at REAL,
    UNIQUE(run_id, notifier_id, final_result)
);
CREATE INDEX executor_notification_outbox_due ON executor_notification_outbox(status, available_at);
CREATE TABLE executor_notification_intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    final_result TEXT NOT NULL,
    payload TEXT NOT NULL,
    notify_health_result INTEGER NOT NULL CHECK (notify_health_result IN (0,1)),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','expanded')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL,
    created_at REAL NOT NULL,
    expanded_at REAL,
    UNIQUE(run_id,final_result)
);
CREATE INDEX executor_notification_intents_due ON executor_notification_intents(status,available_at);
CREATE TABLE notification_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    translations TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TRIGGER notifier_template_insert BEFORE INSERT ON notifiers
WHEN NEW.template_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM notification_templates WHERE id=NEW.template_id)
BEGIN SELECT RAISE(ABORT, 'notification_template_not_found'); END;
CREATE TRIGGER notifier_template_update BEFORE UPDATE OF template_id ON notifiers
WHEN NEW.template_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM notification_templates WHERE id=NEW.template_id)
BEGIN SELECT RAISE(ABORT, 'notification_template_not_found'); END;
CREATE TRIGGER notification_template_in_use BEFORE DELETE ON notification_templates
WHEN EXISTS (SELECT 1 FROM notifiers WHERE template_id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'notification_template_in_use'); END;
CREATE TABLE managed_deployment_identity (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    installation_id TEXT NOT NULL
);
CREATE TABLE managed_targets (
    target_id TEXT PRIMARY KEY,
    executor_id INTEGER NOT NULL UNIQUE,
    identity_key TEXT NOT NULL UNIQUE,
    baseline TEXT,
    last_task_id INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE deployment_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    executor_id INTEGER NOT NULL,
    target_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    summary TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','approved','applied','superseded','blocked','cancelled')),
    reason TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    approved_at REAL,
    approved_by TEXT,
    applied_at REAL
);
CREATE INDEX deployment_plans_task ON deployment_plans(task_id,id);
CREATE TABLE deployment_admission_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES deployment_plans(id),
    event TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    expanded_at REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    due_at REAL NOT NULL,
    UNIQUE(plan_id,event)
);
CREATE TABLE deployment_admission_notification_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admission_event_id INTEGER NOT NULL REFERENCES deployment_admission_events(id),
    notifier_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','sending','delivered','failed','discarded')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL,
    created_at REAL NOT NULL,
    delivered_at REAL,
    UNIQUE(admission_event_id, notifier_id)
);
CREATE INDEX deployment_admission_notification_due ON deployment_admission_notification_outbox(status, available_at, id);
CREATE TABLE podman_target_lineages (
 executor_id INTEGER PRIMARY KEY,
 target_id TEXT NOT NULL UNIQUE,
 mode TEXT NOT NULL CHECK(mode IN ('container','docker_compose')),
 target_fingerprint TEXT NOT NULL,
 generation INTEGER NOT NULL CHECK(generation>=1),
 members TEXT NOT NULL,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL
);
CREATE TABLE podman_target_lineage_transitions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 executor_id INTEGER NOT NULL,
 task_id INTEGER,
 executor_run_id INTEGER,
 from_generation INTEGER NOT NULL,
 to_generation INTEGER NOT NULL,
 old_members TEXT NOT NULL,
 new_members TEXT NOT NULL,
 created_at REAL NOT NULL,
 CHECK((task_id IS NOT NULL) != (executor_run_id IS NOT NULL)),
 UNIQUE(executor_id,to_generation)
);
CREATE INDEX podman_lineage_transition_executor ON podman_target_lineage_transitions(executor_id,id);
-- Dbmate schema migrations
INSERT INTO "schema_migrations" (version) VALUES
  ('20000101000001'),
  ('20260508152003'),
  ('20260508153215'),
  ('20260513000001'),
  ('20260517000001'),
  ('20260808000001'),
  ('20260809000001'),
  ('20260906000001'),
  ('20260916000001'),
  ('20260916000002'),
  ('20260918000001'),
  ('20260919000001'),
  ('20260919000002'),
  ('20260920000001'),
  ('20260920000002'),
  ('20260920000003'),
  ('20260920000004'),
  ('20260921000001'),
  ('20260922000001'),
  ('20260922000002'),
  ('20260922000003');
