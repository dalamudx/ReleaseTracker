from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiosqlite

from ..models import Session, User
from ..oidc_models import OIDCProvider, OAuthState

if TYPE_CHECKING:
    from .sqlite import SQLiteStorage


ADMIN_USER_ID_SETTING_KEY = "system.admin_user_id"
BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY = "system.bootstrap_admin_initialized"
ADMIN_OIDC_ISSUER_SETTING_KEY = "system.admin_oidc_issuer"
ADMIN_OIDC_SUBJECT_SETTING_KEY = "system.admin_oidc_subject"
RESERVED_AUTH_SETTING_KEYS = frozenset(
    {
        ADMIN_USER_ID_SETTING_KEY,
        BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY,
        ADMIN_OIDC_ISSUER_SETTING_KEY,
        ADMIN_OIDC_SUBJECT_SETTING_KEY,
    }
)


async def _upsert_setting(db: aiosqlite.Connection, key: str, value: str) -> None:
    await db.execute(
        """
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value, datetime.now().isoformat()),
    )


async def get_admin_user_id(storage: "SQLiteStorage") -> int | None:
    value = await storage.get_setting(ADMIN_USER_ID_SETTING_KEY)
    if value is None:
        return None
    try:
        user_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("system.admin_user_id must be a positive integer") from exc
    if user_id <= 0:
        raise ValueError("system.admin_user_id must be a positive integer")
    return user_id


async def persist_admin_identity(storage: "SQLiteStorage", user_id: int) -> None:
    if user_id <= 0:
        raise ValueError("admin user id must be positive")
    db = await storage._get_connection()
    try:
        await _upsert_setting(db, ADMIN_USER_ID_SETTING_KEY, str(user_id))
        await _upsert_setting(db, BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY, "true")
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def create_bootstrap_admin(storage: "SQLiteStorage", user: User) -> User:
    """Create the bootstrap user and both identity markers in one transaction."""
    db = await storage._get_connection()
    try:
        cursor = await db.execute(
            """
            INSERT INTO users
            (username, email, password_hash, oauth_provider, oauth_sub, avatar_url, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user.username,
                user.email,
                user.password_hash,
                user.oauth_provider,
                user.oauth_sub,
                user.avatar_url,
                user.status,
                user.created_at.isoformat(),
            ),
        )
        user_id = cursor.lastrowid
        if user_id is None:
            raise RuntimeError("Failed to persist bootstrap admin")
        await _upsert_setting(db, ADMIN_USER_ID_SETTING_KEY, str(user_id))
        await _upsert_setting(db, BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY, "true")
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    created_user = user.model_copy()
    created_user.id = user_id
    return created_user


async def get_admin_oidc_binding(storage: "SQLiteStorage") -> tuple[str, str] | None:
    issuer = await storage.get_setting(ADMIN_OIDC_ISSUER_SETTING_KEY)
    subject = await storage.get_setting(ADMIN_OIDC_SUBJECT_SETTING_KEY)
    if issuer is None and subject is None:
        return None
    if issuer is None or subject is None or not issuer.strip() or not subject.strip():
        raise RuntimeError("OIDC administrator binding is incomplete")
    return issuer, subject


async def bind_admin_oidc_identity(
    storage: "SQLiteStorage", issuer: str, subject: str
) -> tuple[str, str]:
    normalized_issuer = issuer.strip()
    normalized_subject = subject.strip()
    if not normalized_issuer or not normalized_subject:
        raise ValueError("OIDC issuer and subject must be non-empty")

    existing = await get_admin_oidc_binding(storage)
    requested = (normalized_issuer, normalized_subject)
    if existing is not None and existing != requested:
        raise ValueError("An OIDC administrator identity is already bound")
    if existing == requested:
        return requested

    db = await storage._get_connection()
    try:
        await _upsert_setting(db, ADMIN_OIDC_ISSUER_SETTING_KEY, normalized_issuer)
        await _upsert_setting(db, ADMIN_OIDC_SUBJECT_SETTING_KEY, normalized_subject)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return requested


