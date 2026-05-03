import pytest

from releasetracker.storage.sqlite import (
    DEFAULT_RELEASE_HISTORY_RETENTION_COUNT,
    DEFAULT_SYSTEM_BASE_URL,
    DEFAULT_SYSTEM_LOG_LEVEL,
    DEFAULT_SYSTEM_TIMEZONE,
    SYSTEM_BASE_URL_SETTING_KEY,
    SYSTEM_LOG_LEVEL_SETTING_KEY,
    SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY,
    SYSTEM_TIMEZONE_SETTING_KEY,
)


@pytest.mark.asyncio
async def test_settings_crud_endpoints(authed_client):
    create_response = authed_client.post(
        "/api/settings",
        json={"key": "test.setting", "value": "enabled"},
    )

    assert create_response.status_code == 200, create_response.text
    assert create_response.json() == {"key": "test.setting", "value": "enabled", "updated_at": None}

    list_response = authed_client.get("/api/settings")

    assert list_response.status_code == 200, list_response.text
    settings = list_response.json()
    created_setting = next(item for item in settings if item["key"] == "test.setting")
    assert created_setting["value"] == "enabled"
    assert created_setting["updated_at"] is not None

    delete_response = authed_client.delete("/api/settings/test.setting")

    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json() == {"message": "Setting deleted"}

    after_delete_response = authed_client.get("/api/settings")

    assert after_delete_response.status_code == 200, after_delete_response.text
    assert all(item["key"] != "test.setting" for item in after_delete_response.json())


@pytest.mark.asyncio
async def test_release_history_retention_setting_accepts_valid_integer(authed_client):
    response = authed_client.post(
        "/api/settings",
        json={"key": SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY, "value": "25"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["value"] == "25"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["abc", "0", "-1", "1001", "1.5"])
async def test_release_history_retention_setting_rejects_invalid_values(authed_client, value):
    response = authed_client.post(
        "/api/settings",
        json={"key": SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY, "value": value},
    )

    assert response.status_code == 400, response.text


@pytest.mark.asyncio
async def test_release_history_retention_storage_falls_back_to_default(storage):
    assert (
        await storage.get_release_history_retention_count()
        == DEFAULT_RELEASE_HISTORY_RETENTION_COUNT
    )

    await storage.set_setting(SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY, "invalid")

    assert (
        await storage.get_release_history_retention_count()
        == DEFAULT_RELEASE_HISTORY_RETENTION_COUNT
    )


@pytest.mark.asyncio
async def test_timezone_setting_accepts_valid_iana_timezone(authed_client):
    response = authed_client.post(
        "/api/settings",
        json={"key": SYSTEM_TIMEZONE_SETTING_KEY, "value": "Asia/Shanghai"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["value"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_timezone_setting_rejects_invalid_timezone(authed_client):
    response = authed_client.post(
        "/api/settings",
        json={"key": SYSTEM_TIMEZONE_SETTING_KEY, "value": "Mars/Base"},
    )

    assert response.status_code == 400, response.text


@pytest.mark.asyncio
async def test_log_level_setting_accepts_and_normalizes_valid_level(authed_client):
    response = authed_client.post(
        "/api/settings",
        json={"key": SYSTEM_LOG_LEVEL_SETTING_KEY, "value": "debug"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["value"] == "DEBUG"


@pytest.mark.asyncio
async def test_log_level_setting_rejects_invalid_level(authed_client):
    response = authed_client.post(
        "/api/settings",
        json={"key": SYSTEM_LOG_LEVEL_SETTING_KEY, "value": "TRACE"},
    )

    assert response.status_code == 400, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://releases.example.com/", "https://releases.example.com"),
        ("https://example.com/releasetracker/", "https://example.com/releasetracker"),
        ("", ""),
    ],
)
async def test_base_url_setting_accepts_and_normalizes_valid_values(
    authed_client, value, expected
):
    response = authed_client.post(
        "/api/settings",
        json={"key": SYSTEM_BASE_URL_SETTING_KEY, "value": value},
    )

    assert response.status_code == 200, response.text
    assert response.json()["value"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        "example.com/releasetracker",
        "/releasetracker",
        "ftp://example.com/releasetracker",
        "https://example.com/releasetracker?x=1",
        "https://example.com/releasetracker#callback",
    ],
)
async def test_base_url_setting_rejects_invalid_values(authed_client, value):
    response = authed_client.post(
        "/api/settings",
        json={"key": SYSTEM_BASE_URL_SETTING_KEY, "value": value},
    )

    assert response.status_code == 400, response.text


@pytest.mark.asyncio
async def test_runtime_setting_helpers_fall_back_to_defaults(storage):
    assert await storage.get_system_timezone() == DEFAULT_SYSTEM_TIMEZONE
    assert await storage.get_system_log_level() == DEFAULT_SYSTEM_LOG_LEVEL
    assert await storage.get_system_base_url() == DEFAULT_SYSTEM_BASE_URL

    await storage.set_setting(SYSTEM_TIMEZONE_SETTING_KEY, "Mars/Base")
    await storage.set_setting(SYSTEM_LOG_LEVEL_SETTING_KEY, "TRACE")
    await storage.set_setting(SYSTEM_BASE_URL_SETTING_KEY, " https://example.com/releasetracker/ ")

    assert await storage.get_system_timezone() == DEFAULT_SYSTEM_TIMEZONE
    assert await storage.get_system_log_level() == DEFAULT_SYSTEM_LOG_LEVEL
    assert await storage.get_system_base_url() == "https://example.com/releasetracker"
