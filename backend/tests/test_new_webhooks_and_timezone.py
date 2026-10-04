from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from releasetracker.models import Release
from releasetracker.notifiers import (
    SUPPORTED_NOTIFIER_TYPES,
    DingTalkNotifier,
    DiscordNotifier,
    FeishuNotifier,
    SlackNotifier,
    TelegramNotifier,
    build_notifier,
)
from releasetracker.notifiers.templates import (
    format_localized_time,
    render_notification,
)
from releasetracker.services.outbound_http import OutboundResponse


def _sample_release() -> Release:
    return Release(
        tracker_name="demo-app",
        tracker_type="github",
        name="1.5.0",
        tag_name="v1.5.0",
        version="1.5.0",
        published_at=datetime(2026, 6, 15, 12, 30, 0, tzinfo=timezone.utc),
        url="https://github.com/example/demo-app/releases/tag/v1.5.0",
        prerelease=False,
        body="Feature update and bug fixes.",
        channel_name="stable",
    )


def test_supported_notifier_types_includes_popular_webhooks():
    assert {"feishu", "dingtalk", "discord", "slack", "telegram", "wecom", "webhook"}.issubset(
        SUPPORTED_NOTIFIER_TYPES
    )


def test_format_localized_time_with_various_timezones():
    utc_dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert format_localized_time(utc_dt, "UTC") == "2026-01-01 00:00:00"
    assert format_localized_time(utc_dt, "Asia/Shanghai") == "2026-01-01 08:00:00"
    assert format_localized_time(utc_dt, "America/New_York") == "2025-12-31 19:00:00"
    assert format_localized_time("2026-01-01T00:00:00Z", "Asia/Shanghai") == "2026-01-01 08:00:00"


@pytest.mark.asyncio
async def test_notification_template_renders_configured_system_timezone():
    release = _sample_release()
    # In UTC (12:30:00)
    utc_rendered = await render_notification("new_release", release, "zh", tz_name="UTC")
    assert "2026-06-15 12:30:00" in utc_rendered["body"]

    # In Asia/Shanghai (UTC+8 -> 20:30:00)
    sh_rendered = await render_notification("new_release", release, "zh", tz_name="Asia/Shanghai")
    assert "2026-06-15 20:30:00" in sh_rendered["body"]
    assert sh_rendered["timezone"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_feishu_notifier_sends_interactive_card():
    notifier = build_notifier(
        notifier_type="feishu",
        name="Feishu Bot",
        url="https://open.feishu.cn/open-apis/bot/v2/hook/test",
        events=["new_release"],
        language="zh",
        timezone="Asia/Shanghai",
    )
    assert isinstance(notifier, FeishuNotifier)
    notifier.send_payload = AsyncMock(return_value=True)

    success = await notifier.notify("new_release", _sample_release())
    assert success is True
    sent = notifier.send_payload.call_args.args[0]
    assert sent["msg_type"] == "interactive"
    card = sent["card"]
    assert card["header"]["template"] == "green"
    assert any("2026-06-15 20:30:00" in elem.get("content", "") for elem in card["elements"])

    # Test response validation
    accepted, error = notifier._validate_success_response(
        OutboundResponse(status_code=200, headers={}, body=b'{"code":0,"msg":"success"}')
    )
    assert accepted is True and error is None

    rejected, error = notifier._validate_success_response(
        OutboundResponse(status_code=200, headers={}, body=b'{"code":19001,"msg":"invalid param"}')
    )
    assert rejected is False and "code=19001" in error


@pytest.mark.asyncio
async def test_dingtalk_notifier_sends_markdown_payload():
    notifier = build_notifier(
        notifier_type="dingtalk",
        name="DingTalk Bot",
        url="https://oapi.dingtalk.com/robot/send?access_token=test",
        events=["new_release"],
        language="zh",
        timezone="Asia/Shanghai",
    )
    assert isinstance(notifier, DingTalkNotifier)
    notifier.send_payload = AsyncMock(return_value=True)

    success = await notifier.notify("new_release", _sample_release())
    assert success is True
    sent = notifier.send_payload.call_args.args[0]
    assert sent["msgtype"] == "markdown"
    assert "2026-06-15 20:30:00" in sent["markdown"]["text"]

    # Test response validation
    accepted, error = notifier._validate_success_response(
        OutboundResponse(status_code=200, headers={}, body=b'{"errcode":0,"errmsg":"ok"}')
    )
    assert accepted is True and error is None

    rejected, error = notifier._validate_success_response(
        OutboundResponse(
            status_code=200, headers={}, body=b'{"errcode":300001,"errmsg":"token invalid"}'
        )
    )
    assert rejected is False and "errcode=300001" in error


@pytest.mark.asyncio
async def test_discord_notifier_sends_embeds():
    notifier = build_notifier(
        notifier_type="discord",
        name="Discord Bot",
        url="https://discord.com/api/webhooks/test",
        events=["new_release"],
        language="en",
        timezone="America/New_York",
    )
    assert isinstance(notifier, DiscordNotifier)
    notifier.send_payload = AsyncMock(return_value=True)

    success = await notifier.notify("new_release", _sample_release())
    assert success is True
    sent = notifier.send_payload.call_args.args[0]
    assert "embeds" in sent
    assert len(sent["embeds"]) == 1
    assert "2026-06-15 08:30:00" in sent["embeds"][0]["description"]

    # Test response validation (Discord returns 200 or 204)
    accepted, _ = notifier._validate_success_response(
        OutboundResponse(status_code=204, headers={}, body=b"")
    )
    assert accepted is True


@pytest.mark.asyncio
async def test_slack_notifier_sends_text():
    notifier = build_notifier(
        notifier_type="slack",
        name="Slack Bot",
        url="https://hooks.slack.com/services/test",
        events=["new_release"],
        language="zh",
        timezone="Asia/Shanghai",
    )
    assert isinstance(notifier, SlackNotifier)
    notifier.send_payload = AsyncMock(return_value=True)

    success = await notifier.notify("new_release", _sample_release())
    assert success is True
    sent = notifier.send_payload.call_args.args[0]
    assert "text" in sent
    assert "2026-06-15 20:30:00" in sent["text"]

    # Test response validation
    accepted, _ = notifier._validate_success_response(
        OutboundResponse(status_code=200, headers={}, body=b"ok")
    )
    assert accepted is True


@pytest.mark.asyncio
async def test_telegram_notifier_sends_markdown_with_chat_id():
    notifier = build_notifier(
        notifier_type="telegram",
        name="Telegram Bot",
        url="https://api.telegram.org/bot123456:ABC-DEF/sendMessage?chat_id=-1001234567890&message_thread_id=42",
        events=["new_release"],
        language="zh",
        timezone="Asia/Shanghai",
    )
    assert isinstance(notifier, TelegramNotifier)
    notifier.send_payload = AsyncMock(return_value=True)

    success = await notifier.notify("new_release", _sample_release())
    assert success is True
    sent = notifier.send_payload.call_args.args[0]
    assert sent["chat_id"] == "-1001234567890"
    assert sent["message_thread_id"] == 42
    assert sent["parse_mode"] == "Markdown"
    assert "2026-06-15 20:30:00" in sent["text"]

    # Test response validation
    accepted, error = notifier._validate_success_response(
        OutboundResponse(
            status_code=200, headers={}, body=b'{"ok":true,"result":{"message_id":100}}'
        )
    )
    assert accepted is True and error is None

    rejected, error = notifier._validate_success_response(
        OutboundResponse(
            status_code=400,
            headers={},
            body=b'{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}',
        )
    )
    assert rejected is False and "chat not found" in error