async def unbind_admin_oidc_identity(storage: "SQLiteStorage") -> None:
    db = await storage._get_connection()
    try:
        await db.execute(
            "DELETE FROM settings WHERE key IN (?, ?)",
            (ADMIN_OIDC_ISSUER_SETTING_KEY, ADMIN_OIDC_SUBJECT_SETTING_KEY),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def create_user(storage: "SQLiteStorage", user: User) -> User:
    db = await storage._get_connection()
    cursor = await db.execute(
        """
        INSERT INTO users
        (username, email, password_hash, oauth_provider, oauth_sub, avatar_url, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user.username,
            user.email,
            user.password_hash,
            user.oauth_provider,
            user.oauth_sub,
            user.avatar_url,
            user.status,
            user.created_at.isoformat(),
        ),
    )
    user_id = cursor.lastrowid
    await db.commit()

    created_user = user.model_copy()
    created_user.id = user_id
    return created_user


async def get_user_by_username(storage: "SQLiteStorage", username: str) -> User | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = await cursor.fetchone()
    return _row_to_user(row) if row else None


async def get_user_by_id(storage: "SQLiteStorage", user_id: int) -> User | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = await cursor.fetchone()
    return _row_to_user(row) if row else None


async def update_user_password(storage: "SQLiteStorage", user_id: int, password_hash: str) -> bool:
    db = await storage._get_connection()
    await db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    await db.commit()
    return True


async def create_session(storage: "SQLiteStorage", session: Session) -> Session:
    db = await storage._get_connection()
    cursor = await db.execute(
        """
        INSERT INTO sessions
        (user_id, token_hash, refresh_token_hash, user_agent, ip_address, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session.user_id,
            session.token_hash,
            session.refresh_token_hash,
            session.user_agent,
            session.ip_address,
            session.expires_at.isoformat(),
            session.created_at.isoformat(),
        ),
    )
    session_id = cursor.lastrowid
    await db.commit()

    created_session = session.model_copy()
    created_session.id = session_id
    return created_session


async def get_session(storage: "SQLiteStorage", token_hash: str) -> Session | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,))
    row = await cursor.fetchone()
    return _row_to_session(row) if row else None


async def get_session_by_refresh_token(
    storage: "SQLiteStorage", refresh_token_hash: str
) -> Session | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        "SELECT * FROM sessions WHERE refresh_token_hash = ?", (refresh_token_hash,)
    )
    row = await cursor.fetchone()
    return _row_to_session(row) if row else None


async def delete_session(storage: "SQLiteStorage", token_hash: str) -> None:
    db = await storage._get_connection()
    await db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
    await db.commit()


async def delete_all_sessions(storage: "SQLiteStorage") -> int:
    db = await storage._get_connection()
    result = await db.execute("DELETE FROM sessions")
    await db.commit()
    return result.rowcount or 0


async def count_active_sessions(storage: "SQLiteStorage") -> int:
    now = datetime.now().isoformat()
    db = await storage._get_connection()
    cursor = await db.execute("SELECT COUNT(*) FROM sessions WHERE expires_at >= ?", (now,))
    row = await cursor.fetchone()
    return row[0] if row else 0


async def update_session_tokens(
    storage: "SQLiteStorage",
    session_id: int,
    current_refresh_token_hash: str,
    token_hash: str,
    refresh_token_hash: str,
    expires_at: datetime,
) -> bool:
    db = await storage._get_connection()
    cursor = await db.execute(
        """
        UPDATE sessions
        SET token_hash = ?, refresh_token_hash = ?, expires_at = ?
        WHERE id = ? AND refresh_token_hash = ?
        """,
        (
            token_hash,
            refresh_token_hash,
            expires_at.isoformat(),
            session_id,
            current_refresh_token_hash,
        ),
    )
    await db.commit()
    return cursor.rowcount == 1


async def delete_expired_sessions(storage: "SQLiteStorage") -> None:
    now = datetime.now().isoformat()
    db = await storage._get_connection()
    await db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    await db.commit()


def _row_to_user(row: Any) -> User:
    keys = row.keys() if hasattr(row, "keys") else []
    return User(
        id=row["id"],
        username=row["username"],
        email=row["email"],
        password_hash=row["password_hash"],
        oauth_provider=row["oauth_provider"] if "oauth_provider" in keys else None,
        oauth_sub=row["oauth_sub"] if "oauth_sub" in keys else None,
        avatar_url=row["avatar_url"] if "avatar_url" in keys else None,
        status=row["status"] or "active",
        created_at=datetime.fromisoformat(row["created_at"]),
        last_login_at=(
            datetime.fromisoformat(row["last_login_at"]) if row["last_login_at"] else None
        ),
    )


