import pytest

from releasetracker.services.instance_policy import IMPORT_MARKER, POLICY_DEFAULTS, normalize_policy
from releasetracker.services.legacy_settings import import_legacy_settings


@pytest.mark.asyncio
async def test_legacy_import_is_atomic_and_preserves_saved_values(storage, monkeypatch):
    await storage.set_setting("system.cors_origins", "")
    monkeypatch.setenv("FRONTEND_URL", "https://obsolete.example")
    token = "fixture-metrics-token-" * 3
    monkeypatch.setenv("RELEASETRACKER_METRICS_TOKEN", token)
    monkeypatch.setenv("RELEASETRACKER_CORS_ORIGINS", "https://obsolete.example")
    monkeypatch.setenv("RELEASETRACKER_MUTATION_CONCURRENCY", "3")
    monkeypatch.setenv("RELEASETRACKER_PRE_MIGRATION_BACKUP", "0")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "192.0.2.1,192.0.2.2")
    monkeypatch.setenv("RELEASETRACKER_WORKER_POLL_SECONDS", "61")
    with pytest.raises(ValueError):
        await import_legacy_settings(await storage._get_connection())
    assert await storage.get_setting(IMPORT_MARKER) is None
    assert await storage.get_setting("system.mutation_concurrency") is None
    monkeypatch.delenv("RELEASETRACKER_WORKER_POLL_SECONDS")
    await import_legacy_settings(await storage._get_connection())
    from releasetracker.services.metrics_credential import METRICS_TOKEN_SETTING_KEY, encode_token

    assert await storage.get_setting(METRICS_TOKEN_SETTING_KEY) == encode_token(token)
    assert token not in str(await storage.get_all_settings_with_updated_at())
    assert await storage.get_setting("system.pre_migration_backup") == "false"
    assert await storage.get_setting("system.forwarded_allow_ips") == "192.0.2.1\n192.0.2.2"
    assert await storage.get_setting("system.cors_origins") == ""
    assert not await storage.get_system_base_url()
    await storage.set_setting("system.mutation_concurrency", "2")
    assert await import_legacy_settings(await storage._get_connection()) == 0
    assert await storage.get_setting("system.mutation_concurrency") == "2"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("worker_poll_seconds", "0"),
        ("mutation_concurrency", "4"),
        ("readiness_pull_grace_seconds", "3601"),
        ("runtime_health_interval_seconds", "299"),
        ("pre_migration_backup", "sometimes"),
        ("online_restore", "on"),
        ("cors_origins", "https://example.org/app"),
        ("cors_origins", "*"),
        ("cors_origins", "https://example.org:invalid"),
        ("forwarded_allow_ips", "invalid"),
    ],
)
def test_policy_validation_rejects_invalid_values(key, value):
    with pytest.raises(ValueError):
        normalize_policy("system." + key, value)


@pytest.mark.asyncio
async def test_policy_defaults_and_import_status_are_guarded(authed_client, storage):
    await storage.set_setting(IMPORT_MARKER, "true")
    response = authed_client.get("/api/settings")
    assert response.status_code == 200
    values = {item["key"]: item["value"] for item in response.json()}
    assert {key: values[key] for key in POLICY_DEFAULTS} == POLICY_DEFAULTS
    assert IMPORT_MARKER not in values
    assert (
        authed_client.post(
            "/api/settings", json={"key": IMPORT_MARKER, "value": "false"}
        ).status_code
        == 403
    )
    assert authed_client.delete(f"/api/settings/{IMPORT_MARKER}").status_code == 403


@pytest.mark.asyncio
async def test_removed_backup_settings_from_existing_instances(authed_client, storage):
    for key in ("system.backup_daily_retention", "system.backup_weekly_retention"):
        await storage.set_setting(key, "4")
        assert (
            authed_client.post("/api/settings", json={"key": key, "value": "2"}).status_code == 400
        )
    response = authed_client.get("/api/settings")
    assert response.status_code == 200
    assert {item["key"] for item in response.json()}.isdisjoint(
        {"system.backup_daily_retention", "system.backup_weekly_retention"}
    )
