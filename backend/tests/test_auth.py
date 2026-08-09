import asyncio
import logging

from httpx import ASGITransport, AsyncClient
import pytest
from urllib.parse import parse_qs, urlparse

from releasetracker.main import app
from releasetracker.models import LoginRequest, User
from releasetracker.oidc_models import OIDCProvider
from releasetracker.routers.oidc import (
    OIDC_BROWSER_COOKIE_NAME,
    _hash_browser_binding,
    get_oidc_service,
)
from releasetracker.services.auth import (
    BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY,
    pwd_context,
)
from releasetracker.storage.sqlite import (
    ADMIN_OIDC_ISSUER_SETTING_KEY,
    ADMIN_OIDC_SUBJECT_SETTING_KEY,
    ADMIN_PASSWORD_RESET_REQUIRED_SETTING_KEY,
    ADMIN_USER_ID_SETTING_KEY,
    SYSTEM_BASE_URL_SETTING_KEY,
    SQLiteStorage,
)

BOOTSTRAP_LOG_PREFIX = "Bootstrap admin user created; one-time bootstrap admin password:"


async def _remove_admin_user(storage) -> None:
    db = await storage._get_connection()
    await db.execute("DELETE FROM users WHERE username = ?", ("admin",))
    await db.commit()


async def _reset_bootstrap_state(storage) -> None:
    await _remove_admin_user(storage)
    for key in (
        ADMIN_USER_ID_SETTING_KEY,
        BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY,
        ADMIN_PASSWORD_RESET_REQUIRED_SETTING_KEY,
        ADMIN_OIDC_ISSUER_SETTING_KEY,
        ADMIN_OIDC_SUBJECT_SETTING_KEY,
    ):
        await storage.delete_setting(key)


@pytest.mark.asyncio
async def test_ensure_admin_user_fresh_database_creates_random_password_once(
    auth_service, storage, monkeypatch, caplog
):
    await _reset_bootstrap_state(storage)
    bootstrap_password = "generated-bootstrap-password"
    token_urlsafe_calls = []

    def fake_token_urlsafe(nbytes):
        token_urlsafe_calls.append(nbytes)
        return bootstrap_password

    monkeypatch.setattr("releasetracker.services.auth.secrets.token_urlsafe", fake_token_urlsafe)
    caplog.set_level(logging.INFO, logger="releasetracker.services.auth")

    await auth_service.ensure_admin_user()

    admin = await storage.get_user_by_username("admin")
    assert admin is not None
    assert pwd_context.verify(bootstrap_password, admin.password_hash)
    assert not pwd_context.verify("admin", admin.password_hash)
    assert token_urlsafe_calls == [32]
    assert await storage.get_setting(BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY) == "true"
    assert await storage.get_admin_user_id() == admin.id
    assert [
        record.getMessage()
        for record in caplog.records
        if record.name == "releasetracker.services.auth"
        and record.getMessage().startswith(BOOTSTRAP_LOG_PREFIX)
    ] == [f"{BOOTSTRAP_LOG_PREFIX} {bootstrap_password}"]


@pytest.mark.asyncio
async def test_ensure_admin_user_repeated_call_is_idempotent(
    auth_service, storage, monkeypatch, caplog
):
    await _reset_bootstrap_state(storage)
    bootstrap_password = "generated-bootstrap-password"
    monkeypatch.setattr(
        "releasetracker.services.auth.secrets.token_urlsafe",
        lambda _nbytes: bootstrap_password,
    )
    caplog.set_level(logging.INFO, logger="releasetracker.services.auth")

    await auth_service.ensure_admin_user()
    original_admin = await storage.get_user_by_username("admin")
    await auth_service.ensure_admin_user()
    current_admin = await storage.get_user_by_username("admin")

    assert original_admin is not None
    assert current_admin is not None
    assert current_admin.id == original_admin.id
    assert current_admin.password_hash == original_admin.password_hash
    assert (
        sum(
            record.name == "releasetracker.services.auth"
            and record.getMessage().startswith(BOOTSTRAP_LOG_PREFIX)
            for record in caplog.records
        )
        == 1
    )


