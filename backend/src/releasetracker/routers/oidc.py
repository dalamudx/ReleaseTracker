"""Public OIDC routes for login entry and callback handling"""

import logging
import secrets
from typing import Annotated
from urllib.parse import urlencode

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


async def _build_public_url(storage: SQLiteStorage, request: Request, path: str) -> str:
    base_url = await storage.get_system_base_url()
    if base_url:
        return f"{base_url}{path}"
    return str(request.base_url).rstrip("/") + path


@router.get("/api/auth/oidc/providers")
async def list_oidc_providers(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
):
    """List enabled OIDC providers for login page buttons"""
    providers = await storage.list_oauth_providers(enabled_only=True)
    # Do not return sensitive configuration fields
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
    """Start the OIDC authorization flow and redirect to the IdP"""
    # Check whether the provider exists
    provider = await storage.get_oauth_provider(provider_slug)
    if not provider or not provider.enabled:
        raise HTTPException(status_code=404, detail="OIDC 提供商不存在或已禁用")

    # Generate state for CSRF protection and a PKCE pair
    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = generate_pkce_pair()

    # Store state and PKCE verifier in the database with a 10-minute TTL
    await storage.save_oauth_state(state, provider_slug, code_verifier)

    callback_path = request.app.url_path_for("oidc_callback", provider_slug=provider_slug)
    redirect_uri = await _build_public_url(storage, request, callback_path)
    logger.info(f"OIDC 授权 redirect_uri: {redirect_uri}")

    # Generate the authorization URL
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
    """Handle the OIDC callback after browser redirect"""
    # 1. Clean up expired state records
    await storage.cleanup_expired_oauth_states()

    # 2. Validate and consume state atomically to prevent replay attacks
    oauth_state = await storage.get_and_delete_oauth_state(state)
    if not oauth_state:
        logger.warning(f"无效或过期的 OIDC state: {state[:8]}...")
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    if oauth_state.provider_slug != provider_slug:
        logger.warning(f"OIDC state 提供商不匹配: {oauth_state.provider_slug} != {provider_slug}")
        raise HTTPException(status_code=400, detail="Provider mismatch")

    # Check whether state is expired
    from datetime import datetime

    if oauth_state.expires_at < datetime.now():
        raise HTTPException(status_code=400, detail="OAuth state expired")

    # 3. Build the callback URL consistently with the authorize endpoint
    callback_path = request.app.url_path_for("oidc_callback", provider_slug=provider_slug)
    redirect_uri = await _build_public_url(storage, request, callback_path)

    # 4. Exchange the code for tokens, fetch user info, and create or link the user
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

    # 5. Redirect to the frontend with the token in the URL hash so it does not appear in server logs
    frontend_url = await _build_public_url(storage, request, "")
    callback_payload = urlencode(
        {
            "token": token_pair.access_token,
            "access_token": token_pair.access_token,
            "refresh_token": token_pair.refresh_token,
            "token_type": token_pair.token_type,
            "expires_in": str(token_pair.expires_in),
        }
    )
    return RedirectResponse(url=f"{frontend_url}/#{callback_payload}")
