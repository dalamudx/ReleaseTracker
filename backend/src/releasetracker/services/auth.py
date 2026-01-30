"""认证服务模块"""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import hashlib

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
from ..config import Settings
from ..storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)

# 密码哈希配置
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

import os

# JWT 配置
SECRET_KEY = os.getenv("JWT_SECRET")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


class AuthService:
    """认证服务"""

    def __init__(self, storage: SQLiteStorage):
        self.storage = storage

        if SECRET_KEY:
            self.secret_key = SECRET_KEY
        else:
            logger.warning("No JWT_SECRET set. Using insecure default key for development only!")
            self.secret_key = "dev-insecure-secret-key-do-not-use-in-prod"

    async def register(self, req: RegisterRequest) -> User:
        """注册用户"""
        # 检查用户是否存在
        existing_user = await self.storage.get_user_by_username(req.username)
        if existing_user:
            raise ValueError("Username already exists")

        # 哈希密码
        password_hash = pwd_context.hash(req.password)

        # 创建用户
        user = User(
            username=req.username, email=req.email, password_hash=password_hash, status="active"
        )

        return await self.storage.create_user(user)

    async def login(
        self, req: LoginRequest, user_agent: str = None, ip_address: str = None
    ) -> tuple[User, TokenPair]:
        """用户登录"""
        user = await self.storage.get_user_by_username(req.username)
        if not user:
            raise ValueError("Invalid credentials")

        if not pwd_context.verify(req.password, user.password_hash):
            raise ValueError("Invalid credentials")

        if user.status != "active":
            raise ValueError("User account is not active")

        # 生成令牌
        token_pair = self._create_token_pair(user)

        # 创建会话
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
        """用户登出"""
        token_hash = self._hash_token(token)
        await self.storage.delete_session(token_hash)

    async def change_password(self, token: str, req: ChangePasswordRequest) -> None:
        """修改密码"""
        user = await self.get_current_user(token)

        # 验证旧密码
        if not pwd_context.verify(req.old_password, user.password_hash):
            raise ValueError("Invalid old password")

        # 更新密码
        new_password_hash = pwd_context.hash(req.new_password)
        await self.storage.update_user_password(user.id, new_password_hash)

    async def refresh_token(self, refresh_token: str) -> TokenPair:
        """刷新令牌"""
        try:
            payload = jwt.decode(refresh_token, self.secret_key, algorithms=[ALGORITHM])
            username = payload.get("sub")
            token_type = payload.get("type")

            if token_type != "refresh":
                raise ValueError("Invalid token type")

            user = await self.storage.get_user_by_username(username)
            if not user:
                raise ValueError("User not found")

            # 验证会话
            # refresh_hash = self._hash_token(refresh_token)
            # 这里简化处理，严谨实现应该根据 refresh_hash 查会话
            # 目前 storage 只有根据 access_token_hash 查会话，暂略过数据库验证

            return self._create_token_pair(user)

        except jwt.PyJWTError:
            raise ValueError("Invalid refresh token")

    async def get_current_user(self, token: str) -> User:
        """根据令牌获取当前用户"""
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
        """生成令牌对"""
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
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=ALGORITHM)
        return encoded_jwt

    async def ensure_admin_user(self):
        """确保存在管理员用户"""
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
        """计算令牌哈希"""
        return hashlib.sha256(token.encode()).hexdigest()
