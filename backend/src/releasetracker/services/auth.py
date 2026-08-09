"""Authentication service module"""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from passlib.context import CryptContext

from .jwt_tokens import JWTTokenError, decode_jwt, encode_jwt

from ..models import (
    User,
    Session,
    LoginRequest,
    RegisterRequest,
    TokenPair,
    ChangePasswordRequest,
)
from ..storage.sqlite import (
    BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY as STORAGE_BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY,
    SQLiteStorage,
)

if TYPE_CHECKING:
    from .system_keys import SystemKeyManager

logger = logging.getLogger(__name__)

# Password hashing configuration
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT Configuration
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7
BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY = STORAGE_BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY
LEGACY_ADMIN_PASSWORD = "admin"


class AuthService:
    """Authentication service"""

    def __init__(self, storage: SQLiteStorage, system_key_manager: "SystemKeyManager"):
        self.storage = storage
        self.system_key_manager = system_key_manager

    @property
    def secret_key(self) -> str:
        return self.system_key_manager.jwt_secret

    async def register(self, req: RegisterRequest) -> User:
        """Fail closed: single-admin mode never permits registration."""
        raise ValueError("Registration is disabled in single-admin mode")

    async def login(
        self, req: LoginRequest, user_agent: Optional[str] = None, ip_address: Optional[str] = None
    ) -> tuple[User, TokenPair]:
        """User login"""
        await self.ensure_password_reset_not_required()
        user = await self.storage.get_user_by_username(req.username)
        if not user:
            raise ValueError("Invalid credentials")

        if not user.password_hash or not pwd_context.verify(req.password, user.password_hash):
            raise ValueError("Invalid credentials")

        if user.status != "active":
            raise ValueError("User account is not active")

        if user.id is None:
            raise ValueError("User not found")

        # Generate tokens
        token_pair = self._create_token_pair(user)

        # Create a session
        session = Session(
            user_id=user.id,
            token_hash=self._hash_token(token_pair.access_token),
            refresh_token_hash=self._hash_token(token_pair.refresh_token),
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=datetime.fromtimestamp(
                decode_jwt(token_pair.access_token, self.secret_key)["exp"]
            ),
        )

        await self.storage.create_session(session)

        return user, token_pair

    async def logout(self, token: str) -> None:
        """User logout"""
        token_hash = self._hash_token(token)
        await self.storage.delete_session(token_hash)

    async def change_password(self, token: str, req: ChangePasswordRequest) -> None:
        """Change password"""
        user = await self.get_current_user(token)

        # Verify the old password
        if not self.verify_password(user, req.old_password):
            raise ValueError("Invalid old password")

        # Update password
        if user.id is None:
            raise ValueError("User not found")

        new_password_hash = pwd_context.hash(req.new_password)
        await self.storage.update_user_password(user.id, new_password_hash)

    async def refresh_token(self, refresh_token: str) -> TokenPair:
        """Refresh token"""
        await self.ensure_password_reset_not_required()
        try:
            payload = decode_jwt(refresh_token, self.secret_key)
            subject = payload.get("sub")
            token_type = payload.get("type")

            if token_type != "refresh" or not subject:
                raise ValueError("Invalid token type")

            refresh_hash = self._hash_token(refresh_token)
            session = await self.storage.get_session_by_refresh_token(refresh_hash)
            if not session:
                raise ValueError("Invalid refresh token")

            user = await self.storage.get_user_by_id(session.user_id)
            if not user or str(user.id) != str(subject) or user.status != "active":
                raise ValueError("Invalid refresh token")

            if session.id is None:
                raise ValueError("Invalid refresh token")

            token_pair = self._create_token_pair(user)
            session_updated = await self.storage.update_session_tokens(
                session_id=session.id,
                current_refresh_token_hash=refresh_hash,
                token_hash=self._hash_token(token_pair.access_token),
                refresh_token_hash=self._hash_token(token_pair.refresh_token),
                expires_at=datetime.fromtimestamp(
                    decode_jwt(token_pair.access_token, self.secret_key)["exp"]
                ),
            )
            if not session_updated:
                raise ValueError("Invalid refresh token")
            return token_pair

        except JWTTokenError:
            raise ValueError("Invalid refresh token")

    async def get_current_user(self, token: str) -> User:
        """Get the current user from a token"""
        await self.ensure_password_reset_not_required()
        try:
            payload = decode_jwt(token, self.secret_key)
            subject = payload.get("sub")
            if subject is None:
                raise ValueError("Invalid token")

            token_hash = self._hash_token(token)
            session = await self.storage.get_session(token_hash)
            if not session:
                raise ValueError("Session expired or invalid")

            if session.expires_at < datetime.now():
                await self.storage.delete_session(token_hash)
                raise ValueError("Session expired")

            user = await self.storage.get_user_by_id(session.user_id)
            if (
                not user
                or user.status != "active"
                or str(session.user_id) != str(subject)
                or payload.get("type") != "access"
            ):
                raise ValueError("Invalid token")

            return user

        except JWTTokenError:
            raise ValueError("Invalid token")

    def _create_token_pair(self, user: User) -> TokenPair:
        """Generate a token pair"""
        if user.id is None:
            raise ValueError("User not found")
        claims = {
            "sub": str(user.id),
        }

        access_token = self._create_access_token(data=claims)
        refresh_token = self._create_refresh_token(data=claims)

        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    def _create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None):
        to_encode = data.copy()
        to_encode.update({"type": "access"})
        to_encode.update({"jti": uuid.uuid4().hex})
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        to_encode.update({"exp": expire})
        encoded_jwt = encode_jwt(to_encode, self.secret_key)
        return encoded_jwt

    def _create_refresh_token(self, data: dict, expires_delta: Optional[timedelta] = None):
        to_encode = data.copy()
        to_encode.update({"type": "refresh"})
        to_encode.update({"jti": uuid.uuid4().hex})
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        to_encode.update({"exp": expire})
        encoded_jwt = encode_jwt(to_encode, self.secret_key)
        return encoded_jwt

    def verify_password(self, user: User, password: str) -> bool:
        """Verify a local password after the caller has checked reset state."""
        return bool(user.password_hash) and pwd_context.verify(password, user.password_hash)

    async def verify_password_for_reauth(self, user: User, password: str) -> bool:
        """Verify a re-authentication password while honoring the reset-required marker."""
        await self.ensure_password_reset_not_required()
        return self.verify_password(user, password)

    async def ensure_password_reset_not_required(self) -> None:
        """Fail closed while a known legacy administrator password awaits operator reset."""
        if await self.storage.is_admin_password_reset_required():
            raise ValueError("Administrator password reset is required")

    async def reset_admin_password(self, new_password: str) -> int:
        """Reset the stable administrator through the explicit local operator path."""
        if not new_password:
            raise ValueError("New administrator password must not be empty")
        if new_password == LEGACY_ADMIN_PASSWORD:
            raise ValueError("New administrator password must not be the legacy password 'admin'")
        return await self.storage.reset_admin_password(pwd_context.hash(new_password))

    async def _enforce_legacy_password_reset(self, user: User) -> None:
        if user.id is None or not user.password_hash:
            return
        try:
            uses_legacy_password = pwd_context.verify(LEGACY_ADMIN_PASSWORD, user.password_hash)
        except Exception:
            uses_legacy_password = False
        if not uses_legacy_password:
            return
        revoked = await self.storage.mark_admin_password_reset_required(user.id)
        logger.warning(
            "Stable administrator uses the known legacy password; reset required and %s sessions revoked",
            revoked,
        )

    async def ensure_admin_user(self) -> None:
        """Bootstrap or validate the one stable persisted administrator identity."""
        try:
            admin_user_id = await self.storage.get_admin_user_id()
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc

        if admin_user_id is not None:
            user = await self.storage.get_user_by_id(admin_user_id)
            if user is None:
                raise RuntimeError(
                    "Configured system.admin_user_id does not reference an existing user"
                )
            if await self.storage.get_setting(BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY) is None:
                await self.storage.persist_admin_identity(admin_user_id)
            await self._enforce_legacy_password_reset(user)
            return

        legacy_admin = await self.storage.get_user_by_username("admin")
        bootstrap_initialized = await self.storage.get_setting(
            BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY
        )
        if legacy_admin is not None:
            if legacy_admin.id is None:
                raise RuntimeError("Legacy admin user is not persisted")
            await self.storage.persist_admin_identity(legacy_admin.id)
            await self._enforce_legacy_password_reset(legacy_admin)
            return

        if bootstrap_initialized is not None:
            raise RuntimeError(
                "Bootstrap admin initialization has already completed, but the admin user is "
                "missing; refusing to create a new credential"
            )

        bootstrap_password = secrets.token_urlsafe(32)
        admin_user = User(
            username="admin",
            email="admin@example.com",
            password_hash=pwd_context.hash(bootstrap_password),
            status="active",
        )
        await self.storage.create_bootstrap_admin(admin_user)
        logger.info(
            "Bootstrap admin user created; one-time bootstrap admin password: %s",
            bootstrap_password,
        )

    def _hash_token(self, token: str) -> str:
        """Calculate token hash"""
        return hashlib.sha256(token.encode()).hexdigest()
