from datetime import datetime, timezone
import pytest

from helpers.executor_runtime import (
    create_runtime_connection,
    save_docker_tracker_config,
)
from releasetracker.config import Channel, ExecutorConfig
from releasetracker.models import ExecutorSnapshot, Release
from releasetracker.storage.sqlite import (
    DEFAULT_EXECUTOR_SNAPSHOT_RETENTION_COUNT,
    DEFAULT_RELEASE_HISTORY_RETENTION_COUNT,
    DEFAULT_SYSTEM_BASE_URL,
    DEFAULT_SYSTEM_LOG_LEVEL,
    DEFAULT_SYSTEM_TIMEZONE,
    SYSTEM_BASE_URL_SETTING_KEY,
    SYSTEM_EXECUTOR_SNAPSHOT_RETENTION_COUNT_SETTING_KEY,
    SYSTEM_LOG_LEVEL_SETTING_KEY,
    SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY,
    SYSTEM_TIMEZONE_SETTING_KEY,
)


async def _create_executor_for_cleanup(storage, *, name: str) -> int:
    runtime_id = await create_runtime_connection(storage, name=f"{name}-runtime")
    await save_docker_tracker_config(
        storage,
        name=f"{name}-tracker",
        image=name,
        channels=[Channel(name="stable", enabled=True, type="release")],
    )
    aggregate_tracker = await storage.get_aggregate_tracker(f"{name}-tracker")
    tracker_source_id = aggregate_tracker.sources[0].id
    return await storage.save_executor_config(
        ExecutorConfig(
            name=name,
            runtime_type="docker",
            runtime_connection_id=runtime_id,
            tracker_name=f"{name}-tracker",
            tracker_source_id=tracker_source_id,
            enabled=True,
            update_mode="manual",
            target_ref={"mode": "container", "container_id": f"{name}-container"},
        )
    )


async def _seed_snapshots_for_cleanup(storage, executor_id: int, count: int) -> list[int]:
    ids: list[int] = []
    for index in range(count):
        ids.append(
            await storage.create_executor_snapshot(
                ExecutorSnapshot(
                    executor_id=executor_id,
                    snapshot_data={"image": f"sample-web:1.{index}.0"},
                    trigger="pre_update",
                    image_at_capture=f"sample-web:1.{index}.0",
                    created_at=datetime(2026, 4, 1, 0, index, 0),
                    updated_at=datetime(2026, 4, 1, 0, index, 0),
                )
            )
        )
    return ids


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
async def test_executor_snapshot_retention_setting_accepts_valid_integer(authed_client):
    response = authed_client.post(
        "/api/settings",
        json={"key": SYSTEM_EXECUTOR_SNAPSHOT_RETENTION_COUNT_SETTING_KEY, "value": "12"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["value"] == "12"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["abc", "0", "-1", "1001", "1.5"])
async def test_executor_snapshot_retention_setting_rejects_invalid_values(
    authed_client, value
):
    response = authed_client.post(
        "/api/settings",
        json={"key": SYSTEM_EXECUTOR_SNAPSHOT_RETENTION_COUNT_SETTING_KEY, "value": value},
    )

    assert response.status_code == 400, response.text


@pytest.mark.asyncio
async def test_executor_snapshot_retention_storage_falls_back_to_default(storage):
    assert (
        await storage.get_executor_snapshot_retention_count()
        == DEFAULT_EXECUTOR_SNAPSHOT_RETENTION_COUNT
    )

    await storage.set_setting(SYSTEM_EXECUTOR_SNAPSHOT_RETENTION_COUNT_SETTING_KEY, "invalid")

    assert (
        await storage.get_executor_snapshot_retention_count()
        == DEFAULT_EXECUTOR_SNAPSHOT_RETENTION_COUNT
    )


@pytest.mark.asyncio
async def test_cleanup_release_history_endpoint_uses_saved_retention(
    authed_client, storage
):
    await save_docker_tracker_config(
        storage,
        name="settings-clean-release",
        image="settings-clean-release",
        channels=[Channel(name="stable", enabled=True, type="release")],
    )
    aggregate_tracker = await storage.get_aggregate_tracker("settings-clean-release")
    runtime_source = storage._select_runtime_source(aggregate_tracker)
    releases = [
        Release(
            tracker_name="settings-clean-release",
            tracker_type="container",
            name=f"{version}.0.0",
            tag_name=f"{version}.0.0",
            version=f"{version}.0.0",
            published_at=datetime(2026, 5, version, tzinfo=timezone.utc),
            url=f"https://example.com/{version}.0.0",
            prerelease=False,
        )
        for version in range(1, 4)
    ]
    for release in releases:
        await storage.save_source_observations(
            aggregate_tracker.id,
            runtime_source,
            [release],
            observed_at=release.published_at,
        )
        identity_key = storage.release_identity_key_for_source(
            release,
            source_type=runtime_source.source_type,
        )
        source_history_id = await storage.get_source_release_history_id(
            runtime_source.id,
            identity_key,
        )
        await storage.upsert_tracker_release_history(
            aggregate_tracker.id,
            release,
            primary_source_release_history_id=source_history_id,
            source_type=runtime_source.source_type,
        )
    await storage.refresh_tracker_current_releases(aggregate_tracker.id, [releases[-1]])
    await storage.set_setting(SYSTEM_RELEASE_HISTORY_RETENTION_COUNT_SETTING_KEY, "1")

    response = authed_client.post("/api/settings/actions/cleanup-release-history")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"] == "release_history_cleanup"
    assert body["retention_count"] == 1
    assert body["trackers_scanned"] >= 1
    assert body["tracker_release_history_deleted"] == 2
    remaining = await storage.get_tracker_release_history_releases(aggregate_tracker.id)
    assert [release.version for release in remaining] == ["3.0.0"]


@pytest.mark.asyncio
async def test_cleanup_snapshot_history_endpoint_uses_saved_retention(
    authed_client, storage
):
    executor_id = await _create_executor_for_cleanup(storage, name="settings-clean-snapshot")
    ids = await _seed_snapshots_for_cleanup(storage, executor_id, count=4)
    await storage.set_setting(SYSTEM_EXECUTOR_SNAPSHOT_RETENTION_COUNT_SETTING_KEY, "2")

    response = authed_client.post("/api/settings/actions/cleanup-snapshot-history")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "action": "snapshot_history_cleanup",
        "retention_count": 2,
        "executors_scanned": 1,
        "executors_pruned": 1,
        "snapshots_deleted": 2,
    }
    remaining = await storage.list_executor_snapshots(executor_id, limit=10, offset=0)
    assert {snapshot.id for snapshot in remaining} == set(ids[2:])


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
