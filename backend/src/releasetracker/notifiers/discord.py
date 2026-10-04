"""Discord webhook notifier."""

from __future__ import annotations

from typing import Any

from ..services.outbound_http import OutboundResponse
from .webhook import WebhookNotifier


class DiscordNotifier(WebhookNotifier):
    """Send ReleaseTracker events using the Discord Webhook embeds protocol."""

    provider_name = "Discord"

    async def notify(self, event: str, payload: Any) -> bool:
        if event not in self.events:
            return False
        generic_payload = await self.prepare(event, payload)
        content = generic_payload.get("_rendered_markdown") or generic_payload.get("content") or ""
        embed = dict((generic_payload.get("embeds") or [{}])[0])
        title = embed.get("title") or generic_payload.get("tracker") or "ReleaseTracker"
        detail_url = embed.get("url")

        # Discord embed colors (decimal integer)
        if "success" in event or "new_release" in event:
            color = 5763719  # Green
        elif "failed" in event or "error" in event or "blocked" in event:
            color = 15548997  # Red
        elif "approval" in event or "warning" in event:
            color = 15105570  # Orange
        else:
            color = 5793266  # Blurple / Blue

        discord_embed: dict[str, Any] = {
            "title": str(title)[:256],
            "description": str(content)[:4096],
            "color": color,
        }
        if detail_url:
            discord_embed["url"] = detail_url
        if embed.get("fields"):
            discord_embed["fields"] = embed["fields"][:25]
        if embed.get("footer"):
            discord_embed["footer"] = embed["footer"]

        return await self.send_payload(
            {
                "embeds": [discord_embed],
            }
        )

    def _validate_success_response(self, response: OutboundResponse) -> tuple[bool, str | None]:
        # Discord returns 200 OK or 204 No Content
        if 200 <= response.status_code < 300:
            return True, None
        return False, f"HTTP {response.status_code}"
