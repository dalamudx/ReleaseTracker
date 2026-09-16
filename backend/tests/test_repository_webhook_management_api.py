from __future__ import annotations

import pytest

from releasetracker.models import AggregateTracker, TrackerSource
from releasetracker.storage.sqlite import SYSTEM_BASE_URL_SETTING_KEY


async def make_source(storage):
    tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="management-hook-app",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="gitea",
                    source_config={"repo": "acme/app", "instance": "https://git.example.test"},
                )
            ],
        )
    )
    return tracker.sources[0]


@pytest.mark.asyncio
async def test_repository_webhook_management_crud_hides_secrets(authed_client, storage):
    source = await make_source(storage)
    await storage.set_setting(SYSTEM_BASE_URL_SETTING_KEY, "https://releases.example.test/tracker")
    payload = {
        "tracker_source_id": source.id,
        "provider": "forgejo",
        "enabled": True,
        "auth_mode": "hmac",
        "secret": "0123456789abcdef",
        "release_published": True,
        "workflow_success": True,
        "branches": ["main"],
        "workflows": ["publish.yml"],
        "linked_source_ids": [],
    }
    created = authed_client.post("/api/webhooks/repositories", json=payload)
    assert created.status_code == 201
    body = created.json()
    assert "secret" not in body
    assert body["secret_configured"] is True
    assert (
        body["endpoint_url"]
        == f"https://releases.example.test/tracker/api/webhooks/repository/{body['id']}"
    )

    listed = authed_client.get("/api/webhooks/repositories")
    assert listed.status_code == 200
    assert [hook["id"] for hook in listed.json()] == [body["id"]]
    assert "secret" not in listed.json()[0]

    payload["secret"] = None
    payload["workflow_success"] = False
    updated = authed_client.put(f"/api/webhooks/repositories/{body['id']}", json=payload)
    assert updated.status_code == 200
    assert updated.json()["workflow_success"] is False
    assert updated.json()["secret_configured"] is True

    deleted = authed_client.delete(f"/api/webhooks/repositories/{body['id']}")
    assert deleted.status_code == 204
    assert authed_client.get("/api/webhooks/repositories").json() == []


def test_repository_webhook_management_requires_authentication(client):
    response = client.get("/api/webhooks/repositories")
    assert response.status_code == 401
