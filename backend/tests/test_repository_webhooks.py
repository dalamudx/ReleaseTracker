from __future__ import annotations

import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from releasetracker.models import AggregateTracker, TrackerSource
from releasetracker.scheduler import ReleaseScheduler
from releasetracker.trackers.base import BaseTracker
from releasetracker.routers.webhooks import SlidingWindowRateLimiter
from releasetracker.services.repository_webhooks import (
    RepositoryWebhookInput,
    event_reason,
    normalize_event,
    repository_matches,
    verify_signature,
)
from releasetracker.webhook_scheduler import RepositoryWebhookScheduler


class EmptyTracker(BaseTracker):
    async def fetch_latest(self, fallback_tags: bool = False):
        return None

    async def fetch_all(self, limit: int = 10, fallback_tags: bool = False):
        return []


async def make_tracker(storage, name="webhook-app"):
    return await storage.create_aggregate_tracker(
        AggregateTracker(
            name=name,
            primary_changelog_source_key="repo",
            sources=[
                TrackerSource(
                    source_key="repo",
                    source_type="github",
                    source_config={"repo": "acme/app", "fetch_mode": "rest_first"},
                ),
                TrackerSource(
                    source_key="image",
                    source_type="container",
                    source_config={"image": "acme/app", "registry": "registry.example.com"},
                ),
            ],
        )
    )


def github_release():
    return {
        "action": "published",
        "repository": {"html_url": "https://github.com/acme/app"},
        "release": {"tag_name": "v1.2.3", "draft": False, "prerelease": True},
    }


def github_workflow():
    return {
        "action": "completed",
        "repository": {"html_url": "https://github.com/acme/app"},
        "workflow": {"path": ".github/workflows/publish.yml"},
        "workflow_run": {
            "id": 42,
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
        },
    }


def signed_body(payload, secret="0123456789abcdef"):
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, signature


def test_event_contracts_and_fail_closed_filters():
    release = normalize_event("github", {"x-github-event": "release"}, github_release())
    assert release.kind == "release"
    assert release.ref == "v1.2.3"
    assert event_reason(release, {"release_published": True}) == ""

    workflow = normalize_event("github", {"x-github-event": "workflow_run"}, github_workflow())
    config = {
        "release_published": True,
        "workflow_success": True,
        "branches": ["release/*"],
        "workflows": [".github/workflows/publish.yml"],
    }
    assert event_reason(workflow, config) == "branches_mismatch"
    workflow.branch = ""
    assert event_reason(workflow, config) == "branches_unavailable"

    failed = github_workflow()
    failed["workflow_run"]["conclusion"] = "failure"
    assert normalize_event("github", {"x-github-event": "workflow_run"}, failed).kind == "ignored"

    gitlab_pipeline = normalize_event(
        "gitlab",
        {"x-gitlab-event": "Pipeline Hook"},
        {
            "project": {"web_url": "https://gitlab.com/acme/app"},
            "object_attributes": {"id": 7, "status": "success", "ref": "main", "name": "publish"},
        },
    )
    assert (gitlab_pipeline.kind, gitlab_pipeline.branch, gitlab_pipeline.workflow) == (
        "workflow",
        "main",
        "publish",
    )
    action_payload = {
        "action": "success",
        "run": {
            "id": 8,
            "status": "success",
            "workflow_id": "publish.yml",
            "ref": "refs/heads/main",
            "repository": {"html_url": "https://codeberg.org/acme/app"},
        },
    }
    for provider in ("forgejo", "gitea"):
        for action in ("success", "completed"):
            action_payload["action"] = action
            action_run = normalize_event(
                provider, {f"x-{provider}-event": "action_run"}, action_payload
            )
            assert (action_run.kind, action_run.branch, action_run.workflow) == (
                "workflow",
                "main",
                "publish.yml",
            )


def test_receiver_rate_limiter_bounds_invalid_and_valid_requests():
    limiter = SlidingWindowRateLimiter(limit=2, window=10)
    assert limiter.allow("hook", now=100)
    assert limiter.allow("hook", now=101)
    assert not limiter.allow("hook", now=102)
    assert limiter.allow("hook", now=111)


