"""Notifier construction shared by release, executor, and test deliveries."""

from __future__ import annotations

from .base import BaseNotifier
from .webhook import WebhookNotifier
from .wecom import WeComNotifier

SUPPORTED_NOTIFIER_TYPES = frozenset({"webhook", "wecom"})


def build_notifier(
    *,
    notifier_type: str,
    name: str,
    url: str,
    events: list[str] | None = None,
    language: str = "en",
) -> BaseNotifier:
    common = {
        "name": name,
        "url": url,
        "events": events,
        "language": language,
    }
    if notifier_type == "webhook":
        return WebhookNotifier(**common)
    if notifier_type == "wecom":
        return WeComNotifier(**common)
    raise ValueError(f"Unsupported notifier type: {notifier_type}")
