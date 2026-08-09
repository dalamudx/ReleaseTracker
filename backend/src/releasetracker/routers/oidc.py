"""Public OIDC routes for login entry and callback handling"""

import hashlib
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
from ..services.secure_urls import require_canonical_https_base_url

logger = logging.getLogger(__name__)

router = APIRouter(tags=["OIDC Auth"])

OIDC_BROWSER_COOKIE_NAME = "__Host-releasetracker-oidc"
OIDC_BROWSER_COOKIE_MAX_AGE = 600


def get_oidc_service(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> OIDCService:
    return OIDCService(storage, auth_service)


async def _get_public_base_url(storage: SQLiteStorage) -> str:
    base_url = await storage.get_system_base_url()
    return require_canonical_https_base_url(base_url)


async def _build_public_url(storage: SQLiteStorage, path: str) -> str:
    return f"{await _get_public_base_url(storage)}{path}"


def _hash_browser_binding(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _set_browser_binding_cookie(response, token: str) -> None:
    response.set_cookie(
        OIDC_BROWSER_COOKIE_NAME,
        token,
        max_age=OIDC_BROWSER_COOKIE_MAX_AGE,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )


def _clear_browser_binding_cookie(response) -> None:
    response.delete_cookie(
        OIDC_BROWSER_COOKIE_NAME,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )


@router.get("/api/auth/oidc/providers")
async def list_oidc_providers(
    storage: Annotated[SQLiteStorage, Depends(get_storage)],
):
    """List enabled OIDC providers for login page buttons"""
    binding = await storage.get_admin_oidc_binding()
    if binding is None:
        return []
    providers = [
        provider
        for provider in await storage.list_oauth_providers(enabled_only=True)
        if provider.issuer_url == binding[0]
    ]
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
    try:
        callback_path = request.app.url_path_for("oidc_callback", provider_slug=provider_slug)
        redirect_uri = await _build_public_url(storage, callback_path)
        await oidc_service.auth_service.ensure_password_reset_not_required()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Check whether the provider exists
    provider = await storage.get_oauth_provider(provider_slug)
    if not provider or not provider.enabled:
        raise HTTPException(status_code=404, detail="OIDC provider does not exist or is disabled")
    binding = await storage.get_admin_oidc_binding()
    if binding is None or provider.issuer_url != binding[0]:
        raise HTTPException(
            status_code=403, detail="OIDC provider is not bound to the administrator"
        )

    # Generate state, nonce, and a PKCE pair.
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    browser_token = secrets.token_urlsafe(32)
    code_verifier, code_challenge = generate_pkce_pair()
    logger.info("OIDC authorize uses configured callback URL for provider=%s", provider_slug)

    try:
        auth_url = await oidc_service.get_authorization_url(
            provider_slug, redirect_uri, state, code_challenge, nonce
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await storage.save_oauth_state(
        state,
        provider_slug,
        code_verifier,
        nonce,
        "login",
        _hash_browser_binding(browser_token),
    )

    response = RedirectResponse(url=auth_url)
    _set_browser_binding_cookie(response, browser_token)
    return response


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
    # Validate the canonical redirect target before touching one-time state.
    try:
        callback_path = request.app.url_path_for("oidc_callback", provider_slug=provider_slug)
        redirect_uri = await _build_public_url(storage, callback_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await storage.cleanup_expired_oauth_states()
    browser_token = request.cookies.get(OIDC_BROWSER_COOKIE_NAME)
    if not browser_token:
        raise HTTPException(status_code=400, detail="Missing OIDC browser binding cookie")

    oauth_state = await storage.consume_oauth_state(
        state,
        provider_slug,
        _hash_browser_binding(browser_token),
    )
    if not oauth_state:
        logger.warning("Invalid, expired, or browser-mismatched OIDC state")
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    # 4. Exchange the code, validate the ID token, and resolve the stable administrator.
    try:
        user, token_pair = await oidc_service.handle_callback(
            provider_slug=provider_slug,
            code=code,
            redirect_uri=redirect_uri,
            oauth_state=oauth_state,
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )
    except ValueError as e:
        logger.error(f"OIDC callback handling failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"OIDC callback unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="OIDC authentication failed")

    # 5. Redirect to the frontend with the token in the URL hash so it does not appear in server logs
    frontend_url = await _get_public_base_url(storage)
    callback_payload = urlencode(
        {
            "token": token_pair.access_token,
            "access_token": token_pair.access_token,
            "refresh_token": token_pair.refresh_token,
            "token_type": token_pair.token_type,
            "expires_in": str(token_pair.expires_in),
        }
    )
    response = RedirectResponse(url=f"{frontend_url}/#{callback_payload}")
    _clear_browser_binding_cookie(response)
    return response
