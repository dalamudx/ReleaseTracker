"""OIDC 认证服务"""

import logging
import secrets
import hashlib
import base64
import json
import httpx
from datetime import datetime

from ..storage.sqlite import SQLiteStorage
from ..services.auth import AuthService
from ..oidc_models import OIDCProvider, OIDCUserInfo
from ..models import User, Session, TokenPair

logger = logging.getLogger(__name__)


def generate_pkce_pair() -> tuple[str, str]:
    """生成 PKCE code_verifier 和 code_challenge"""
    # code_verifier: 43-128 个字符的随机 URL-safe 字符串
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    # code_challenge: SHA256(code_verifier) 后 Base64URL 编码
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return code_verifier, code_challenge


class OIDCService:
    """OIDC 认证服务"""

    def __init__(self, storage: SQLiteStorage, auth_service: AuthService):
        self.storage = storage
        self.auth_service = auth_service

    async def _get_provider_endpoints(self, provider: OIDCProvider) -> OIDCProvider:
        """通过 OIDC Discovery 自动填充端点 URL"""
        if provider.discovery_enabled and provider.issuer_url:
            discovery_url = f"{provider.issuer_url.rstrip('/')}/.well-known/openid-configuration"
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(discovery_url)
                    resp.raise_for_status()
                    config = resp.json()
                provider.authorization_url = config["authorization_endpoint"]
                provider.token_url = config["token_endpoint"]
                provider.userinfo_url = config.get("userinfo_endpoint") or provider.userinfo_url
                provider.jwks_uri = config.get("jwks_uri") or provider.jwks_uri
                logger.info(f"OIDC Discovery 成功：{provider.name}")
            except Exception as e:
                logger.warning(f"OIDC Discovery 失败（{provider.name}）：{e}")
                if not (provider.authorization_url and provider.token_url):
                    raise ValueError("OIDC Discovery 失败且未配置手动端点")
        return provider

    async def get_authorization_url(
        self, provider_slug: str, redirect_uri: str, state: str, code_challenge: str
    ) -> str:
        """生成 OAuth2 授权 URL 并存储 state/PKCE"""
        provider = await self.storage.get_oauth_provider(provider_slug)
        if not provider or not provider.enabled:
            raise ValueError(f"提供商 {provider_slug} 不存在或已禁用")

        provider = await self._get_provider_endpoints(provider)

        # 获取 PKCE verifier（从调用方传入 code_verifier）
        # 这里传入的 code_challenge 已经由路由层生成并存入数据库
        auth_url = (
            f"{provider.authorization_url}"
            f"?response_type=code"
            f"&client_id={provider.client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&scope={provider.scopes}"
            f"&state={state}"
            f"&code_challenge={code_challenge}"
            f"&code_challenge_method=S256"
        )
        return auth_url

    async def handle_callback(
        self,
        provider_slug: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[User, TokenPair]:
        """处理 OIDC 回调：换取 token → 获取用户信息 → 创建/关联用户"""
        provider = await self.storage.get_oauth_provider(provider_slug)
        if not provider:
            raise ValueError(f"提供商 {provider_slug} 不存在")

        provider = await self._get_provider_endpoints(provider)

        # 1. 用 code 换取 access_token
        # 大多数标准 OIDC IdP (如 Authentik, Keycloak) 默认要求使用 Basic Auth (client_secret_basic)
        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": provider.client_id,
            "code_verifier": code_verifier,
        }
        async with httpx.AsyncClient(timeout=15) as http:
            try:
                auth = (
                    (provider.client_id, provider.client_secret) if provider.client_secret else None
                )
                token_resp = await http.post(
                    str(provider.token_url),
                    data=token_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    auth=auth,
                )
                token_resp.raise_for_status()
                token = token_resp.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Token 换取失败：{e.response.status_code} {e.response.text}")
                raise ValueError(f"Token 换取失败：{e.response.status_code}")

        # 2. 获取用户信息
        if token.get("id_token"):
            userinfo = self._parse_id_token(token["id_token"], provider_slug)
        else:
            async with httpx.AsyncClient(timeout=10) as http:
                ui_resp = await http.get(
                    str(provider.userinfo_url),
                    headers={"Authorization": f"Bearer {token['access_token']}"},
                )
                ui_resp.raise_for_status()
                userinfo = self._normalize_userinfo(ui_resp.json(), provider_slug)

        # 3. 创建或关联用户
        user = await self._get_or_create_user(userinfo)

        # 4. 生成 JWT
        token_pair = self.auth_service._create_token_pair(user)

        # 5. 创建会话
        import jwt as pyjwt

        session = Session(
            user_id=user.id,
            token_hash=self.auth_service._hash_token(token_pair.access_token),
            refresh_token_hash=self.auth_service._hash_token(token_pair.refresh_token),
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=datetime.fromtimestamp(
                pyjwt.decode(
                    token_pair.access_token,
                    key=self.auth_service.secret_key,
                    algorithms=["HS256"],
                )["exp"]
            ),
        )
        await self.storage.create_session(session)

        return user, token_pair

    def _parse_id_token(self, id_token: str, provider_slug: str) -> OIDCUserInfo:
        """解析 JWT ID Token（不验签 - 仅用于读取声明）"""
        parts = id_token.split(".")
        if len(parts) != 3:
            raise ValueError("ID Token 格式无效")
        payload = parts[1]
        # 补全 Base64 padding
        payload += "=" * (4 - len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return OIDCUserInfo(
            sub=claims["sub"],
            email=claims.get("email", f"{claims['sub']}@{provider_slug}.local"),
            email_verified=claims.get("email_verified", False),
            name=claims.get("name"),
            preferred_username=claims.get("preferred_username"),
            picture=claims.get("picture"),
            provider_slug=provider_slug,
        )

    def _normalize_userinfo(self, data: dict, provider_slug: str) -> OIDCUserInfo:
        """规范化 userinfo 端点返回的数据"""
        return OIDCUserInfo(
            sub=data["sub"],
            email=data.get("email", f"{data['sub']}@{provider_slug}.local"),
            email_verified=data.get("email_verified", False),
            name=data.get("name"),
            preferred_username=data.get("preferred_username"),
            picture=data.get("picture"),
            provider_slug=provider_slug,
        )

    async def _get_or_create_user(self, userinfo: OIDCUserInfo) -> User:
        """获取或创建 OIDC 用户（三步匹配逻辑）"""
        # 1. 按 OIDC sub 匹配
        existing = await self.storage.get_user_by_oauth(userinfo.provider_slug, userinfo.sub)
        if existing:
            # 同步头像/邮箱
            if userinfo.email_verified or userinfo.picture:
                await self.storage.update_user_oidc_info(
                    existing.id,
                    email=userinfo.email if userinfo.email_verified else None,
                    avatar_url=userinfo.picture,
                )
            return existing

        # 2. 按用户名匹配（自动关联）
        username = userinfo.preferred_username or userinfo.email.split("@")[0]
        by_username = await self.storage.get_user_by_username(username)
        if by_username:
            await self.storage.link_oauth_to_user(
                by_username.id, userinfo.provider_slug, userinfo.sub, userinfo.picture
            )
            return by_username

        # 3. 创建新用户
        user = User(
            username=username,
            email=userinfo.email,
            password_hash=None,  # OIDC 用户无密码
            oauth_provider=userinfo.provider_slug,
            oauth_sub=userinfo.sub,
            avatar_url=userinfo.picture,
            status="active",
        )
        return await self.storage.create_user(user)
