import asyncio

from httpx import ASGITransport, AsyncClient
import pytest
from urllib.parse import parse_qs, urlparse

from releasetracker.main import app
from releasetracker.models import LoginRequest
from releasetracker.oidc_models import OIDCProvider
from releasetracker.routers.oidc import get_oidc_service
from releasetracker.storage.sqlite import SYSTEM_BASE_URL_SETTING_KEY


@pytest.mark.asyncio
async def test_admin_can_register_user(client, auth_service):
    """管理员可以创建新用户"""
    # 1. 确保管理员用户存在
    await auth_service.ensure_admin_user()

    # 2. 管理员登录
    response = client.post("/api/auth/token", data={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    admin_token = response.json()["access_token"]

    # 3. 管理员创建新用户
    response = client.post(
        "/api/auth/register",
        json={"username": "tester", "email": "tester@example.com", "password": "password123"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "tester"
    assert "id" in data

    # 4. 新用户可以登录
    response = client.post(
        "/api/auth/token", data={"username": "tester", "password": "password123"}
    )
    assert response.status_code == 200
    token_data = response.json()
    assert "access_token" in token_data
    token = token_data["access_token"]

    # 5. 新用户获取信息
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["username"] == "tester"


@pytest.mark.asyncio
async def test_register_requires_auth(client):
    """未认证用户不能注册"""
    response = client.post(
        "/api/auth/register",
        json={"username": "unauthorized", "email": "unauth@example.com", "password": "password123"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_failure(client):
    """错误的凭证返回 401"""
    response = client.post(
        "/api/auth/token", data={"username": "nonexistent", "password": "wrongpassword"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_returns_token_pair_and_allows_me(client, auth_service):
    await auth_service.ensure_admin_user()

    login_response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login_response.status_code == 200
    token_pair = login_response.json()["token"]

    original_access_token = token_pair["access_token"]
    original_refresh_token = token_pair["refresh_token"]

    refresh_response = client.post(
        "/api/auth/refresh", params={"refresh_token": original_refresh_token}
    )
    assert refresh_response.status_code == 200
    refreshed = refresh_response.json()
    assert refreshed["access_token"]
    assert refreshed["refresh_token"]
    assert refreshed["expires_in"] > 0
    assert refreshed["token_type"] == "Bearer"
    assert refreshed["access_token"] != original_access_token
    assert refreshed["refresh_token"] != original_refresh_token

    me_response = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {refreshed['access_token']}"}
    )
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "admin"


@pytest.mark.asyncio
async def test_refresh_rejects_invalid_token(client):
    response = client.post("/api/auth/refresh", params={"refresh_token": "invalid-token"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_old_refresh_token_reuse_fails_after_rotation(client, auth_service):
    await auth_service.ensure_admin_user()

    login_response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login_response.status_code == 200
    original_refresh_token = login_response.json()["token"]["refresh_token"]

    first_refresh_response = client.post(
        "/api/auth/refresh", params={"refresh_token": original_refresh_token}
    )
    assert first_refresh_response.status_code == 200

    reused_refresh_response = client.post(
        "/api/auth/refresh", params={"refresh_token": original_refresh_token}
    )
    assert reused_refresh_response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_fails_after_logout(client, auth_service):
    await auth_service.ensure_admin_user()

    login_response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login_response.status_code == 200
    token_pair = login_response.json()["token"]

    logout_response = client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {token_pair['access_token']}"},
    )
    assert logout_response.status_code == 200

    refresh_response = client.post(
        "/api/auth/refresh", params={"refresh_token": token_pair["refresh_token"]}
    )
    assert refresh_response.status_code == 401


@pytest.mark.asyncio
async def test_concurrent_refresh_reuse_allows_only_one_success(auth_service, storage):
    await auth_service.ensure_admin_user()
    _, token_pair = await auth_service.login(LoginRequest(username="admin", password="admin"))

    previous_storage = getattr(app.state, "storage", None)
    previous_system_key_manager = getattr(app.state, "system_key_manager", None)
    app.state.storage = storage
    app.state.system_key_manager = storage.system_key_manager

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as async_client:
        try:
            responses = await asyncio.gather(
                async_client.post(
                    "/api/auth/refresh",
                    params={"refresh_token": token_pair.refresh_token},
                ),
                async_client.post(
                    "/api/auth/refresh",
                    params={"refresh_token": token_pair.refresh_token},
                ),
            )

            statuses = sorted(response.status_code for response in responses)
            assert statuses == [200, 401]

            successful_response = next(
                response for response in responses if response.status_code == 200
            )
            refreshed = successful_response.json()
            assert refreshed["refresh_token"] != token_pair.refresh_token

            retry_response = await async_client.post(
                "/api/auth/refresh",
                params={"refresh_token": token_pair.refresh_token},
            )
            assert retry_response.status_code == 401
        finally:
            if previous_storage is None:
                delattr(app.state, "storage")
            else:
                app.state.storage = previous_storage
            if previous_system_key_manager is None:
                delattr(app.state, "system_key_manager")
            else:
                app.state.system_key_manager = previous_system_key_manager


@pytest.mark.asyncio
async def test_oidc_authorize_uses_configured_base_url(client, storage):
    provider = await storage.save_oauth_provider(
        OIDCProvider(
            name="Mock",
            slug="mock",
            client_id="client-id",
            client_secret="secret",
            authorization_url="https://idp.example.com/authorize",
            token_url="https://idp.example.com/token",
            userinfo_url="https://idp.example.com/userinfo",
            discovery_enabled=False,
            enabled=True,
        )
    )
    assert provider.slug == "mock"
    await storage.set_setting(SYSTEM_BASE_URL_SETTING_KEY, "https://example.com/releasetracker")

    response = client.get("/api/auth/oidc/mock/authorize", follow_redirects=False)

    assert response.status_code == 307
    redirect = urlparse(response.headers["location"])
    params = parse_qs(redirect.query)
    assert params["redirect_uri"] == [
        "https://example.com/releasetracker/auth/oidc/mock/callback"
    ]


@pytest.mark.asyncio
async def test_oidc_callback_redirect_includes_refresh_token_payload(client, auth_service, storage):
    await auth_service.ensure_admin_user()
    user = await storage.get_user_by_username("admin")
    assert user is not None

    await storage.set_setting(SYSTEM_BASE_URL_SETTING_KEY, "https://example.com/releasetracker")
    await storage.save_oauth_state("test-state", "mock", "verifier")

    class MockOIDCService:
        async def handle_callback(self, **kwargs):
            assert kwargs["redirect_uri"] == (
                "https://example.com/releasetracker/auth/oidc/mock/callback"
            )
            return user, auth_service._create_token_pair(user)

    app.dependency_overrides[get_oidc_service] = lambda: MockOIDCService()

    try:
        response = client.get(
            "/auth/oidc/mock/callback?code=test-code&state=test-state",
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(get_oidc_service, None)

    assert response.status_code == 307
    redirect = urlparse(response.headers["location"])
    assert redirect.scheme == "https"
    assert redirect.netloc == "example.com"
    assert redirect.path == "/releasetracker/"
    fragment = parse_qs(redirect.fragment)
    assert fragment["token"][0]
    assert fragment["access_token"][0] == fragment["token"][0]
    assert fragment["refresh_token"][0]
    assert fragment["token_type"] == ["Bearer"]
    assert int(fragment["expires_in"][0]) > 0
