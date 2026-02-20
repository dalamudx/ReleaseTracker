-- depends: 0004_users_oidc_fields

CREATE TABLE IF NOT EXISTS oauth_providers (
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

CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    provider_slug TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