def _row_to_session(row: Any) -> Session:
    return Session(
        id=row["id"],
        user_id=row["user_id"],
        token_hash=row["token_hash"],
        refresh_token_hash=row["refresh_token_hash"],
        user_agent=row["user_agent"],
        ip_address=row["ip_address"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


async def save_oauth_provider(storage: "SQLiteStorage", provider: OIDCProvider) -> OIDCProvider:
    now = datetime.now().isoformat()
    db = await storage._get_connection()
    cursor = await db.execute(
        """
        INSERT INTO oauth_providers
        (name, slug, issuer_url, discovery_enabled, client_id, client_secret,
         authorization_url, token_url, userinfo_url, jwks_uri, scopes,
         enabled, icon_url, description, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            provider.name,
            provider.slug,
            provider.issuer_url,
            1 if provider.discovery_enabled else 0,
            provider.client_id,
            storage._encrypt(provider.client_secret) if provider.client_secret else None,
            provider.authorization_url,
            provider.token_url,
            provider.userinfo_url,
            provider.jwks_uri,
            provider.scopes,
            1 if provider.enabled else 0,
            provider.icon_url,
            provider.description,
            now,
            now,
        ),
    )
    provider_id = cursor.lastrowid
    await db.commit()
    result = provider.model_copy()
    result.id = provider_id
    return result


async def get_total_oauth_providers_count(storage: "SQLiteStorage") -> int:
    db = await storage._get_connection()
    cursor = await db.execute("SELECT COUNT(*) FROM oauth_providers")
    row = await cursor.fetchone()
    return row[0] if row else 0


async def list_oauth_providers(
    storage: "SQLiteStorage", enabled_only: bool = False
) -> list[OIDCProvider]:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    if enabled_only:
        cursor = await db.execute("SELECT * FROM oauth_providers WHERE enabled = 1 ORDER BY name")
    else:
        cursor = await db.execute("SELECT * FROM oauth_providers ORDER BY name")
    rows = await cursor.fetchall()
    return [_row_to_oidc_provider(storage, row) for row in rows]


async def get_oauth_provider(storage: "SQLiteStorage", slug: str) -> OIDCProvider | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM oauth_providers WHERE slug = ?", (slug,))
    row = await cursor.fetchone()
    return _row_to_oidc_provider(storage, row, decrypt_secret=True) if row else None


async def get_oauth_provider_by_id(
    storage: "SQLiteStorage", provider_id: int
) -> OIDCProvider | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM oauth_providers WHERE id = ?", (provider_id,))
    row = await cursor.fetchone()
    return _row_to_oidc_provider(storage, row, decrypt_secret=True) if row else None


async def update_oauth_provider(
    storage: "SQLiteStorage", provider_id: int, provider: OIDCProvider
) -> None:
    now = datetime.now().isoformat()
    db = await storage._get_connection()
    if provider.client_secret:
        await db.execute(
            """
            UPDATE oauth_providers SET
            name=?, issuer_url=?, discovery_enabled=?, client_id=?, client_secret=?,
            authorization_url=?, token_url=?, userinfo_url=?, jwks_uri=?,
            scopes=?, enabled=?, icon_url=?, description=?, updated_at=?
            WHERE id=?
            """,
            (
                provider.name,
                provider.issuer_url,
                1 if provider.discovery_enabled else 0,
                provider.client_id,
                storage._encrypt(provider.client_secret),
                provider.authorization_url,
                provider.token_url,
                provider.userinfo_url,
                provider.jwks_uri,
                provider.scopes,
                1 if provider.enabled else 0,
                provider.icon_url,
                provider.description,
                now,
                provider_id,
            ),
        )
    else:
        await db.execute(
            """
            UPDATE oauth_providers SET
            name=?, issuer_url=?, discovery_enabled=?, client_id=?,
            authorization_url=?, token_url=?, userinfo_url=?, jwks_uri=?,
            scopes=?, enabled=?, icon_url=?, description=?, updated_at=?
            WHERE id=?
            """,
            (
                provider.name,
                provider.issuer_url,
                1 if provider.discovery_enabled else 0,
                provider.client_id,
                provider.authorization_url,
                provider.token_url,
                provider.userinfo_url,
                provider.jwks_uri,
                provider.scopes,
                1 if provider.enabled else 0,
                provider.icon_url,
                provider.description,
                now,
                provider_id,
            ),
        )
    await db.commit()


async def delete_oauth_provider(storage: "SQLiteStorage", provider_id: int) -> None:
    db = await storage._get_connection()
    await db.execute("DELETE FROM oauth_providers WHERE id = ?", (provider_id,))
    await db.commit()


def _row_to_oidc_provider(
    storage: "SQLiteStorage", row: Any, decrypt_secret: bool = False
) -> OIDCProvider:
    secret = None
    if decrypt_secret and row["client_secret"]:
        secret = storage._decrypt(row["client_secret"])
    return OIDCProvider(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        issuer_url=row["issuer_url"],
        discovery_enabled=bool(row["discovery_enabled"]),
        client_id=row["client_id"],
        client_secret=secret,
        authorization_url=row["authorization_url"],
        token_url=row["token_url"],
        userinfo_url=row["userinfo_url"],
        jwks_uri=row["jwks_uri"],
        scopes=row["scopes"] or "openid email profile",
        enabled=bool(row["enabled"]),
        icon_url=row["icon_url"],
        description=row["description"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


async def save_oauth_state(
    storage: "SQLiteStorage",
    state: str,
    provider_slug: str,
    code_verifier: str,
    nonce: str,
    flow_type: str,
    initiating_admin_user_id: int | None = None,
) -> None:
    if flow_type not in {"login", "bind"}:
        raise ValueError("OAuth flow type must be login or bind")
    if flow_type == "bind" and initiating_admin_user_id is None:
        raise ValueError("Binding flow requires an initiating administrator")
    if flow_type == "login" and initiating_admin_user_id is not None:
        raise ValueError("Login flow cannot have an initiating administrator")

    expires_at = (datetime.now() + timedelta(minutes=10)).isoformat()
    db = await storage._get_connection()
    await db.execute(
        """
        INSERT OR REPLACE INTO oauth_states
        (state, provider_slug, code_verifier, nonce, flow_type,
         initiating_admin_user_id, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            state,
            provider_slug,
            code_verifier,
            nonce,
            flow_type,
            initiating_admin_user_id,
            expires_at,
        ),
    )
    await db.commit()


async def get_and_delete_oauth_state(storage: "SQLiteStorage", state: str) -> OAuthState | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM oauth_states WHERE state = ?", (state,))
    row = await cursor.fetchone()
    if not row:
        return None
    await db.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    await db.commit()
    return OAuthState(
        state=row["state"],
        provider_slug=row["provider_slug"],
        code_verifier=row["code_verifier"],
        nonce=row["nonce"],
        flow_type=row["flow_type"],
        initiating_admin_user_id=row["initiating_admin_user_id"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
    )


async def cleanup_expired_oauth_states(storage: "SQLiteStorage") -> None:
    now = datetime.now().isoformat()
    db = await storage._get_connection()
    await db.execute("DELETE FROM oauth_states WHERE expires_at < ?", (now,))
    await db.commit()


async def get_user_by_oauth(storage: "SQLiteStorage", provider: str, oauth_sub: str) -> User | None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        "SELECT * FROM users WHERE oauth_provider = ? AND oauth_sub = ?",
        (provider, oauth_sub),
    )
    row = await cursor.fetchone()
    return _row_to_user(row) if row else None


async def link_oauth_to_user(
    storage: "SQLiteStorage",
    user_id: int,
    provider: str,
    oauth_sub: str,
    avatar_url: str | None = None,
) -> None:
    db = await storage._get_connection()
    await db.execute(
        "UPDATE users SET oauth_provider = ?, oauth_sub = ?, avatar_url = ? WHERE id = ?",
        (provider, oauth_sub, avatar_url, user_id),
    )
    await db.commit()


async def update_user_oidc_info(
    storage: "SQLiteStorage",
    user_id: int,
    username: str | None = None,
    email: str | None = None,
    avatar_url: str | None = None,
) -> None:
    db = await storage._get_connection()
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        "SELECT username, email, avatar_url FROM users WHERE id = ?", (user_id,)
    )
    row = await cursor.fetchone()
    if not row:
        return

    next_username = row["username"]
    if username and username != row["username"]:
        cursor = await db.execute(
            "SELECT id FROM users WHERE username = ? AND id != ?", (username, user_id)
        )
        if not await cursor.fetchone():
            next_username = username

    next_email = row["email"]
    if email and email != row["email"]:
        cursor = await db.execute(
            "SELECT id FROM users WHERE email = ? AND id != ?", (email, user_id)
        )
        if not await cursor.fetchone():
            next_email = email

    next_avatar_url = avatar_url if avatar_url else row["avatar_url"]
    await db.execute(
        "UPDATE users SET username = ?, email = ?, avatar_url = ? WHERE id = ?",
        (next_username, next_email, next_avatar_url, user_id),
    )
    await db.commit()
