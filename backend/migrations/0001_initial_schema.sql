-- depends:

-- 发布记录表（核心表）
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracker_name TEXT NOT NULL,
    name TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    version TEXT NOT NULL,
    published_at TEXT NOT NULL,
    url TEXT NOT NULL,
    prerelease INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(tracker_name, tag_name)
);

-- 全局设置表
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 追踪器运行状态表
CREATE TABLE IF NOT EXISTS tracker_status (
    name TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    last_check TEXT,
    last_version TEXT,
    error TEXT
);

-- 凭证表
CREATE TABLE IF NOT EXISTS credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    token TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Webhook 通知器表
CREATE TABLE IF NOT EXISTS notifiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    url TEXT NOT NULL,
    events TEXT DEFAULT '["new_release"]',
    enabled INTEGER DEFAULT 1,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 追踪器配置表
CREATE TABLE IF NOT EXISTS trackers (
    name TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    repo TEXT,
    project TEXT,
    instance TEXT,
    chart TEXT,
    credential_name TEXT,
    channels TEXT DEFAULT '[]',
    interval TEXT DEFAULT '1h',
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 版本历史记录表
CREATE TABLE IF NOT EXISTS release_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id INTEGER NOT NULL,
    commit_sha TEXT NOT NULL,
    published_at TEXT NOT NULL,
    body TEXT,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY (release_id) REFERENCES releases(id) ON DELETE CASCADE
);

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

-- 会话表
CREATE TABLE IF NOT EXISTS sessions (
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
