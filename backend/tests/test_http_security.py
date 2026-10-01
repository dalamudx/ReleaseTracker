from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from releasetracker.services.http_security import (
    LoginRateLimiter,
    LoginRateLimitMiddleware,
    configure_http_security,
    cors_origins,
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
    app = FastAPI()
    app.add_middleware(LoginRateLimitMiddleware, limiter=LoginRateLimiter(per_peer=1))
    from releasetracker.services.http_security import SecurityHeadersMiddleware

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


def test_default_same_origin_and_explicit_cors(monkeypatch):
    monkeypatch.delenv("RELEASETRACKER_CORS_ORIGINS", raising=False)
    app = FastAPI()
    configure_http_security(app)
    assert (
        "access-control-allow-origin"
        not in TestClient(app).get("/", headers={"Origin": "https://evil.test"}).headers
    )
    monkeypatch.setenv(
        "RELEASETRACKER_CORS_ORIGINS", "https://ui.example.test,http://localhost:5173"
    )
    app = FastAPI()
    configure_http_security(app)
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
        "access-control-allow-origin"
        not in client.get("/", headers={"Origin": "https://evil.test"}).headers
    )


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
def test_invalid_cors_fails_closed(monkeypatch, origin):
    monkeypatch.setenv("RELEASETRACKER_CORS_ORIGINS", origin)
    with pytest.raises(ValueError):
        cors_origins()
