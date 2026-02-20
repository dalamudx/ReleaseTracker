"""OIDC 公开路由（登录入口 + 回调处理）"""

import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse

from ..services.oidc_service import OIDCService, generate_pkce_pair
from ..services.auth import AuthService
from ..storage.sqlite import SQLiteStorage
from ..dependencies import get_storage, get_auth_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["OIDC Auth"])


def get_oidc_service(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> OIDCService:
    return OIDCService(storage, auth_service)


@router.get("/api/auth/oidc/providers")
async def list_oidc_providers(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
):
    """列出已启用的 OIDC 提供商（公开接口，供登录页展示按钮）"""
    providers = await storage.list_oauth_providers(enabled_only=True)
    # 不返回敏感配置字段
    return [
        {
            "slug": p.slug,
            "name": p.name,
            "icon_url": p.icon_url,
            "description": p.description,
        }
        for p in providers
    ]


@router.get("/api/auth/oidc/{provider_slug}/authorize")
async def oidc_authorize(
    provider_slug: str,
    request: Request,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    oidc_service: Annotated[OIDCService, Depends(get_oidc_service)],
):
    """发起 OIDC 授权流程（重定向到 IdP）"""
    # 检查提供商是否存在
    provider = await storage.get_oauth_provider(provider_slug)
    if not provider or not provider.enabled:
        raise HTTPException(status_code=404, detail="OIDC 提供商不存在或已禁用")

    # 生成 state（防 CSRF）和 PKCE pair
    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = generate_pkce_pair()

    # 将 state + PKCE verifier 存入数据库（10 分钟 TTL）
    await storage.save_oauth_state(state, provider_slug, code_verifier)

    # 构建回调 URL（指向后端，不带 /api 前缀以便浏览器直接访问）
    redirect_uri = str(request.url_for("oidc_callback", provider_slug=provider_slug))
    logger.info(f"OIDC 授权 redirect_uri: {redirect_uri}")

    # 生成授权 URL
    auth_url = await oidc_service.get_authorization_url(
        provider_slug, redirect_uri, state, code_challenge
    )

    return RedirectResponse(url=auth_url)


@router.get("/auth/oidc/{provider_slug}/callback", name="oidc_callback")
async def oidc_callback(
    provider_slug: str,
    code: str,
    state: str,
    request: Request,
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    oidc_service: Annotated[OIDCService, Depends(get_oidc_service)],
):
    """处理 OIDC 回调（浏览器重定向回来）"""
    # 1. 清理过期 state
    await storage.cleanup_expired_oauth_states()

    # 2. 验证并消费 state（原子性读+删，防止重放攻击）
    oauth_state = await storage.get_and_delete_oauth_state(state)
    if not oauth_state:
        logger.warning(f"无效或过期的 OIDC state: {state[:8]}...")
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    if oauth_state.provider_slug != provider_slug:
        logger.warning(f"OIDC state 提供商不匹配: {oauth_state.provider_slug} != {provider_slug}")
        raise HTTPException(status_code=400, detail="Provider mismatch")

    # 检查 state 是否过期
    from datetime import datetime

    if oauth_state.expires_at < datetime.now():
        raise HTTPException(status_code=400, detail="OAuth state expired")

    # 3. 构建回调 URL（与 authorize 端点保持一致）
    redirect_uri = str(request.url_for("oidc_callback", provider_slug=provider_slug))

    # 4. 用 code 换取 token，获取用户信息，创建/关联用户
    try:
        user, token_pair = await oidc_service.handle_callback(
            provider_slug=provider_slug,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=oauth_state.code_verifier,
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )
    except ValueError as e:
        logger.error(f"OIDC 回调处理失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"OIDC 回调意外错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="OIDC 认证失败")

    # 5. 重定向到前端，token 放在 URL hash（不会出现在服务器日志中）
    from ..config import Settings

    frontend_url = Settings().FRONTEND_URL
    return RedirectResponse(url=f"{frontend_url}/#token={token_pair.access_token}")