@pytest.mark.asyncio
async def test_ensure_admin_user_existing_admin_is_unchanged(
    auth_service, storage, monkeypatch, caplog
):
    existing_admin = await storage.get_user_by_username("admin")
    assert existing_admin is not None
    await storage.delete_setting(BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY)
    await storage.delete_setting(ADMIN_USER_ID_SETTING_KEY)

    def fail_if_password_generated(_nbytes):
        pytest.fail("existing admin must not generate a bootstrap password")

    monkeypatch.setattr(
        "releasetracker.services.auth.secrets.token_urlsafe", fail_if_password_generated
    )
    caplog.set_level(logging.INFO, logger="releasetracker.services.auth")

    await auth_service.ensure_admin_user()

    current_admin = await storage.get_user_by_username("admin")
    assert current_admin is not None
    assert current_admin.id == existing_admin.id
    assert current_admin.password_hash == existing_admin.password_hash
    assert await storage.get_setting(BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY) == "true"
    assert not any(
        record.name == "releasetracker.services.auth"
        and record.getMessage().startswith(BOOTSTRAP_LOG_PREFIX)
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_legacy_stable_admin_is_marked_revoked_and_blocked(auth_service, storage):
    admin_id = await storage.get_admin_user_id()
    assert admin_id is not None
    admin = await storage.get_user_by_id(admin_id)
    assert admin is not None
    _, token_pair = await auth_service.login(
        LoginRequest(username=admin.username, password="test-admin-password")
    )
    await storage.update_user_password(admin_id, pwd_context.hash("admin"))

    await auth_service.ensure_admin_user()

    assert await storage.get_setting(ADMIN_PASSWORD_RESET_REQUIRED_SETTING_KEY) == "true"
    assert await storage.count_active_sessions() == 0
    with pytest.raises(ValueError, match="reset is required"):
        await auth_service.login(LoginRequest(username=admin.username, password="admin"))
    with pytest.raises(ValueError, match="reset is required"):
        await auth_service.get_current_user(token_pair.access_token)
    with pytest.raises(ValueError, match="reset is required"):
        await auth_service.refresh_token(token_pair.refresh_token)


@pytest.mark.asyncio
async def test_operator_reset_clears_marker_preserves_identity_and_rejects_admin(
    auth_service, storage
):
    admin_id = await storage.get_admin_user_id()
    assert admin_id is not None
    await storage.update_user_password(admin_id, pwd_context.hash("admin"))
    await auth_service.ensure_admin_user()

    with pytest.raises(ValueError, match="legacy password"):
        await auth_service.reset_admin_password("admin")
    assert await storage.is_admin_password_reset_required()

    reset_user_id = await auth_service.reset_admin_password("replacement-password")
    assert reset_user_id == admin_id
    assert not await storage.is_admin_password_reset_required()
    user, _ = await auth_service.login(
        LoginRequest(username="admin", password="replacement-password")
    )
    assert user.id == admin_id


@pytest.mark.asyncio
async def test_stable_admin_id_survives_rename_and_username_reuse(client, auth_service, storage):
    admin_id = await storage.get_admin_user_id()
    assert admin_id is not None
    db = await storage._get_connection()
    await db.execute("UPDATE users SET username = ? WHERE id = ?", ("renamed-admin", admin_id))
    await db.commit()
    impostor = await storage.create_user(
        User(
            username="admin",
            email="impostor@example.com",
            password_hash=pwd_context.hash("impostor-password"),
        )
    )

    stable_login = client.post(
        "/api/auth/token",
        data={"username": "renamed-admin", "password": "test-admin-password"},
    )
    assert stable_login.status_code == 200
    stable_response = client.get(
        "/api/settings",
        headers={"Authorization": f"Bearer {stable_login.json()['access_token']}"},
    )
    assert stable_response.status_code == 200

    impostor_login = client.post(
        "/api/auth/token",
        data={"username": "admin", "password": "impostor-password"},
    )
    assert impostor_login.status_code == 200
    impostor_response = client.get(
        "/api/settings",
        headers={"Authorization": f"Bearer {impostor_login.json()['access_token']}"},
    )
    assert impostor_response.status_code == 403
    assert impostor.id != admin_id


@pytest.mark.asyncio
async def test_admin_identity_settings_are_hidden_and_immutable_through_api(authed_client, storage):
    admin_user_id = await storage.get_admin_user_id()
    assert admin_user_id is not None

    list_response = authed_client.get("/api/settings")
    assert list_response.status_code == 200
    assert ADMIN_USER_ID_SETTING_KEY not in {item["key"] for item in list_response.json()}

    update_response = authed_client.post(
        "/api/settings",
        json={"key": ADMIN_USER_ID_SETTING_KEY, "value": "999999"},
    )
    delete_response = authed_client.delete(f"/api/settings/{ADMIN_USER_ID_SETTING_KEY}")

    assert update_response.status_code == 403
    assert delete_response.status_code == 403
    assert await storage.get_admin_user_id() == admin_user_id


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_value", ["not-an-id", "0", "999999"])
async def test_ensure_admin_user_invalid_reference_fails_closed(
    auth_service, storage, stored_value
):
    await storage.set_setting(ADMIN_USER_ID_SETTING_KEY, stored_value)
    with pytest.raises(RuntimeError):
        await auth_service.ensure_admin_user()


@pytest.mark.asyncio
async def test_ensure_admin_user_deleted_bootstrap_admin_fails_closed(
    auth_service, storage, monkeypatch, caplog
):
    await _reset_bootstrap_state(storage)
    bootstrap_password = "generated-bootstrap-password"
    monkeypatch.setattr(
        "releasetracker.services.auth.secrets.token_urlsafe",
        lambda _nbytes: bootstrap_password,
    )
    caplog.set_level(logging.INFO, logger="releasetracker.services.auth")
    await auth_service.ensure_admin_user()
    await _remove_admin_user(storage)
    caplog.clear()

    def fail_if_password_generated(_nbytes):
        pytest.fail("deleted bootstrap admin must not generate a new password")

    monkeypatch.setattr(
        "releasetracker.services.auth.secrets.token_urlsafe", fail_if_password_generated
    )

    with pytest.raises(RuntimeError, match="does not reference an existing user"):
        await auth_service.ensure_admin_user()

    assert await storage.get_user_by_username("admin") is None
    assert await storage.get_setting(BOOTSTRAP_ADMIN_INITIALIZED_SETTING_KEY) == "true"
    assert not any(BOOTSTRAP_LOG_PREFIX in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("authenticated_as", ["anonymous", "nonadmin", "admin"])
async def test_register_is_disabled_for_every_caller(
    client, auth_service, storage, authenticated_as
):
    headers = {}
    if authenticated_as != "anonymous":
        username = "admin" if authenticated_as == "admin" else "authtester"
        password = "test-admin-password" if authenticated_as == "admin" else "password123"
        login = client.post("/api/auth/token", data={"username": username, "password": password})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.post(
        "/api/auth/register",
        json={"username": "blocked", "email": "blocked@example.com", "password": "password123"},
        headers=headers,
    )
    expected_status = 401 if authenticated_as == "anonymous" else 403
    assert response.status_code == expected_status
    assert await storage.get_user_by_username("blocked") is None

    with pytest.raises(ValueError, match="Registration is disabled"):
        from releasetracker.models import RegisterRequest

        await auth_service.register(
            RegisterRequest(
                username="blocked-service",
                email="blocked-service@example.com",
                password="password123",
            )
        )


@pytest.mark.asyncio
async def test_local_password_login_remains_available_after_oidc_binding(client, storage):
    await storage.bind_admin_oidc_identity("https://issuer.example.com", "subject-1")
    response = client.post(
        "/api/auth/token", data={"username": "admin", "password": "test-admin-password"}
    )
    assert response.status_code == 200


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

    login_response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "test-admin-password"}
    )
    assert login_response.status_code == 200
    token_pair = login_response.json()["token"]

    original_access_token = token_pair["access_token"]
    original_refresh_token = token_pair["refresh_token"]

    refresh_response = client.post(
        "/api/auth/refresh", json={"refresh_token": original_refresh_token}
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
    response = client.post("/api/auth/refresh", json={"refresh_token": "invalid-token"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rejects_query_only_transport(client):
    response = client.post(
        "/api/auth/refresh",
        params={"refresh_token": "query-token-must-not-be-read"},
    )
    assert response.status_code in {400, 422}


@pytest.mark.asyncio
async def test_refresh_rejects_mixed_query_and_body_without_consuming_body_token(
    client,
    auth_service,
    caplog,
):
    await auth_service.ensure_admin_user()
    login_response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "test-admin-password"}
    )
    refresh_token = login_response.json()["token"]["refresh_token"]

    caplog.clear()
    mixed_response = client.post(
        "/api/auth/refresh",
        params={"refresh_token": "query-secret"},
        json={"refresh_token": refresh_token},
    )
    assert mixed_response.status_code == 400
    assert "query-secret" not in caplog.text
    assert refresh_token not in caplog.text

    body_response = client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert body_response.status_code == 200
    assert "refresh_token=" not in str(body_response.request.url)


@pytest.mark.asyncio
async def test_old_refresh_token_reuse_fails_after_rotation(client, auth_service):
    await auth_service.ensure_admin_user()

    login_response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "test-admin-password"}
    )
    assert login_response.status_code == 200
    original_refresh_token = login_response.json()["token"]["refresh_token"]

    first_refresh_response = client.post(
        "/api/auth/refresh", json={"refresh_token": original_refresh_token}
    )
    assert first_refresh_response.status_code == 200

    reused_refresh_response = client.post(
        "/api/auth/refresh", json={"refresh_token": original_refresh_token}
    )
    assert reused_refresh_response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_fails_after_logout(client, auth_service):
    await auth_service.ensure_admin_user()

    login_response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "test-admin-password"}
    )
    assert login_response.status_code == 200
    token_pair = login_response.json()["token"]

    logout_response = client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {token_pair['access_token']}"},
    )
    assert logout_response.status_code == 200

    refresh_response = client.post(
        "/api/auth/refresh", json={"refresh_token": token_pair["refresh_token"]}
    )
    assert refresh_response.status_code == 401


