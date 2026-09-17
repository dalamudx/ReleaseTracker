from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from releasetracker.models import AggregateTracker, TrackerSource
from releasetracker.services.repository_webhooks import RepositoryWebhookInput


async def make_hook(storage, *, branches: list[str] | None = None):
    tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="api-webhook-app",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_config={"repo": "acme/api-webhook-app", "fetch_mode": "rest_first"},
                )
            ],
        )
    )
    saved = await storage.webhooks.save(
        RepositoryWebhookInput(
            tracker_source_id=tracker.sources[0].id,
            provider="github",
            enabled=True,
            secret="0123456789abcdef",
            branches=branches or [],
        )
    )
    return await storage.webhooks.get(saved["id"])


def signed_release(repository: str = "acme/api-webhook-app"):
    body = json.dumps(
        {
            "action": "published",
            "repository": {"html_url": f"https://github.com/{repository}"},
            "release": {"tag_name": "v1.0.0", "draft": False},
        },
        separators=(",", ":"),
    ).encode()
    signature = "sha256=" + hmac.new(b"0123456789abcdef", body, hashlib.sha256).hexdigest()
    return body, signature


@pytest.mark.asyncio
async def test_public_receiver_accepts_once_and_deduplicates_delivery(client, storage):
    hook = await make_hook(storage, branches=["release-only-does-not-apply"])
    body, signature = signed_release()
    headers = {
        "content-type": "application/json",
        "x-github-event": "release",
        "x-github-delivery": "delivery-1",
        "x-hub-signature-256": signature,
    }
    path = f"/api/webhooks/repository/{hook['id']}"
    accepted = client.post(path, content=body, headers=headers)
    assert accepted.status_code == 202
    assert accepted.json()["state"] == "queued"
    duplicate = client.post(path, content=body, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["state"] == "duplicate"
    deliveries = await storage.webhooks.deliveries(hook["id"])
    assert len(deliveries) == 1
    assert deliveries[0]["state"] == "pending"


@pytest.mark.asyncio
async def test_public_receiver_bounds_payload_and_ignores_unselected_events(client, storage):
    hook = await make_hook(storage)
    path = f"/api/webhooks/repository/{hook['id']}"
    oversized = client.post(path, content=b"x" * (1024 * 1024 + 1))
    assert oversized.status_code == 413

    payload = {
        "action": "completed",
        "repository": {"html_url": "https://github.com/acme/api-webhook-app"},
        "workflow_run": {
            "id": 9,
            "status": "completed",
            "conclusion": "failure",
            "head_branch": "main",
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(b"0123456789abcdef", body, hashlib.sha256).hexdigest()
    ignored = client.post(
        path,
        content=body,
        headers={
            "content-type": "application/json",
            "x-github-event": "workflow_run",
            "x-hub-signature-256": signature,
        },
    )
    assert ignored.status_code == 200
    assert ignored.json()["state"] == "ignored"
    deliveries = await storage.webhooks.deliveries(hook["id"])
    assert deliveries[0]["requests"] == []


@pytest.mark.asyncio
async def test_public_receiver_rejects_bad_signature_and_wrong_repository(client, storage):
    hook = await make_hook(storage)
    path = f"/api/webhooks/repository/{hook['id']}"
    body, _signature = signed_release()
    invalid = client.post(
        path,
        content=body,
        headers={
            "content-type": "application/json",
            "x-github-event": "release",
            "x-hub-signature-256": "sha256=bad",
        },
    )
    assert invalid.status_code == 401

    wrong_body, wrong_signature = signed_release("someone/else")
    wrong = client.post(
        path,
        content=wrong_body,
        headers={
            "content-type": "application/json",
            "x-github-event": "release",
            "x-hub-signature-256": wrong_signature,
        },
    )
    assert wrong.status_code == 403
    assert await storage.webhooks.deliveries(hook["id"]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["forgejo", "gitea"])
@pytest.mark.parametrize(
    "trigger,extra,reason",
    [
        ({"ref": "refs/heads/dev"}, {}, ""),
        ({"ref": "dev", "ref_type": "branch"}, {}, ""),
        ({"ref": "refs/heads/main"}, {}, "branches_mismatch"),
        ({"ref": "refs/tags/dev"}, {}, "branches_unavailable"),
        ({"ref": "refs/heads/dev"}, {"event": "pull_request"}, "branches_unavailable"),
        ({}, {}, "branches_unavailable"),
        ({"ref": "refs/heads/dev"}, {"workflow_id": "ci.yml"}, "workflows_mismatch"),
        ({"ref": "refs/heads/dev"}, {"workflow_id": ""}, "workflows_unavailable"),
    ],
)
async def test_signed_action_run_filters_then_queues_repository_and_container(
    client, storage, provider, trigger, extra, reason
):
    from test_repository_webhook_branches import action_payload
    from releasetracker.webhook_scheduler import _container_priority_aliases

    tracker = await storage.create_aggregate_tracker(
        AggregateTracker(
            name="forge-branch",
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="gitea",
                    source_config={"repo": "acme/app", "instance": "https://code.example"},
                ),
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_config={"image": "acme/app", "registry": "registry.example"},
                ),
            ],
        )
    )
    repo_id, image_id = [s.id for s in tracker.sources]
    hook = await storage.webhooks.save(
        RepositoryWebhookInput(
            tracker_source_id=repo_id,
            provider=provider,
            enabled=True,
            secret="0123456789abcdef",
            linked_source_ids=[image_id],
            branches=["dev"],
            workflows=["Release.yml"],
        )
    )
    payload = action_payload({"event": "push", "event_payload": json.dumps(trigger), **extra})
    body = json.dumps(payload).encode()
    headers = {
        "content-type": "application/json",
        f"x-{provider}-event": "action_run_success",
        f"x-{provider}-delivery": "branch-regression-1738",
        f"x-{provider}-signature": hmac.new(b"0123456789abcdef", body, hashlib.sha256).hexdigest(),
    }
    path = f"/api/webhooks/repository/{hook['id']}"
    bad_headers = {**headers, f"x-{provider}-signature": "invalid"}
    assert client.post(path, content=body, headers=bad_headers).status_code == 401
    assert await storage.webhooks.deliveries(hook["id"]) == []
    response = client.post(path, content=body, headers=headers)
    assert response.status_code == (200 if reason else 202)
    (delivery,) = await storage.webhooks.deliveries(hook["id"])
    assert delivery["reason"] == reason
    if reason:
        assert delivery["state"] == "ignored"
        assert delivery["requests"] == []
    else:
        assert {request["tracker_source_id"] for request in delivery["requests"]} == {
            repo_id,
            image_id,
        }
        assert delivery["summary"]["branch"] == "dev"
        assert _container_priority_aliases([{"summary": json.dumps(delivery["summary"])}]) == (
            "dev",
        )
        duplicate = client.post(path, content=body, headers=headers)
        assert duplicate.status_code == 200 and duplicate.json()["state"] == "duplicate"
        (delivery,) = await storage.webhooks.deliveries(hook["id"])
        assert len(delivery["requests"]) == 2
