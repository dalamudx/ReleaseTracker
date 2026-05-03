"""Authentication service module"""

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional
import hashlib
import uuid

import jwt
from passlib.context import CryptContext

from ..models import (
    User,
    Session,
    LoginRequest,
    RegisterRequest,
    TokenPair,
    ChangePasswordRequest,
)
from ..storage.sqlite import SQLiteStorage

if TYPE_CHECKING:
    from .system_keys import SystemKeyManager

logger = logging.getLogger(__name__)

# Password hashing configuration
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT Configuration
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


class AuthService:
    """Authentication service"""

    def __init__(self, storage: SQLiteStorage, system_key_manager: "SystemKeyManager"):
        self.storage = storage
        self.system_key_manager = system_key_manager

    @property
    def secret_key(self) -> str:
        return self.system_key_manager.jwt_secret

    async def register(self, req: RegisterRequest) -> User:
        """Register a user"""
        # Check whether the user exists
        existing_user = await self.storage.get_user_by_username(req.username)
        if existing_user:
            raise ValueError("Username already exists")

        # Hash the password
        password_hash = pwd_context.hash(req.password)

        # Create the user
        user = User(
            username=req.username, email=req.email, password_hash=password_hash, status="active"
        )

        return await self.storage.create_user(user)

    async def login(
        self, req: LoginRequest, user_agent: Optional[str] = None, ip_address: Optional[str] = None
    ) -> tuple[User, TokenPair]:
        """User login"""
        user = await self.storage.get_user_by_username(req.username)
        if not user:
            raise ValueError("Invalid credentials")

        if not pwd_context.verify(req.password, user.password_hash):
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
                jwt.decode(token_pair.access_token, self.secret_key, algorithms=[ALGORITHM])["exp"]
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
        if not pwd_context.verify(req.old_password, user.password_hash):
            raise ValueError("Invalid old password")

        # Update password
        if user.id is None:
            raise ValueError("User not found")

        new_password_hash = pwd_context.hash(req.new_password)
        await self.storage.update_user_password(user.id, new_password_hash)

    async def refresh_token(self, refresh_token: str) -> TokenPair:
        """Refresh token"""
        try:
            payload = jwt.decode(refresh_token, self.secret_key, algorithms=[ALGORITHM])
            username = payload.get("sub")
            token_type = payload.get("type")

            if token_type != "refresh" or not username:
                raise ValueError("Invalid token type")

            refresh_hash = self._hash_token(refresh_token)
            session = await self.storage.get_session_by_refresh_token(refresh_hash)
            if not session:
                raise ValueError("Invalid refresh token")

            user = await self.storage.get_user_by_id(session.user_id)
            if not user or user.username != username or user.status != "active":
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
                    jwt.decode(token_pair.access_token, self.secret_key, algorithms=[ALGORITHM])[
                        "exp"
                    ]
                ),
            )
            if not session_updated:
                raise ValueError("Invalid refresh token")
            return token_pair

        except jwt.PyJWTError:
            raise ValueError("Invalid refresh token")

    async def get_current_user(self, token: str) -> User:
        """Get the current user from a token"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[ALGORITHM])
            username = payload.get("sub")
            if username is None:
                raise ValueError("Invalid token")

            token_hash = self._hash_token(token)
            session = await self.storage.get_session(token_hash)
            if not session:
                raise ValueError("Session expired or invalid")

            if session.expires_at < datetime.now():
                await self.storage.delete_session(token_hash)
                raise ValueError("Session expired")

            user = await self.storage.get_user_by_id(session.user_id)
            if not user:
                raise ValueError("User not found")

            return user

        except jwt.PyJWTError:
            raise ValueError("Invalid token")

    def _create_token_pair(self, user: User) -> TokenPair:
        """Generate a token pair"""
        claims = {
            "sub": user.username,
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
        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=ALGORITHM)
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
        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=ALGORITHM)
        return encoded_jwt

    async def ensure_admin_user(self):
        """Ensure an admin user exists"""
        user = await self.storage.get_user_by_username("admin")
        if not user:
            logger.info("Creating default admin user")
            password_hash = pwd_context.hash("admin")
            admin_user = User(
                username="admin",
                email="admin@example.com",
                password_hash=password_hash,
                status="active",
            )
            await self.storage.create_user(admin_user)

    def _hash_token(self, token: str) -> str:
        """Calculate token hash"""
        return hashlib.sha256(token.encode()).hexdigest()
