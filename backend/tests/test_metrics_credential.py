from pathlib import Path

import pytest

from releasetracker.services.metrics_credential import (
    MAX_TOKEN_LENGTH,
    METRICS_TOKEN_SETTING_KEY,
    MIN_TOKEN_LENGTH,
    encode_token,
    get_digest,
    read_token,
    matches,
    save_token,
)

TOKEN = "Test_16-Char-Key"
ROTATED = "r" * 48


@pytest.mark.parametrize(
    "token",
    [
        "short",
        "x" * (MIN_TOKEN_LENGTH - 1),
        "y" * (MAX_TOKEN_LENGTH + 1),
        "z" * 40 + " ",
        "\u00e9" * 32,
    ],
)
def test_invalid_tokens_are_rejected_without_echo(token):
    with pytest.raises(ValueError) as rejected:
        encode_token(token)
    assert token not in str(rejected.value)


def test_empty_token_requests_revocation_and_valid_tokens_become_digests():
    assert encode_token("") == ""
    digest = encode_token(TOKEN)
    assert len(digest) == 64 and digest != TOKEN and TOKEN not in digest
    assert encode_token(TOKEN) == digest
    assert matches(digest, TOKEN)
    assert not matches(digest, ROTATED)
    assert not matches(digest, "")
    assert not matches("", TOKEN)
    assert not matches(digest, "\u00e9" * 32)


@pytest.mark.asyncio
async def test_credentials_are_encrypted_and_verified_by_digest(storage):
    assert await save_token(storage, TOKEN) is True
    assert await get_digest(storage) == encode_token(TOKEN)
    # SQLite may keep the newest row in its write-ahead log until a checkpoint.
    raw = b"".join(
        path.read_bytes()
        for path in Path(storage.db_path).parent.glob(f"{Path(storage.db_path).name}*")
    )
    assert TOKEN.encode() not in raw
    assert encode_token(TOKEN).encode() in raw
    from cryptography.fernet import Fernet

    new_key = Fernet.generate_key().decode()
    await storage.rotate_encrypted_data(new_key)
    storage.set_encryption_key(new_key)
    assert await read_token(storage) == (True, TOKEN)
    assert (await storage.get_encryption_key_inventory())["undecryptable_count"] == 0

    assert await save_token(storage, "") is False
    assert await get_digest(storage) == ""
    # A revocation is persisted so a later legacy import cannot restore a token.
    assert await storage.get_setting(METRICS_TOKEN_SETTING_KEY) == ""


@pytest.mark.asyncio
async def test_admin_token_lifecycle_controls_scrape_access(authed_client, storage):
    assert authed_client.get("/api/settings/metrics-token").json() == {"configured": False}
    assert authed_client.get("/metrics").status_code == 404

    saved = authed_client.put("/api/settings/metrics-token", json={"token": TOKEN})
    assert saved.status_code == 200
    assert saved.json() == {"configured": True}
    assert TOKEN not in saved.text
    current = authed_client.get("/api/settings/metrics-token")
    assert current.json() == {"configured": True, "token": TOKEN}
    assert current.headers["Cache-Control"] == "no-store"

    assert authed_client.get("/metrics").status_code == 401
    assert (
        authed_client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    )
    assert (
        authed_client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).status_code
        == 200
    )

    assert (
        authed_client.put("/api/settings/metrics-token", json={"token": ROTATED}).status_code == 200
    )
    assert (
        authed_client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).status_code
        == 401
    )
    assert (
        authed_client.get("/metrics", headers={"Authorization": f"Bearer {ROTATED}"}).status_code
        == 200
    )

    revoked = authed_client.put("/api/settings/metrics-token", json={"token": ""})
    assert revoked.json() == {"configured": False}
    assert (
        authed_client.get("/metrics", headers={"Authorization": f"Bearer {ROTATED}"}).status_code
        == 404
    )


@pytest.mark.asyncio
async def test_invalid_token_submission_keeps_the_previous_credential(authed_client):
    assert (
        authed_client.put("/api/settings/metrics-token", json={"token": TOKEN}).status_code == 200
    )
    rejected = authed_client.put("/api/settings/metrics-token", json={"token": "too-short"})
    assert rejected.status_code == 400
    assert "too-short" not in rejected.text
    assert TOKEN not in rejected.text
    assert (
        authed_client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).status_code
        == 200
    )


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"token": 12345678901234},
        {"token": ["leak-me-please"]},
        {"token": {"nested": "leak-me-please"}},
        {"value": "leak-me-please"},
        ["leak-me-please"],
        "leak-me-please",
        {},
    ],
)
async def test_malformed_token_bodies_never_echo_the_submitted_value(authed_client, body):
    response = authed_client.put("/api/settings/metrics-token", json=body)
    assert response.status_code == 400
    assert "leak-me-please" not in response.text
    assert "12345678901234" not in response.text
    assert authed_client.get("/api/settings/metrics-token").json() == {"configured": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"not json", b"", b"\xff\xfe"])
