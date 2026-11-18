"""Bounded single-instance HTTP admission controls; no credentials are retained."""

import math
import time
from collections import deque
from typing import Any

from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .inbound_policy import inbound_policy, load_policy


class SecurityHeadersMiddleware:
    """Harden every HTTP response, including static pages and error responses."""

    HEADERS = (
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        # A full script-src policy requires moving the theme bootstrap out of
        # index.html; these directives do not block that existing inline script.
        (b"content-security-policy", b"frame-ancestors 'none'; base-uri 'self'; object-src 'none'"),
    )

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend((name, value) for name, value in self.HEADERS if name not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, secure_send)


class LoginRateLimiter:
    """Limit work before parsing a body or performing expensive password hashing.

    A global budget bounds both distributed attempts and per-peer memory. The
    ASGI peer is authoritative; arbitrary forwarded headers are never read.
    """

    def __init__(self, *, clock=time.monotonic, per_peer=10, total=100, window=60):
        self.clock = clock
        self.per_peer = per_peer
        self.total = total
        self.window = window
        self.attempts = deque()

    def retry_after(self, peer):
        now = self.clock()
        while self.attempts and self.attempts[0][0] <= now - self.window:
            self.attempts.popleft()
        peer_times = [when for when, host in self.attempts if host == peer]
        deadlines = []
        if len(self.attempts) >= self.total:
            deadlines.append(self.attempts[0][0] + self.window)
        if len(peer_times) >= self.per_peer:
            deadlines.append(peer_times[0] + self.window)
        if deadlines:
            return max(1, math.ceil(max(deadlines) - now))
        self.attempts.append((now, peer))
        return 0

    def record_success(self, peer):
        # Valid credentials are not brute-force attempts. Admission is still
        # counted until the response is known, including concurrent requests.
        for item in reversed(self.attempts):
            if item[1] == peer:
                self.attempts.remove(item)
                break


class LoginRateLimitMiddleware:
    def __init__(self, app, limiter=None):
        self.app = app
        self.limiter = limiter or LoginRateLimiter()

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        root = scope.get("root_path", "").rstrip("/")
        if root and path.startswith(root + "/"):
            path = path[len(root) :]
        if (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and path.rstrip("/")
            in {
                "/api/auth/login",
                "/api/auth/token",
                "/api/auth/browser/login",
            }
        ):
            peer = (scope.get("client") or ("unknown",))[0]
            retry = self.limiter.retry_after(peer)
            if retry:
                response = JSONResponse(
                    {"detail": "Too many login attempts; try again later"},
                    status_code=429,
                    headers={"Retry-After": str(retry), "Cache-Control": "no-store"},
                )
                await response(scope, receive, send)
                return

            async def on_login_response(message):
                if message["type"] == "http.response.start" and message["status"] == 200:
                    self.limiter.record_success(peer)
                await send(message)

            return await self.app(scope, receive, on_login_response)
        await self.app(scope, receive, send)


def cors_origins(app: Any) -> tuple[str, ...]:
    """Origins from the saved policy, shared with the browser session checks."""
    return inbound_policy(app).cors_origins


class TrustedProxyMiddleware:
    """Apply the saved trusted-proxy policy to the peer address and scheme.

    The ASGI peer stays authoritative for untrusted callers; forwarded headers are
    interpreted by the server library only for peers the saved policy lists. The
    resolved values land before rate limiting, CORS and cookie decisions because
    this middleware runs outside them.
    """

    def __init__(self, app):
        self.app = app
        self._trusted: tuple[str, ...] | None = None
        self._delegate = ProxyHeadersMiddleware(app, trusted_hosts=[])

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        application = scope["app"]
        policy = inbound_policy(application)
        storage = getattr(application.state, "storage", None)
        # Online restore owns an exclusive process lock. Other workers share only SQLite.
        if (
            storage is not None
            and getattr(application.state, "online_restore", None) is None
            and any(
                name in {b"origin", b"x-forwarded-for", b"x-forwarded-proto"}
                for name, _ in scope["headers"]
            )
        ):
            try:
                policy = await load_policy(storage)
            finally:
                # Preflight and rate-limited requests can bypass the route cleanup.
                await storage.close_current_task_connection()
        scope["inbound_policy"] = policy
        trusted = policy.trusted_proxies
        if trusted != self._trusted:
            self._trusted = trusted
            self._delegate = ProxyHeadersMiddleware(self.app, trusted_hosts=list(trusted))
        return await self._delegate(scope, receive, send)


class PolicyCORSMiddleware:
    def __init__(self, app, **options):
        self.app = app
        self.options = options
        self.origins = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        origins = scope["inbound_policy"].cors_origins
        if origins != self.origins:
            self.delegate = CORSMiddleware(self.app, allow_origins=origins, **self.options)
            self.origins = origins
        return await self.delegate(scope, receive, send)


def configure_http_security(app):
    app.add_middleware(LoginRateLimitMiddleware)
    app.add_middleware(
        PolicyCORSMiddleware,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-CSRF-Token",
            "X-ReleaseTracker-Browser",
        ],
        expose_headers=["X-Total-Count"],
    )
    app.add_middleware(TrustedProxyMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