@pytest.mark.asyncio
async def test_concurrent_refresh_reuse_allows_only_one_success(auth_service, storage):
    await auth_service.ensure_admin_user()
    _, token_pair = await auth_service.login(
        LoginRequest(username="admin", password="test-admin-password")
    )

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
                    json={"refresh_token": token_pair.refresh_token},
                ),
                async_client.post(
                    "/api/auth/refresh",
                    json={"refresh_token": token_pair.refresh_token},
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
                json={"refresh_token": token_pair.refresh_token},
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
            issuer_url="https://idp.example.com",
            authorization_url="https://idp.example.com/authorize",
            token_url="https://idp.example.com/token",
            jwks_uri="https://idp.example.com/jwks",
            discovery_enabled=False,
            enabled=True,
        )
    )
    assert provider.slug == "mock"
    await storage.set_setting(SYSTEM_BASE_URL_SETTING_KEY, "https://example.com/releasetracker")
    await storage.bind_admin_oidc_identity("https://idp.example.com", "admin-subject")

    response = client.get("/api/auth/oidc/mock/authorize", follow_redirects=False)

    assert response.status_code == 307
    redirect = urlparse(response.headers["location"])
    params = parse_qs(redirect.query)
    assert params["redirect_uri"] == ["https://example.com/releasetracker/auth/oidc/mock/callback"]
    assert params["nonce"][0]
    set_cookie = response.headers["set-cookie"]
    assert OIDC_BROWSER_COOKIE_NAME in set_cookie
    assert "Secure" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Max-Age=600" in set_cookie


