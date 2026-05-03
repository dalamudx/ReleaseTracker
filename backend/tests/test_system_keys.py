import json

import pytest
from cryptography.fernet import Fernet

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.models import Credential, LoginRequest
from releasetracker.oidc_models import OIDCProvider
from releasetracker.services.system_keys import SystemKeyManager, rotate_encryption_key
from releasetracker.storage.sqlite import SQLiteStorage


async def authenticate_admin(client, auth_service):
    _, token_pair = await auth_service.login(LoginRequest(username="admin", password="admin"))
    client.headers["Authorization"] = f"Bearer {token_pair.access_token}"
    return client


@pytest.mark.asyncio
async def test_system_key_manager_generates_missing_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))

    manager = SystemKeyManager(tmp_path / "system-secrets.json")
    await manager.initialize()

    payload = json.loads((tmp_path / "system-secrets.json").read_text(encoding="utf-8"))
    assert payload["jwt_secret"] == manager.jwt_secret
    assert payload["encryption_key"] == manager.encryption_key
    assert payload["jwt_secret"] != "x" * 40
    Fernet(payload["encryption_key"].encode("utf-8"))


@pytest.mark.asyncio
async def test_system_key_manager_preserves_existing_keys_and_fills_missing(tmp_path):
    jwt_secret = "j" * 40
    secrets_path = tmp_path / "system-secrets.json"
    secrets_path.write_text(json.dumps({"jwt_secret": jwt_secret}), encoding="utf-8")

    manager = SystemKeyManager(secrets_path)
    await manager.initialize()

    payload = json.loads(secrets_path.read_text(encoding="utf-8"))
    assert payload["jwt_secret"] == jwt_secret
    assert payload["encryption_key"] == manager.encryption_key
    Fernet(payload["encryption_key"].encode("utf-8"))


@pytest.mark.asyncio
async def test_system_key_manager_rejects_invalid_existing_encryption_key(tmp_path):
    secrets_path = tmp_path / "system-secrets.json"
    secrets_path.write_text(
        json.dumps({"jwt_secret": "j" * 40, "encryption_key": "not-a-fernet-key"}),
        encoding="utf-8",
    )

    manager = SystemKeyManager(secrets_path)

    with pytest.raises(ValueError):
        await manager.initialize()


@pytest.mark.asyncio
async def test_security_keys_status_hides_raw_values(client, auth_service, system_key_manager):
    await authenticate_admin(client, auth_service)
    response = client.get("/api/settings/security-keys")

    assert response.status_code == 200
    payload = response.json()
    assert payload["jwt_secret"]["configured"] is True
    assert payload["jwt_secret"]["fingerprint"] == system_key_manager.fingerprint(
        system_key_manager.jwt_secret
    )
    assert payload["encryption_key"]["configured"] is True
    assert payload["encryption_key"]["fingerprint"] == system_key_manager.fingerprint(
        system_key_manager.encryption_key
    )
    assert system_key_manager.jwt_secret not in response.text
    assert system_key_manager.encryption_key not in response.text


@pytest.mark.asyncio
async def test_jwt_secret_rotation_invalidates_sessions(client, auth_service):
    await authenticate_admin(client, auth_service)
    old_secret = auth_service.secret_key
    assert await auth_service.storage.count_active_sessions() == 1

    response = client.post(
        "/api/settings/security-keys/jwt-secret",
        json={"generate": True},
    )

    assert response.status_code == 200
    assert response.json()["invalidated_sessions"] == 1
    assert response.json()["requires_reauth"] is True
    assert auth_service.secret_key != old_secret
    assert await auth_service.storage.count_active_sessions() == 0
    assert client.get("/api/settings/security-keys").status_code == 401


