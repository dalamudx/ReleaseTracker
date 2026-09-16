import json
import logging
from datetime import datetime, timezone

import pytest

from releasetracker.models import Release
from releasetracker.notifiers import WeComNotifier, build_notifier
from releasetracker.notifiers.wecom import _build_wecom_markdown
from releasetracker.notifiers.webhook import _build_webhook_payload
from releasetracker.services.outbound_http import OutboundResponse


def _release() -> Release:
    return Release(
        tracker_name="nginx_frontend",
        tracker_type="gitea",
        name="3.6.0-dev-build",
        tag_name="3.6.0-dev-build",
        version="3.6.0-dev-build",
        published_at=datetime(2026, 9, 16, 19, 36, 50, tzinfo=timezone.utc),
        url="https://git.example.com/acme/app/releases/tag/3.6.0-dev-build",
        prerelease=True,
        body="修复登录问题并更新容器制品。",
        channel_name="dev",
    )


def test_wecom_release_markdown_contains_version_details():
    generic = _build_webhook_payload("republish", _release(), language="zh")

    content = _build_wecom_markdown(generic)

    assert "nginx_frontend" in content
    assert "3.6.0-dev-build" in content
    assert "重新发布" in content
    assert "标签：** 3.6.0-dev-build" in content
    assert "渠道：** dev" in content
    assert "修复登录问题" in content
    assert "[查看详情](https://git.example.com/" in content


def test_wecom_executor_markdown_contains_run_details():
    generic = _build_webhook_payload(
        "executor_run_success",
        {
            "entity": "executor_run",
            "executor_name": "frontend",
            "tracker_name": "nginx_frontend",
            "runtime_type": "docker",
            "status": "success",
            "run_id": 42,
            "from_version": "3.6.0-dev-old",
            "to_version": "3.6.0-dev-build",
            "finished_at": "2026-09-16T19:36:50+08:00",
            "message": "Container updated",
        },
        language="zh",
    )

    content = _build_wecom_markdown(generic)

    assert "执行器运行成功" in content
    assert "frontend" in content
    assert "3.6.0-dev-old" in content
    assert "3.6.0-dev-build" in content
    assert "运行 ID：** 42" in content


@pytest.mark.asyncio
async def test_wecom_notifier_sends_markdown_and_accepts_errcode_zero(monkeypatch):
    requests = []

    class _FakeClient:
        async def request(self, method, url, **kwargs):
            requests.append((method, url, kwargs))
            return OutboundResponse(
                status_code=200,
                headers={},
                body=json.dumps({"errcode": 0, "errmsg": "ok"}).encode(),
            )

    monkeypatch.setattr("releasetracker.notifiers.webhook.OutboundHTTPClient", _FakeClient)
    notifier = WeComNotifier(
        "wecom",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=redacted",
        events=["republish"],
        language="zh",
    )

    delivered = await notifier.notify("republish", _release())

    assert delivered is True
    body = requests[0][2]["json_body"]
    assert body["msgtype"] == "markdown"
    assert body["markdown"]["content"]
    assert len(body["markdown"]["content"].encode("utf-8")) <= 4096


@pytest.mark.asyncio
async def test_wecom_notifier_rejects_http_200_business_error(monkeypatch, caplog):
    sensitive_url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=must-not-appear"

    class _FakeClient:
        async def request(self, method, url, **kwargs):
            del method, url, kwargs
            return OutboundResponse(
                status_code=200,
                headers={},
                body=json.dumps({"errcode": 40008, "errmsg": "invalid message type"}).encode(),
            )

    monkeypatch.setattr("releasetracker.notifiers.webhook.OutboundHTTPClient", _FakeClient)
    caplog.set_level(logging.ERROR)

    delivered = await WeComNotifier(
        "wecom", sensitive_url, events=["republish"], language="zh"
    ).notify("republish", _release())

    assert delivered is False
    assert "errcode=40008" in caplog.text
    assert "invalid message type" in caplog.text
    assert "must-not-appear" not in caplog.text


def test_notifier_factory_builds_wecom_and_rejects_unknown_type():
    notifier = build_notifier(
        notifier_type="wecom",
        name="wecom",
        url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=redacted",
    )
    assert isinstance(notifier, WeComNotifier)

    with pytest.raises(ValueError, match="Unsupported notifier type"):
        build_notifier(
            notifier_type="unknown",
            name="unknown",
            url="https://hooks.example.com/events",
        )
