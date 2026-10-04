"""Slack webhook notifier."""

from __future__ import annotations

from typing import Any

from ..services.outbound_http import OutboundResponse
from .webhook import WebhookNotifier


class SlackNotifier(WebhookNotifier):
    """Send ReleaseTracker events using the Slack incoming webhook protocol."""

    provider_name = "Slack"

    async def notify(self, event: str, payload: Any) -> bool:
        if event not in self.events:
            return False
        generic_payload = await self.prepare(event, payload)
        content = generic_payload.get("_rendered_markdown") or generic_payload.get("content") or ""
        embed = (generic_payload.get("embeds") or [{}])[0]
        title = embed.get("title") or generic_payload.get("tracker") or "ReleaseTracker"
        detail_url = embed.get("url")

        text = content
        if not text.startswith("#"):
            text = f"*{title}*\n\n{text}"
        if detail_url and detail_url not in text:
            view_text = "查看详情" if self.language == "zh" else "View details"
            text += f"\n\n<{detail_url}|{view_text}>"

        return await self.send_payload({
            "text": str(text)[:40000],
        })

    def _validate_success_response(self, response: OutboundResponse) -> tuple[bool, str | None]:
        if 200 <= response.status_code < 300:
            return True, None
        return False, f"HTTP {response.status_code}"
