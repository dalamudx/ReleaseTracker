"""Browser-only authentication. API bearer endpoints retain their existing contract."""

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..dependencies import get_auth_service
from ..models import LoginRequest
from ..services.auth import AuthService
from ..services.browser_sessions import (
    clear_session_cookies,
    cookie_value,
    require_browser_request,
    set_session_cookies,
    validate_csrf,
)
from .auth import RefreshTokenRequest, _request_login_audit_context

router = APIRouter(prefix="/api/auth/browser", tags=["auth"])
Auth = Annotated[AuthService, Depends(get_auth_service)]


async def _session_response(request, auth, tokens, user=None):
    user = user or await auth.get_current_user(tokens.access_token)
    response = JSONResponse({"user": user.model_dump(mode="json")})
    await set_session_cookies(response, request, auth.storage, tokens)
    return response


@router.post("/login")
async def login(req: LoginRequest, request: Request, auth: Auth):
    await require_browser_request(request, auth.storage)
    try:
        agent, address = _request_login_audit_context(request)
        user, tokens = await auth.login(req, user_agent=agent, ip_address=address)
        return await _session_response(request, auth, tokens, user)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/refresh")
async def refresh(request: Request, auth: Auth):
    await require_browser_request(request, auth.storage)
    validate_csrf(request)
    try:
        tokens = await auth.refresh_token(cookie_value(request, "refresh"))
        return await _session_response(request, auth, tokens)
    except ValueError as exc:
        # Do not delete cookies on failure: another tab may have just rotated
        # the session successfully. JWT/session verification still fails closed.
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/migrate")
async def migrate(req: RefreshTokenRequest, request: Request, auth: Auth):
    """Rotate an explicitly supplied legacy credential into HttpOnly cookies."""
    await require_browser_request(request, auth.storage)
    try:
        tokens = await auth.refresh_token(req.refresh_token)
        return await _session_response(request, auth, tokens)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/logout")
async def logout(request: Request, auth: Auth):
    await require_browser_request(request, auth.storage)
    validate_csrf(request)
    token = cookie_value(request, "access")
    if token:
        await auth.logout(token)
    response = JSONResponse({"message": "Logged out successfully"})
    clear_session_cookies(response)
    return response
