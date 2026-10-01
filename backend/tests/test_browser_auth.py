import pytest
from releasetracker.models import LoginRequest
from releasetracker.services.auth import pwd_context
from releasetracker.services.browser_sessions import COOKIE_PREFIX, SECURE_PREFIX
from releasetracker.storage.sqlite import SYSTEM_BASE_URL_SETTING_KEY

HEADER = {"X-ReleaseTracker-Browser": "1"}
LOGIN = "/api/auth/browser/login"


async def admin(auth_service, storage):
    await auth_service.ensure_admin_user()
    user = await storage.get_user_by_username("admin")
    await storage.update_user_password(user.id, pwd_context.hash("browser-password"))


def login(client):
    return client.post(
        LOGIN, json={"username": "admin", "password": "browser-password"}, headers=HEADER
    )


def csrf(client):
    return {**HEADER, "X-CSRF-Token": client.cookies.get(COOKIE_PREFIX + "csrf")}


@pytest.mark.asyncio
async def test_browser_login_is_httponly_and_protects_every_write(client, auth_service, storage):
    await admin(auth_service, storage)
    response = login(client)
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"user"}
    assert "password_hash" not in response.json()["user"]
    cookies = response.headers.get_list("set-cookie")
    active = [v for v in cookies if "Max-Age=604800" in v]
    assert len(active) == 3
    assert all("SameSite=lax" in v and "Path=/" in v for v in active)
    assert all("HttpOnly" in v for v in active if "-csrf=" not in v)
    assert all("HttpOnly" not in v for v in active if "-csrf=" in v)
    assert client.get("/api/auth/me").status_code == 200
    assert "password_hash" not in client.get("/api/auth/me").json()
    assert client.post("/api/tasks/clear").status_code == 403
    assert client.post("/api/tasks/clear", headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert client.post("/api/tasks/clear", headers=csrf(client)).status_code == 200
    # Explicit, invalid Bearer credentials must not fall back to a valid cookie.
    assert (
        client.get("/api/auth/me", headers={"Authorization": "Bearer invalid"}).status_code == 401
    )


@pytest.mark.asyncio
async def test_browser_login_accepts_same_origin_with_asgi_root_path(
    client, auth_service, storage, monkeypatch
):
    await admin(auth_service, storage)
    monkeypatch.setattr(client.app, "root_path", "/releasetracker")
    response = client.post(
        LOGIN,
        json={"username": "admin", "password": "browser-password"},
        headers={**HEADER, "Origin": "http://testserver"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_browser_refresh_rotates_csrf_and_rejects_replay(client, auth_service, storage):
    await admin(auth_service, storage)
    assert login(client).status_code == 200
    old_refresh = client.cookies.get(COOKIE_PREFIX + "refresh")
    old_csrf = csrf(client)
    assert client.post("/api/auth/browser/refresh", headers=HEADER).status_code == 403
    response = client.post("/api/auth/browser/refresh", headers=old_csrf)
    assert response.status_code == 200
    assert "token" not in response.json()
    assert csrf(client) != old_csrf
    assert client.post("/api/tasks/clear", headers=old_csrf).status_code == 403
    with pytest.raises(ValueError):
        await auth_service.refresh_token(old_refresh)
    assert client.post("/api/auth/browser/logout", headers=csrf(client)).status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert client.cookies.get(COOKIE_PREFIX + "refresh") is None


@pytest.mark.asyncio
async def test_browser_login_and_migration_reject_foreign_origins(client, auth_service, storage):
    await admin(auth_service, storage)
    body = {"username": "admin", "password": "browser-password"}
    assert client.post(LOGIN, json=body).status_code == 403
    assert (
        client.post(
            LOGIN, json=body, headers={**HEADER, "Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(LOGIN, json=body, headers={**HEADER, "Origin": "http://testserver"}).status_code
        == 200
    )
    _, tokens = await auth_service.login(LoginRequest(**body))
    response = client.post(
        "/api/auth/browser/migrate", json={"refresh_token": tokens.refresh_token}, headers=HEADER
    )
    assert response.status_code == 200
    assert "token" not in response.json()
    with pytest.raises(ValueError):
        await auth_service.refresh_token(tokens.refresh_token)


@pytest.mark.asyncio
async def test_configured_https_url_sets_host_secure_cookies_even_behind_http_proxy(
    client, auth_service, storage
):
    await admin(auth_service, storage)
    await storage.set_setting(SYSTEM_BASE_URL_SETTING_KEY, "https://app.example/prefix")
    response = login(client)
    active = [v for v in response.headers.get_list("set-cookie") if "Max-Age=604800" in v]
    assert all(v.startswith(SECURE_PREFIX) and "Secure" in v and "Domain=" not in v for v in active)
    # HTTPS-only cookies are not sent by an HTTP client.
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.asyncio
async def test_tampered_signed_csrf_cookie_is_rejected(client, auth_service, storage):
    await admin(auth_service, storage)
    assert login(client).status_code == 200
    access = client.cookies.get(COOKIE_PREFIX + "access")
    refresh = client.cookies.get(COOKIE_PREFIX + "refresh")
    forged = "nonce." + "a" * 64
    response = client.post(
        "/api/tasks/clear",
        headers={
            "Cookie": f"{COOKIE_PREFIX}access={access}; {COOKIE_PREFIX}refresh={refresh}; {COOKIE_PREFIX}csrf={forged}",
            "X-CSRF-Token": forged,
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_api_bearer_contract_and_password_hash_privacy_remain_intact(
    client, auth_service, storage
):
    await admin(auth_service, storage)
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "browser-password"}
    )
    assert response.status_code == 200
    assert "password_hash" not in response.json()["user"]
    token = response.json()["token"]["access_token"]
    assert (
        client.post("/api/tasks/clear", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )
