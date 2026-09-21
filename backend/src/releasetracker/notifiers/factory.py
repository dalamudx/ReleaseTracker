"""Notifier construction shared by release, executor, and test deliveries."""

from __future__ import annotations

from .base import BaseNotifier
from .templates import builtin
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
    template: dict | None = None,
    detail_url: str | None = None,
) -> BaseNotifier:
    common = {
        "name": name,
        "url": url,
        "events": events,
        "language": language,
        "template": template if template is not None else builtin(),
        "detail_url": detail_url,
    }
    if notifier_type == "webhook":
        return WebhookNotifier(**common)
    if notifier_type == "wecom":
        return WeComNotifier(**common)
    raise ValueError(f"Unsupported notifier type: {notifier_type}")
