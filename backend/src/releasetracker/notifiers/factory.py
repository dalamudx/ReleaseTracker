"""Notifier construction shared by release, executor, and test deliveries."""

from __future__ import annotations

from .base import BaseNotifier
from .dingtalk import DingTalkNotifier
from .discord import DiscordNotifier
from .feishu import FeishuNotifier
from .slack import SlackNotifier
from .telegram import TelegramNotifier
from .templates import builtin
from .webhook import WebhookNotifier
from .wecom import WeComNotifier

SUPPORTED_NOTIFIER_TYPES = frozenset(
    {"webhook", "wecom", "feishu", "dingtalk", "discord", "slack", "telegram"}
)


def build_notifier(
    *,
    notifier_type: str,
    name: str,
    url: str,
    events: list[str] | None = None,
    language: str = "en",
    template: dict | None = None,
    detail_url: str | None = None,
    timezone: str | None = None,
) -> BaseNotifier:
    common = {
        "name": name,
        "url": url,
        "events": events,
        "language": language,
        "template": template if template is not None else builtin(),
        "detail_url": detail_url,
        "timezone": timezone,
    }
    if notifier_type == "webhook":
        return WebhookNotifier(**common)
    if notifier_type == "wecom":
        return WeComNotifier(**common)
    if notifier_type == "feishu":
        return FeishuNotifier(**common)
    if notifier_type == "dingtalk":
        return DingTalkNotifier(**common)
    if notifier_type == "discord":
        return DiscordNotifier(**common)
    if notifier_type == "slack":
        return SlackNotifier(**common)
    if notifier_type == "telegram":
        return TelegramNotifier(**common)
    raise ValueError(f"Unsupported notifier type: {notifier_type}")