@pytest.mark.asyncio
async def test_oidc_authorize_rejects_missing_base_url_without_host_fallback(client, storage):
    await storage.save_oauth_provider(
        OIDCProvider(
            name="Mock",
            slug="mock",
            client_id="client-id",
            client_secret="secret",
            issuer_url="https://idp.example.com",
            authorization_url="https://idp.example.com/authorize",
            token_url="https://idp.example.com/token",
            jwks_uri="https://idp.example.com/jwks",
            discovery_enabled=False,
            enabled=True,
        )
    )
    await storage.bind_admin_oidc_identity("https://idp.example.com", "admin-subject")

    response = client.get(
        "/api/auth/oidc/mock/authorize",
        headers={"Host": "attacker.example"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "BASE URL" in response.json()["detail"]
    db = await storage._get_connection()
    assert (await (await db.execute("SELECT COUNT(*) FROM oauth_states")).fetchone())[0] == 0


@pytest.mark.asyncio
async def test_oidc_callback_browser_mismatch_does_not_consume_state(client, storage):
    await storage.set_setting(SYSTEM_BASE_URL_SETTING_KEY, "https://example.com")
    await storage.save_oauth_state(
        "bound-state",
        "mock",
        "verifier",
        "nonce",
        "login",
        _hash_browser_binding("correct-browser"),
    )

    response = client.get(
        "/auth/oidc/mock/callback?code=test-code&state=bound-state",
        headers={"Cookie": f"{OIDC_BROWSER_COOKIE_NAME}=wrong-browser"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    db = await storage._get_connection()
    row = await (
        await db.execute("SELECT state FROM oauth_states WHERE state = ?", ("bound-state",))
    ).fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_oauth_state_is_consumed_atomically_across_storage_instances(storage):
    await storage.save_oauth_state(
        "single-use-state",
        "mock",
        "verifier",
        "nonce",
        "login",
        _hash_browser_binding("browser-token"),
    )
    second_storage = SQLiteStorage(
        storage.db_path,
        system_key_manager=storage.system_key_manager,
    )
    try:
        results = await asyncio.gather(
            storage.consume_oauth_state(
                "single-use-state",
                "mock",
                _hash_browser_binding("browser-token"),
            ),
            second_storage.consume_oauth_state(
                "single-use-state",
                "mock",
                _hash_browser_binding("browser-token"),
            ),
        )
    finally:
        await second_storage.close()

    assert sum(result is not None for result in results) == 1


@pytest.mark.asyncio
async def test_oidc_callback_redirect_includes_refresh_token_payload(client, auth_service, storage):
    await auth_service.ensure_admin_user()
    user = await storage.get_user_by_username("admin")
    assert user is not None

    await storage.set_setting(SYSTEM_BASE_URL_SETTING_KEY, "https://example.com/releasetracker")
    await storage.save_oauth_state(
        "test-state",
        "mock",
        "verifier",
        "test-nonce",
        "login",
        _hash_browser_binding("browser-token"),
    )

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
            headers={"Cookie": f"{OIDC_BROWSER_COOKIE_NAME}=browser-token"},
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
    assert f'{OIDC_BROWSER_COOKIE_NAME}=""' in response.headers["set-cookie"]
