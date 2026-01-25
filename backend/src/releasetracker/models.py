"""核心数据模型"""

from datetime import datetime
from typing import Literal, Any

from pydantic import BaseModel, Field


class Release(BaseModel):
    """版本发布模型"""

    id: int | None = None
    tracker_name: str
    name: str
    tag_name: str
    version: str
    published_at: datetime
    url: str
    prerelease: bool = False
    body: str | None = None  # Release Notes 内容
    channel_name: str | None = None  # 渠道名称（stable/prerelease/beta/canary）
    commit_sha: str | None = None  # Git commit SHA
    republish_count: int = 0  # 重新发布次数
    created_at: datetime = Field(default_factory=datetime.now)


class TrackerStatus(BaseModel):
    """追踪器状态"""

    name: str
    type: Literal["github", "gitlab", "helm"]
    enabled: bool = True
    last_check: datetime | None = None
    last_version: str | None = None
    error: str | None = None
    channel_count: int = 0  # 渠道数量


class ReleaseStats(BaseModel):
    """版本统计"""

    total_trackers: int
    total_releases: int
    recent_releases: int  # 最近24小时
    latest_update: datetime | None = None
    daily_stats: list[dict[str, Any]] = []  # 每日发布统计 [{"date": "...", "channels": {...}}]
    channel_stats: dict[str, int] = {}  # 各渠道总发布数 {"正式版": 10, "测试版": 5, ...}
    release_type_stats: dict[str, int] = {}  # 按发布类型统计 {"正式版": 10, "预发布版": 5}


class Credential(BaseModel):
    """API 凭证模型"""

    id: int | None = None
    name: str  # 凭证名称，如 "公司 GitHub Token"
    type: Literal["github", "gitlab", "helm"]  # 凭证类型
    token: str  # API Token
    description: str | None = None  # 可选描述
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ReleaseHistory(BaseModel):
    """版本历史记录模型"""
    
    id: int | None = None
    release_id: int
    commit_sha: str
    published_at: datetime
    body: str | None = None
    channel_name: str | None = None
    recorded_at: datetime = Field(default_factory=datetime.now)


# ==================== Auth Models ====================

class User(BaseModel):
    """用户模型"""
    
    id: int | None = None
    username: str
    email: str
    password_hash: str
    role: str = "user"  # user, admin
    status: str = "active"  # active, inactive
    created_at: datetime = Field(default_factory=datetime.now)
    last_login_at: datetime | None = None


class Session(BaseModel):
    """会话模型"""
    
    id: int | None = None
    user_id: int
    token_hash: str
    refresh_token_hash: str | None = None
    user_agent: str | None = None
    ip_address: str | None = None
    expires_at: datetime
    created_at: datetime = Field(default_factory=datetime.now)


class TokenPair(BaseModel):
    """令牌对"""
    
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int


class LoginRequest(BaseModel):
    """登录请求"""
    
    username: str
    password: str


class RegisterRequest(BaseModel):
    """注册请求"""
    
    username: str
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    """修改密码请求"""
    
    old_password: str
    new_password: str