def test_signatures_and_repository_identity_are_strict():
    body, signature = signed_body(github_release())
    verify_signature("github", "hmac", "0123456789abcdef", {"x-hub-signature-256": signature}, body)
    with pytest.raises(ValueError, match="Invalid signature"):
        verify_signature("github", "hmac", "wrong-secret-value", {}, body)
    verify_signature(
        "gitlab", "gitlab_token", "0123456789abcdef", {"x-gitlab-token": "0123456789abcdef"}, body
    )
    raw_key = b"0123456789abcdef0123456789abcdef"
    signing_token = "whsec_" + base64.b64encode(raw_key).decode()
    message = b"delivery.1000." + body
    signed = "v1," + base64.b64encode(hmac.digest(raw_key, message, "sha256")).decode()
    signing_headers = {
        "webhook-id": "delivery",
        "webhook-timestamp": "1000",
        "webhook-signature": signed,
    }
    verify_signature("gitlab", "gitlab_signing", signing_token, signing_headers, body, now=1001)
    with pytest.raises(ValueError, match="Invalid signature"):
        verify_signature("gitlab", "gitlab_signing", signing_token, signing_headers, body, now=1400)
    source = TrackerSource(
        source_key="repo", source_type="github", source_config={"repo": "acme/app"}
    )
    assert repository_matches(source, "github", {"html_url": "https://github.com/acme/app"})
    assert not repository_matches(source, "github", {"html_url": "https://evil.test/acme/app"})
    gitlab = TrackerSource(
        source_key="repo", source_type="gitlab", source_config={"project": "acme/app"}
    )
    assert repository_matches(
        gitlab, "gitlab", {"web_url": "https://gitlab.com/acme/app", "id": 42}
    )
    gitea = TrackerSource(
        source_key="repo", source_type="gitea", source_config={"repo": "acme/app"}
    )
    assert repository_matches(gitea, "gitea", {"html_url": "https://gitea.com/acme/app"})