@pytest.mark.asyncio
async def test_encryption_key_rotation_preserves_encrypted_data(storage: SQLiteStorage, system_key_manager):
    credential = Credential(
        name="registry-secret",
        type="docker_runtime",
        token="registry-token",
        secrets={"password": "registry-password"},
    )
    credential_id = await storage.create_credential(credential)
    provider = await storage.save_oauth_provider(
        OIDCProvider(
            name="OIDC",
            slug="oidc",
            client_id="client-id",
            client_secret="client-secret",
        )
    )
    runtime_connection_id = await storage.create_runtime_connection(
        RuntimeConnectionConfig(
            name="legacy-runtime",
            type="docker",
            config={"socket": "unix:///var/run/docker.sock"},
            secrets={"token": "runtime-token"},
        )
    )

    db = await storage._get_connection()
    await db.execute(
        "UPDATE runtime_connections SET secrets = ? WHERE id = ?",
        (storage._dump_json(storage._encrypt_nested_strings({"token": "runtime-token"})), runtime_connection_id),
    )
    await db.commit()
    credential_before = await (
        await db.execute("SELECT token, secrets FROM credentials WHERE id = ?", (credential_id,))
    ).fetchone()
    provider_before = await (
        await db.execute("SELECT client_secret FROM oauth_providers WHERE id = ?", (provider.id,))
    ).fetchone()
    runtime_before = await (
        await db.execute("SELECT secrets FROM runtime_connections WHERE id = ?", (runtime_connection_id,))
    ).fetchone()

    response_stats = await rotate_encryption_key(storage, system_key_manager, generate=True)
    assert response_stats["undecryptable_count"] == 0
    assert response_stats["rotated"]["credentials_token"] == 1
    assert response_stats["rotated"]["credentials_secrets"] == 2
    assert response_stats["rotated"]["oauth_provider_client_secret"] == 1
    assert response_stats["rotated"]["runtime_connection_secrets"] == 1

    stored_credential = await storage.get_credential(credential_id)
    stored_provider = await storage.get_oauth_provider("oidc")
    stored_runtime = await storage.get_runtime_connection(runtime_connection_id)

    assert stored_credential is not None
    assert stored_credential.token == "registry-token"
    assert stored_credential.secrets == {"password": "registry-password", "token": "registry-token"}
    assert stored_provider is not None
    assert stored_provider.client_secret == "client-secret"
    assert stored_runtime is not None
    assert stored_runtime.secrets == {"token": "runtime-token"}

    credential_after = await (
        await db.execute("SELECT token, secrets FROM credentials WHERE id = ?", (credential_id,))
    ).fetchone()
    provider_after = await (
        await db.execute("SELECT client_secret FROM oauth_providers WHERE id = ?", (provider.id,))
    ).fetchone()
    runtime_after = await (
        await db.execute("SELECT secrets FROM runtime_connections WHERE id = ?", (runtime_connection_id,))
    ).fetchone()

    assert credential_after["token"] != credential_before["token"]
    assert credential_after["secrets"] != credential_before["secrets"]
    assert provider_after["client_secret"] != provider_before["client_secret"]
    assert runtime_after["secrets"] != runtime_before["secrets"]


@pytest.mark.asyncio
async def test_invalid_encryption_key_rotation_returns_400(client, auth_service):
    await authenticate_admin(client, auth_service)
    response = client.post(
        "/api/settings/security-keys/encryption-key",
        json={"value": "not-a-fernet-key"},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_undecryptable_fernet_value_blocks_rotation(storage: SQLiteStorage):
    db = await storage._get_connection()
    valid_other_key = Fernet.generate_key()
    bad_token = Fernet(valid_other_key).encrypt(b"secret").decode("utf-8")
    await db.execute(
        "INSERT INTO credentials (name, type, token, secrets, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("bad", "api_token", bad_token, "{}", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    await db.commit()

    with pytest.raises(ValueError):
        await storage.rotate_encrypted_data(Fernet.generate_key().decode("utf-8"))

    row = await (await db.execute("SELECT token FROM credentials WHERE name = ?", ("bad",))).fetchone()
    assert row["token"] == bad_token
    inventory = await storage.get_encryption_key_inventory()
    assert inventory["undecryptable_count"] == 1
