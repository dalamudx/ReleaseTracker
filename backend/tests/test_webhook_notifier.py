import logging

import pytest

from releasetracker.notifiers.webhook import WebhookNotifier, _build_webhook_payload
from releasetracker.services.outbound_http import OutboundResponse, OutboundURLRejected


def test_executor_webhook_uses_version_labels_for_helm_release():
    payload = _build_webhook_payload(
        "executor_run_success",
        {
            "entity": "executor_run",
            "executor_name": "sample-ci",
            "tracker_name": "sample-ci-chart",
            "runtime_type": "kubernetes",
            "target_mode": "helm_release",
            "status": "success",
            "from_version": "5.9.17",
            "to_version": "5.9.18",
            "finished_at": "2026-05-03T17:10:00+08:00",
            "message": "Helm release upgraded",
        },
    )

    fields = payload["embeds"][0]["fields"]
    assert {field["name"] for field in fields} >= {"From Version", "To Version"}
    assert "From Image" not in {field["name"] for field in fields}
    assert "From Chart Version" not in {field["name"] for field in fields}
    assert payload["message"] == "[Executor:sample-ci]"
    assert payload["content"] == "[Executor:sample-ci]"
    assert payload["text"] == "[Executor:sample-ci]"
    assert payload["embeds"][0]["description"] == "Helm release upgraded"
    assert payload["embeds"][0]["timestamp"] == "2026-05-03T09:10:00Z"


def test_executor_webhook_uses_version_labels_for_container_targets():
    payload = _build_webhook_payload(
        "executor_run_success",
        {
            "entity": "executor_run",
            "executor_name": "api",
            "tracker_name": "api-image",
            "runtime_type": "docker",
            "target_mode": "container",
            "status": "success",
            "from_version": "api:1.0.0",
            "to_version": "api:1.0.1",
            "finished_at": "2026-05-03T09:10:00Z",
            "message": "Container updated",
        },
    )

    fields = payload["embeds"][0]["fields"]
    assert {field["name"] for field in fields} >= {"From Version", "To Version"}
    assert "From Image" not in {field["name"] for field in fields}
    assert payload["message"] == "[Executor:api]"
    assert payload["content"] == "[Executor:api]"
    assert payload["text"] == "[Executor:api]"
    assert payload["embeds"][0]["description"] == "Container updated"
    assert payload["embeds"][0]["timestamp"] == "2026-05-03T09:10:00Z"


def test_executor_webhook_uses_chinese_labels_when_language_is_zh():
    payload = _build_webhook_payload(
        "executor_run_success",
        {
            "entity": "executor_run",
            "executor_name": "sample-ci",
            "tracker_name": "sample-ci-chart",
            "runtime_type": "kubernetes",
            "status": "success",
            "from_version": "5.9.17",
            "to_version": "5.9.18",
            "run_id": 42,
            "finished_at": "2026-05-03T09:10:00Z",
            "message": "Helm release upgraded",
        },
        language="zh",
    )

    fields = payload["embeds"][0]["fields"]
    assert {field["name"] for field in fields} >= {
        "执行器",
        "追踪器",
        "运行时",
        "结果",
        "原版本",
        "目标版本",
        "运行 ID",
    }
    assert payload["message"] == "[Executor:sample-ci]"
    assert payload["embeds"][0]["title"] == "执行器运行成功"
    result_field = next(field for field in fields if field["name"] == "结果")
    assert result_field["value"] == "成功"
    assert payload["embeds"][0]["footer"]["text"] == "事件：executor_run_success"


def test_generic_webhook_payload_uses_chinese_message_when_language_is_zh():
    payload = _build_webhook_payload("test", {"ok": True}, language="zh")

    assert payload["message"] == "[test] 收到通知"
    assert payload["content"] == "[test] 收到通知"
    assert payload["text"] == "[test] 收到通知"


@pytest.mark.asyncio
async def test_webhook_delivery_uses_shared_outbound_client(monkeypatch):
    requests = []

    class _FakeClient:
        async def request(self, method, url, **kwargs):
            requests.append((method, url, kwargs))
            return OutboundResponse(status_code=204, headers={}, body=b"")

    monkeypatch.setattr(
        "releasetracker.notifiers.webhook.OutboundHTTPClient",
        _FakeClient,
    )
    notifier = WebhookNotifier("public-hook", "https://hooks.example/events")

    delivered = await notifier.notify("new_release", {"ok": True})

    assert delivered is True
    assert requests == [
        (
            "POST",
            "https://hooks.example/events",
            {
                "json_body": {
                    "event": "new_release",
                    "message": "[new_release] Notification received",
                    "content": "[new_release] Notification received",
                    "text": "[new_release] Notification received",
                    "data": {"ok": True},
                }
            },
        )
    ]


@pytest.mark.asyncio
async def test_blocked_webhook_does_not_log_sensitive_url(monkeypatch, caplog):
    sensitive_url = "https://hooks.example/events?token=must-not-appear"

    class _RejectingClient:
        async def request(self, method, url, **kwargs):
            del method, url, kwargs
            raise OutboundURLRejected("outbound destination resolves to a prohibited address")

    monkeypatch.setattr(
        "releasetracker.notifiers.webhook.OutboundHTTPClient",
        _RejectingClient,
    )
    caplog.set_level(logging.ERROR, logger="releasetracker.notifiers.webhook")

    delivered = await WebhookNotifier("blocked-hook", sensitive_url).send_payload({"ok": True})

    assert delivered is False
    assert sensitive_url not in caplog.text
    assert "must-not-appear" not in caplog.text