@pytest.mark.asyncio
async def test_repository_webhook_api_encrypts_deduplicates_and_scopes_sources(
    authed_client, storage
):
    tracker = await make_tracker(storage)
    repo, image = tracker.sources
    response = authed_client.post(
        "/api/webhooks/repositories",
        json={
            "tracker_source_id": repo.id,
            "provider": "github",
            "enabled": True,
            "auth_mode": "hmac",
            "secret": "0123456789abcdef",
            "release_published": True,
            "workflow_success": True,
            "linked_source_ids": [image.id],
            "branches": ["main"],
            "workflows": [".github/workflows/publish.yml"],
        },
    )
    assert response.status_code == 201, response.text
    hook = response.json()
    assert "secret" not in hook
    db = await storage._get_connection()
    encrypted = (
        await (
            await db.execute("SELECT secret FROM repository_webhooks WHERE id=?", (hook["id"],))
        ).fetchone()
    )[0]
    assert encrypted != "0123456789abcdef"
    assert storage._decrypt(encrypted) == "0123456789abcdef"

    body, signature = signed_body(github_release())
    headers = {
        "content-type": "application/json",
        "x-github-event": "release",
        "x-github-delivery": "delivery-one",
        "x-hub-signature-256": signature,
    }
    endpoint = f"/api/webhooks/repository/{hook['id']}"
    accepted = authed_client.post(endpoint, content=body, headers=headers)
    assert accepted.status_code == 202, accepted.text
    duplicate = authed_client.post(endpoint, content=body, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["state"] == "duplicate"

    bad = authed_client.post(
        endpoint, content=body, headers=headers | {"x-hub-signature-256": "bad"}
    )
    assert bad.status_code == 401
    deliveries = await storage.webhooks.deliveries(hook["id"])
    assert len(deliveries) == 1
    assert deliveries[0]["duplicates"] == 1
    assert [r["tracker_source_id"] for r in deliveries[0]["requests"]] == [repo.id]

    workflow_body, workflow_signature = signed_body(github_workflow())
    workflow_response = authed_client.post(
        endpoint,
        content=workflow_body,
        headers={
            "content-type": "application/json",
            "x-github-event": "workflow_run",
            "x-github-delivery": "delivery-two",
            "x-hub-signature-256": workflow_signature,
        },
    )
    assert workflow_response.status_code == 202, workflow_response.text
    deliveries = await storage.webhooks.deliveries(hook["id"])
    assert {r["tracker_source_id"] for r in deliveries[0]["requests"]} == {repo.id, image.id}


@pytest.mark.asyncio
async def test_repository_webhook_secret_participates_in_key_rotation(storage):
    tracker = await make_tracker(storage, "rotation-app")
    hook = await storage.webhooks.save(
        RepositoryWebhookInput(
            tracker_source_id=tracker.sources[0].id,
            provider="github",
            enabled=True,
            secret="0123456789abcdef",
        )
    )
    new_key = Fernet.generate_key().decode()
    stats = await storage.rotate_encrypted_data(new_key)
    db = await storage._get_connection()
    encrypted = (
        await (
            await db.execute("SELECT secret FROM repository_webhooks WHERE id=?", (hook["id"],))
        ).fetchone()
    )[0]
    assert Fernet(new_key.encode()).decrypt(encrypted.encode()).decode() == "0123456789abcdef"
    assert stats["rotated"]["repository_webhook_secret"] == 1


@pytest.mark.asyncio
async def test_aggregate_webhook_check_fetches_only_selected_sources(storage, monkeypatch):
    tracker = await make_tracker(storage, "partial-source-app")
    repo, image = tracker.sources
    scheduler = ReleaseScheduler(storage)
    created_configs = []

    async def create_tracker(config):
        created_configs.append(config)
        return EmptyTracker(config.name, channels=config.channels)

    monkeypatch.setattr(scheduler, "_create_tracker", create_tracker)
    result = await scheduler._process_aggregate_tracker_check(
        tracker.name,
        tracker,
        None,
        trigger_mode="webhook",
        source_ids={repo.id},
    )
    assert len(created_configs) == 1
    assert created_configs[0].type == "github"
    assert set(result["source_fetch_run_ids"]) == {repo.id}
    db = await storage._get_connection()
    runs = await (
        await db.execute("SELECT tracker_source_id,trigger_mode FROM source_fetch_runs ORDER BY id")
    ).fetchall()
    assert [(row["tracker_source_id"], row["trigger_mode"]) for row in runs] == [
        (repo.id, "webhook")
    ]
    assert image.id not in result["source_fetch_run_ids"]


@pytest.mark.asyncio
async def test_webhook_worker_refreshes_only_claimed_sources_and_links_fetch_runs(storage):
    tracker = await make_tracker(storage, "worker-app")
    repo, image = tracker.sources
    hook = await storage.webhooks.save(
        RepositoryWebhookInput(
            tracker_source_id=repo.id,
            provider="github",
            enabled=True,
            secret="0123456789abcdef",
            linked_source_ids=[image.id],
        )
    )
    event = normalize_event("github", {"x-github-event": "workflow_run"}, github_workflow())
    await storage.webhooks.receive(hook, "id:worker", "hash", event, "", now=1000)
    requests = await storage.webhooks.claim(now=1006)
    scheduler = AsyncMock()
    scheduler._manual_checks_in_progress = set()
    scheduler._process_aggregate_tracker_check.return_value = {
        "releases": [],
        "latest_version": None,
        "error": None,
        "source_fetch_run_ids": {repo.id: 101, image.id: 102},
    }
    worker = RepositoryWebhookScheduler(storage, scheduler, AsyncMock())
    await worker._process(requests)
    scheduler._process_aggregate_tracker_check.assert_awaited_once()
    kwargs = scheduler._process_aggregate_tracker_check.await_args.kwargs
    assert kwargs["trigger_mode"] == "webhook"
    assert kwargs["source_ids"] == {repo.id, image.id}
    deliveries = await storage.webhooks.deliveries(hook["id"])
    assert deliveries[0]["state"] == "no_change"
    assert {r["source_fetch_run_id"] for r in deliveries[0]["requests"]} == {101, 102}
