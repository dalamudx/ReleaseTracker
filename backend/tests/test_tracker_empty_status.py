"""An empty tracker is not a failed fetch, including immediately after save."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from releasetracker.models import TrackerStatus
from releasetracker.scheduler import ReleaseScheduler
from test_trackers import make_tracker_payload

pytestmark = pytest.mark.asyncio


async def test_create_then_save_empty_tracker_never_fetches_or_reports_failure(
    authed_client, storage
):
    payload = make_tracker_payload("new-service")
    scheduler = authed_client.app.state.scheduler
    real_scheduler = ReleaseScheduler(storage)
    scheduler.rebuild_tracker_views_from_storage.side_effect = (
        real_scheduler.rebuild_tracker_views_from_storage
    )
    scheduler.check_tracker_now_v2.side_effect = AssertionError("Save must not fetch remotely")
    created = authed_client.post("/api/trackers", json=payload)
    assert created.status_code == 200, created.text
    assert created.json()["status"]["error"] is None
    assert created.json()["status"]["last_check"] is None
    for _ in range(2):
        saved = authed_client.put("/api/trackers/new-service", json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json()["status"]["error"] is None
        assert saved.json()["status"]["last_check"] is None
        assert saved.json()["status"]["last_version"] is None
    scheduler.check_tracker_now_v2.assert_not_called()


@pytest.mark.parametrize(
    "previous_error,expected",
    [
        ("No version information found", None),
        ("registry_repository_not_found", "registry_repository_not_found"),
        ("upstream_timeout", "upstream_timeout"),
        ("security_validation_failed", "security_validation_failed"),
    ],
)
async def test_saving_empty_tracker_preserves_only_real_fetch_errors(
    authed_client, storage, previous_error, expected
):
    payload = make_tracker_payload("empty-service")
    assert authed_client.post("/api/trackers", json=payload).status_code == 200
    checked = datetime(2026, 1, 1, 12)
    await storage.update_tracker_status(
        TrackerStatus(
            name="empty-service",
            type="github",
            enabled=True,
            last_check=checked,
            error=previous_error,
        )
    )
    authed_client.app.state.scheduler.rebuild_tracker_views_from_storage.side_effect = (
        ReleaseScheduler(storage).rebuild_tracker_views_from_storage
    )
    saved = authed_client.put("/api/trackers/empty-service", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"]["error"] == expected
    status = await storage.get_tracker_status("empty-service")
    assert status.last_check == checked
    assert status.last_version is None


@pytest.mark.parametrize("method", ["check_tracker_now_v2", "_check_tracker"])
@pytest.mark.parametrize("error", [None, "registry_repository_not_found"])
async def test_empty_live_check_keeps_source_error_without_inventing_one(
    authed_client, storage, monkeypatch, method, error
):
    payload = make_tracker_payload("checked-service")
    assert authed_client.post("/api/trackers", json=payload).status_code == 200
    scheduler = ReleaseScheduler(storage)
    monkeypatch.setattr(
        scheduler,
        "_process_aggregate_tracker_check",
        AsyncMock(return_value={"releases": [], "latest_version": None, "error": error}),
    )
    status = await getattr(scheduler, method)("checked-service")
    assert status.last_check is not None
    assert status.last_version is None
    assert status.error == error
    assert (await storage.get_tracker_status("checked-service")).error == error
