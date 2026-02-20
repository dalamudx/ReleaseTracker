"""OIDC 认证相关数据模型"""

from datetime import datetime
from pydantic import BaseModel


class OIDCProvider(BaseModel):
    """OIDC 提供商配置"""

    id: int | None = None
    name: str  # 显示名称
    slug: str  # URL slug（唯一键）

    # OIDC Discovery
    issuer_url: str | None = None
    discovery_enabled: bool = True

    # 客户端凭证
    client_id: str
    client_secret: str | None = None  # 加密存储，不对外返回

    # 端点（手动配置或自动发现）
    authorization_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    jwks_uri: str | None = None

    # 配置
    scopes: str = "openid email profile"
    enabled: bool = True

    # 元数据
    icon_url: str | None = None
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OAuthState(BaseModel):
    """OAuth state 临时存储（防止 CSRF）"""

    state: str
    provider_slug: str
    code_verifier: str  # PKCE code_verifier
    expires_at: datetime


class OIDCUserInfo(BaseModel):
    """OIDC 用户信息（从 userinfo 端点或 ID Token 获取）"""

    sub: str  # Subject（唯一标识）
    email: str
    email_verified: bool = False
    name: str | None = None
    preferred_username: str | None = None
    picture: str | None = None
    provider_slug: str