async def test_unreadable_token_bodies_are_refused(authed_client, body):
    response = authed_client.put(
        "/api/settings/metrics-token",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert authed_client.get("/api/settings/metrics-token").json() == {"configured": False}


@pytest.mark.asyncio
async def test_metrics_credential_routes_reject_non_administrators(non_admin_client):
    assert non_admin_client.get("/api/settings/metrics-token").status_code == 403
    assert (
        non_admin_client.put("/api/settings/metrics-token", json={"token": TOKEN}).status_code
        == 403
    )


@pytest.mark.asyncio
async def test_metrics_credential_routes_require_authentication(client):
    assert client.get("/api/settings/metrics-token").status_code == 401
    assert client.put("/api/settings/metrics-token", json={"token": TOKEN}).status_code == 401


@pytest.mark.asyncio
async def test_generic_settings_routes_cannot_reach_the_verifier(authed_client):
    assert (
        authed_client.put("/api/settings/metrics-token", json={"token": TOKEN}).status_code == 200
    )
    values = {item["key"]: item["value"] for item in authed_client.get("/api/settings").json()}
    assert METRICS_TOKEN_SETTING_KEY not in values

    blocked = authed_client.post(
        "/api/settings", json={"key": METRICS_TOKEN_SETTING_KEY, "value": TOKEN}
    )
    assert blocked.status_code == 403
    assert authed_client.delete(f"/api/settings/{METRICS_TOKEN_SETTING_KEY}").status_code == 403
    assert TOKEN not in authed_client.get("/api/settings").text
    assert authed_client.get("/api/settings/metrics-token").json()["token"] == TOKEN
    assert (
        authed_client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).status_code
        == 200
    )


@pytest.mark.asyncio
async def test_verifier_survives_a_database_copy(storage, tmp_path, system_key_manager):
    """Backups and restores carry the settings row, so a restored instance keeps scraping."""
    import sqlite3

    from releasetracker.storage.sqlite import SQLiteStorage

    await save_token(storage, TOKEN)
    restored_path = tmp_path / "restored.db"
    # A backup snapshot includes committed rows still held in the write-ahead log.
    with sqlite3.connect(storage.db_path) as source, sqlite3.connect(restored_path) as target:
        source.backup(target)
    restored = SQLiteStorage(str(restored_path), system_key_manager=system_key_manager)
    try:
        assert await get_digest(restored) == encode_token(TOKEN)
        assert await read_token(restored) == (True, TOKEN)
    finally:
        await restored.close()


@pytest.mark.parametrize("length", [0, 15, 16, 31, 32])
async def test_legacy_environment_token_is_imported_once_as_a_verifier(
    storage, monkeypatch, length
):
    from releasetracker.services.instance_policy import IMPORT_MARKER
    from releasetracker.services.legacy_settings import import_legacy_settings

    legacy_token = "t" * length
    monkeypatch.setenv("RELEASETRACKER_METRICS_TOKEN", legacy_token)
    await import_legacy_settings(await storage._get_connection())
    expected_digest = encode_token(legacy_token) if length >= 32 else ""
    assert await get_digest(storage) == expected_digest
    assert await read_token(storage) == (bool(expected_digest), None)
    assert await storage.get_setting(IMPORT_MARKER) == "true"

    monkeypatch.setenv("RELEASETRACKER_METRICS_TOKEN", ROTATED)
    assert await import_legacy_settings(await storage._get_connection()) == 0
    assert await get_digest(storage) == expected_digest


@pytest.mark.asyncio
async def test_explicit_revocation_survives_the_first_legacy_import(storage, monkeypatch):
    from releasetracker.services.instance_policy import IMPORT_MARKER
    from releasetracker.services.legacy_settings import import_legacy_settings

    assert await save_token(storage, "") is False
    monkeypatch.setenv("RELEASETRACKER_METRICS_TOKEN", ROTATED)
    await import_legacy_settings(await storage._get_connection())
    assert await storage.get_setting(IMPORT_MARKER) == "true"
    assert await get_digest(storage) == ""


@pytest.mark.parametrize("token", ["x" * 513, "invalid token" * 3])
async def test_legacy_token_rejected_by_validation_aborts_the_import(storage, monkeypatch, token):
    from releasetracker.services.instance_policy import IMPORT_MARKER
    from releasetracker.services.legacy_settings import import_legacy_settings

    monkeypatch.setenv("RELEASETRACKER_WORKER_POLL_SECONDS", "8")
    monkeypatch.setenv("RELEASETRACKER_METRICS_TOKEN", token)
    with pytest.raises(ValueError):
        await import_legacy_settings(await storage._get_connection())
    assert await get_digest(storage) == ""
    assert await storage.get_setting(IMPORT_MARKER) is None
    assert await storage.get_setting("system.worker_poll_seconds") is None
