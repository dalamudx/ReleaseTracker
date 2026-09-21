import json
from unittest.mock import AsyncMock

import pytest

from releasetracker.notifiers.templates import builtin, sample_payload
from releasetracker.notifiers.template_store import save_template
from releasetracker.notifiers.webhook import WebhookNotifier
from releasetracker.services.executor_notification_outbox import ExecutorNotificationOutbox

pytestmark = pytest.mark.asyncio


async def test_template_snapshot_survives_edit_language_change_and_restart(storage, monkeypatch):
    template = await save_template(
        storage, builtin() | {"name": "Example template", "body": "Original {{ locale }}"}
    )
    item = await storage.create_notifier(
        {
            "name": "Example channel",
            "url": "https://example.test/hook",
            "events": ["executor_run_success"],
            "language": "zh",
            "template_id": template["id"],
        }
    )
    box = ExecutorNotificationOutbox(storage)
    await box.initialize()
    await box.enqueue(sample_payload("executor_run_success") | {"run_id": 42})
    await box.shutdown()
    await save_template(storage, template | {"body": "Changed {{ locale }}"}, template["id"])
    await storage.update_notifier(item.id, {"language": "en"})
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(WebhookNotifier, "send_payload", sender)
    box = ExecutorNotificationOutbox(storage)
    await box.initialize()
    try:
        assert await box.deliver_one()
        text = sender.call_args.args[0]["content"]
        assert "Original zh" in text and "Changed" not in text
        assert sender.call_args.args[0]["template"]["revision"] == 1
        assert sender.call_args.args[0]["event"] == "executor_run_success"
        assert not await box.deliver_one()
    finally:
        await box.shutdown()


async def test_structured_service_changes_keep_health_evidence_and_strip_secrets():
    from releasetracker.notifiers.templates import context_for

    payload = sample_payload("executor_run_success") | {
        "services": [
            {
                "service": "service-1",
                "from_version": "registry.example.test/team/service-a:1.2.0",
                "to_version": "registry.example.test/team/service-a:1.3.0",
                "status": "success",
                "message": "secret=fixture-secret",
            },
        ]
    }
    ctx = context_for("executor_run_success", payload, "en")
    row = ctx["services"][0]
    assert row["from_display"].endswith(":1.2.0") and row["to_display"].endswith(":1.3.0")
    assert row["check_label"] == "Verification passed"
    assert "fixture-secret" not in json.dumps(ctx)


async def test_long_urls_cannot_break_provider_budget():
    from releasetracker.notifiers.templates import render_notification

    payload = sample_payload("new_release")
    payload.url = "https://example.test/" + "a" * 10000
    result = await render_notification("new_release", payload, "zh", channel="wecom")
    assert len(result["content"].encode()) <= 4096
    assert "https://example.test/" not in result["content"]
