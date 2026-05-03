import pytest


def _admin_headers(client) -> dict[str, str]:
    response = client.post("/api/auth/token", data={"username": "admin", "password": "admin"})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _provider_payload(slug: str, name: str) -> dict:
    return {
        "name": name,
        "slug": slug,
        "issuer_url": "https://issuer.example.com",
        "discovery_enabled": True,
        "client_id": f"{slug}-client-id",
        "client_secret": "super-secret",
        "authorization_url": "https://issuer.example.com/authorize",
        "token_url": "https://issuer.example.com/token",
        "userinfo_url": "https://issuer.example.com/userinfo",
        "jwks_uri": "https://issuer.example.com/jwks",
        "scopes": "openid email profile",
        "enabled": True,
        "icon_url": "https://issuer.example.com/icon.png",
        "description": f"{name} provider",
    }


@pytest.mark.asyncio
async def test_create_oidc_provider_enforces_single_provider_limit(client, auth_service):
    await auth_service.ensure_admin_user()
    headers = _admin_headers(client)

    first_response = client.post(
        "/api/oidc-providers",
        json=_provider_payload("primary", "Primary Provider"),
        headers=headers,
    )

    assert first_response.status_code == 201, first_response.text
    assert first_response.json()["message"] == "OIDC 提供商已创建"
    assert isinstance(first_response.json()["id"], int)

    second_response = client.post(
        "/api/oidc-providers",
        json=_provider_payload("secondary", "Secondary Provider"),
        headers=headers,
    )

    assert second_response.status_code == 409
    assert second_response.json()["detail"] == "仅允许配置一个 OIDC 提供商"


@pytest.mark.asyncio
async def test_delete_then_recreate_oidc_provider(client, auth_service):
    """Delete the sole provider then recreate — guard allows it."""
    await auth_service.ensure_admin_user()
    headers = _admin_headers(client)

    create_resp = client.post(
        "/api/oidc-providers",
        json=_provider_payload("sole", "Sole Provider"),
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    provider_id = create_resp.json()["id"]

    delete_resp = client.delete(f"/api/oidc-providers/{provider_id}", headers=headers)
    assert delete_resp.status_code == 200
    assert delete_resp.json()["message"] == "OIDC 提供商已删除"

    recreate_resp = client.post(
        "/api/oidc-providers",
        json=_provider_payload("sole", "Sole Provider Again"),
        headers=headers,
    )
    assert recreate_resp.status_code == 201, recreate_resp.text
    assert isinstance(recreate_resp.json()["id"], int)


@pytest.mark.asyncio
async def test_update_sole_oidc_provider(client, auth_service):
    """Update still works on the only provider."""
    await auth_service.ensure_admin_user()
    headers = _admin_headers(client)

    create_resp = client.post(
        "/api/oidc-providers",
        json=_provider_payload("updateme", "Before Update"),
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    provider_id = create_resp.json()["id"]

    update_resp = client.put(
        f"/api/oidc-providers/{provider_id}",
        json={"name": "After Update"},
        headers=headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["message"] == "OIDC 提供商已更新"

    get_resp = client.get(f"/api/oidc-providers/{provider_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "After Update"


@pytest.mark.asyncio
async def test_delete_sole_oidc_provider(client, auth_service):
    """Delete works on the only provider and leaves list empty."""
    await auth_service.ensure_admin_user()
    headers = _admin_headers(client)

    create_resp = client.post(
        "/api/oidc-providers",
        json=_provider_payload("deleteme", "To Delete"),
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    provider_id = create_resp.json()["id"]

    delete_resp = client.delete(f"/api/oidc-providers/{provider_id}", headers=headers)
    assert delete_resp.status_code == 200
    assert delete_resp.json()["message"] == "OIDC 提供商已删除"

    list_resp = client.get("/api/oidc-providers", headers=headers)
    assert list_resp.status_code == 200
    assert list_resp.json() == []


@pytest.mark.asyncio
async def test_public_providers_list_empty(client):
    """Public /api/auth/oidc/providers returns an empty list when no providers exist."""
    resp = client.get("/api/auth/oidc/providers")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 0


@pytest.mark.asyncio
async def test_public_providers_list_one_enabled(client, auth_service):
    """Public /api/auth/oidc/providers returns a list with one entry for an enabled provider."""
    await auth_service.ensure_admin_user()
    headers = _admin_headers(client)

    create_resp = client.post(
        "/api/oidc-providers",
        json=_provider_payload("pubtest", "Public Test Provider"),
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text

    resp = client.get("/api/auth/oidc/providers")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    entry = data[0]
    assert entry["slug"] == "pubtest"
    assert entry["name"] == "Public Test Provider"
    assert "client_secret" not in entry
    assert "client_id" not in entry
