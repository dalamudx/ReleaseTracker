from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest

from releasetracker.services.http_security import (
    LoginRateLimiter,
    LoginRateLimitMiddleware,
    TrustedProxyMiddleware,
    configure_http_security,
    cors_origins,
)
from releasetracker.services.inbound_policy import (
    CORS_ORIGINS_SETTING,
    InboundPolicy,
    build_policy,
)


def test_login_budget_shared_between_json_and_oauth_and_expires():
    clock = [0]
    limiter = LoginRateLimiter(clock=lambda: clock[0], per_peer=2)
    app = FastAPI()
    app.add_middleware(LoginRateLimitMiddleware, limiter=limiter)
    client = TestClient(app)
    assert client.post("/api/auth/login").status_code == 404
    assert client.post("/api/auth/token").status_code == 404
    response = client.post("/api/auth/login", headers={"X-Forwarded-For": "new-peer"})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert client.get("/api/auth/me").status_code == 404
    clock[0] = 60
    assert client.post("/api/auth/login").status_code == 404


def test_successful_logins_do_not_consume_failure_budget():
    app = FastAPI()
    app.add_middleware(LoginRateLimitMiddleware, limiter=LoginRateLimiter(per_peer=2))

    @app.post("/api/auth/login")
    def login():
        return {"token": "test"}

    client = TestClient(app)
    for _ in range(5):
        assert client.post("/api/auth/login").status_code == 200


def test_security_headers_even_on_rate_limit():
    from releasetracker.services.http_security import SecurityHeadersMiddleware

    app = FastAPI()
    app.add_middleware(LoginRateLimitMiddleware, limiter=LoginRateLimiter(per_peer=1))
    app.add_middleware(SecurityHeadersMiddleware)
    client = TestClient(app)
    assert client.post("/api/auth/login").status_code == 404
    response = client.post("/api/auth/login")
    assert response.status_code == 429
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_distributed_attempts_have_bounded_memory():
    limiter = LoginRateLimiter(total=5)
    for peer in range(5):
        assert limiter.retry_after(str(peer)) == 0
    for peer in range(5, 100):
        assert limiter.retry_after(str(peer)) > 0
    assert len(limiter.attempts) == 5


def _secured_app(origins=(), trusted=()):
    app = FastAPI()
    app.state.inbound_policy = InboundPolicy(cors_origins=origins, trusted_proxies=trusted)

    @app.get("/")
    def root():
        return {}

    @app.get("/peer")
    def peer(request: Request):
        return {"scheme": request.url.scheme, "host": request.client.host}

    configure_http_security(app)
    return app


def test_unconfigured_cors_denies_foreign_origins():
    client = TestClient(_secured_app())
    assert (
        "access-control-allow-origin"
        not in client.get("/", headers={"Origin": "https://evil.test"}).headers
    )
    preflight = client.options(
        "/api/auth/login",
        headers={"Origin": "https://ui.example.test", "Access-Control-Request-Method": "POST"},
    )
    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers


def test_saved_cors_origins_apply_without_restart():
    app = _secured_app(origins=("https://ui.example.test", "http://localhost:5173"))
    client = TestClient(app)
    response = client.options(
        "/api/auth/login",
        headers={
            "Origin": "https://ui.example.test",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://ui.example.test"
    assert (
        client.get("/", headers={"Origin": "https://ui.example.test"}).headers[
            "access-control-allow-origin"
        ]
        == "https://ui.example.test"
    )
    assert (
        "access-control-allow-origin"
        not in client.get("/", headers={"Origin": "https://evil.test"}).headers
    )

    app.state.inbound_policy = InboundPolicy(cors_origins=("https://other.example.test",))
    assert (
        "access-control-allow-origin"
        not in client.get("/", headers={"Origin": "https://ui.example.test"}).headers
    )
    assert (
        client.get("/", headers={"Origin": "https://other.example.test"}).headers[
            "access-control-allow-origin"
        ]
        == "https://other.example.test"
    )
    assert cors_origins(app) == ("https://other.example.test",)


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "https://*.test",
        "https://u:p@host.test",
        "https://host.test/path",
        "null",
        "https://host.test:abc",
    ],
)
def test_invalid_cors_value_is_not_trusted(origin):
    assert build_policy({CORS_ORIGINS_SETTING: origin}).cors_origins == ()


def test_trusted_proxy_resolves_peer_and_scheme():
    app = _secured_app(trusted=("10.0.0.5",))
    forwarded = {"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"}
    trusted = TestClient(app, client=("10.0.0.5", 50000))
    assert trusted.get("/peer", headers=forwarded).json() == {
        "scheme": "https",
        "host": "203.0.113.9",
    }
    untrusted = TestClient(app, client=("192.0.2.7", 50000))
    assert untrusted.get("/peer", headers=forwarded).json() == {
        "scheme": "http",
        "host": "192.0.2.7",
    }


def test_proxy_chain_uses_first_untrusted_hop():
    app = _secured_app(trusted=("10.0.0.0/24",))
    client = TestClient(app, client=("10.0.0.5", 50000))
    response = client.get(
        "/peer",
        headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.6, 10.0.0.7"},
    )
    assert response.json()["host"] == "203.0.113.9"


def test_login_throttling_uses_the_resolved_client():
    app = FastAPI()
    app.state.inbound_policy = InboundPolicy(trusted_proxies=("10.0.0.5",))

    @app.post("/api/auth/login")
    def login():
        raise HTTPException(status_code=401, detail="Invalid credentials")

    app.add_middleware(LoginRateLimitMiddleware, limiter=LoginRateLimiter(per_peer=1))
    app.add_middleware(TrustedProxyMiddleware)
    client = TestClient(app, client=("10.0.0.5", 50000))
    resolved = {"X-Forwarded-For": "203.0.113.9"}
    assert client.post("/api/auth/login", headers=resolved).status_code == 401
    assert client.post("/api/auth/login", headers=resolved).status_code == 429
    # The budget follows the resolved client, not the shared ingress address.
    assert (
        client.post("/api/auth/login", headers={"X-Forwarded-For": "198.51.100.4"}).status_code
        == 401
    )
