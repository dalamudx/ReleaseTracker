"""HttpOnly browser sessions with a session-bound, signed double-submit CSRF token.

Bearer authentication remains available for API clients. HTTPS cookies use the
__Host- prefix to prevent domain/path cookie injection; plain HTTP is for local
or trusted-network installations only. No JWT is returned to browser JavaScript.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException

from .auth import REFRESH_TOKEN_EXPIRE_DAYS
from .http_security import cors_origins

COOKIE_PREFIX = "releasetracker-"
SECURE_PREFIX = "__Host-" + COOKIE_PREFIX
CSRF_HEADER = "X-CSRF-Token"
BROWSER_HEADER = "X-ReleaseTracker-Browser"
COOKIE_AGE = REFRESH_TOKEN_EXPIRE_DAYS * 86400


def cookie_value(request, field):
    return request.cookies.get(SECURE_PREFIX + field) or request.cookies.get(COOKIE_PREFIX + field)


def _csrf(refresh, nonce):
    signature = hmac.new(refresh.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{signature}"


def validate_csrf(request):
    value = cookie_value(request, "csrf") or ""
    header = request.headers.get(CSRF_HEADER, "")
    refresh = cookie_value(request, "refresh")
    nonce, _, signature = value.partition(".")
    if (
        not refresh
        or not nonce
        or not value.isascii()
        or not header.isascii()
        or len(nonce) > 128
        or len(signature) != 64
        or not hmac.compare_digest(value, header)
        or not hmac.compare_digest(value, _csrf(refresh, nonce))
    ):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


async def require_browser_request(request, storage):
    # Cross-site forms cannot send this header, and untrusted origins cannot
    # obtain CORS preflight permission. Also reject foreign Origin explicitly.
    if request.headers.get(BROWSER_HEADER) != "1":
        raise HTTPException(status_code=403, detail="Browser request header required")
    origin = request.headers.get("origin")
    if origin:
        # An Origin has no path, even when ASGI root_path is a deployment prefix.
        allowed = {f"{request.url.scheme}://{request.url.netloc}", *cors_origins()}
        public = await storage.get_system_base_url()
        if public:
            parsed = urlsplit(public)
            allowed.add(f"{parsed.scheme}://{parsed.netloc}")
        if origin not in allowed:
            raise HTTPException(status_code=403, detail="Browser origin not allowed")


async def set_session_cookies(response, request, storage, tokens):
    public = await storage.get_system_base_url()
    secure = request.url.scheme == "https" or (public and urlsplit(public).scheme == "https")
    secure = bool(secure)
    # Remove old HTTP cookies during an HTTPS migration. No Domain attribute is
    # ever set; __Host- cookies always use Path=/.
    clear_session_cookies(response)
    prefix = SECURE_PREFIX if secure else COOKIE_PREFIX
    csrf = _csrf(tokens.refresh_token, secrets.token_urlsafe(32))
    for field, value in (
        ("access", tokens.access_token),
        ("refresh", tokens.refresh_token),
        ("csrf", csrf),
    ):
        response.set_cookie(
            prefix + field,
            value,
            max_age=COOKIE_AGE,
            httponly=field != "csrf",
            secure=secure,
            samesite="lax",
            path="/",
        )
    response.headers["Cache-Control"] = "no-store"


def clear_session_cookies(response):
    for prefix in (SECURE_PREFIX, COOKIE_PREFIX):
        for field in ("access", "refresh", "csrf"):
            response.delete_cookie(
                prefix + field,
                path="/",
                secure=prefix == SECURE_PREFIX,
                httponly=field != "csrf",
                samesite="lax",
            )
    response.headers["Cache-Control"] = "no-store"
