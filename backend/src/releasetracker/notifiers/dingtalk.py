"""DingTalk robot notifier."""

from __future__ import annotations

from typing import Any

from ..services.outbound_http import OutboundResponse
from .webhook import WebhookNotifier


class DingTalkNotifier(WebhookNotifier):
    """Send ReleaseTracker events using the DingTalk robot markdown protocol."""

    provider_name = "DingTalk"

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
            text = f"### {title}\n\n{text}"
        if detail_url and detail_url not in text:
            view_text = "查看详情" if self.language == "zh" else "View details"
            text += f"\n\n[{view_text}]({detail_url})"

        return await self.send_payload(
            {
                "msgtype": "markdown",
                "markdown": {
                    "title": str(title)[:120],
                    "text": str(text)[:20000],
                },
            }
        )

    def _validate_success_response(self, response: OutboundResponse) -> tuple[bool, str | None]:
        try:
            data = response.json()
        except Exception:
            return False, "response body is not valid JSON"
        if not isinstance(data, dict):
            return False, "response body is not a JSON object"

        errcode = data.get("errcode")
        if errcode is not None and errcode != 0:
            errmsg = str(data.get("errmsg") or "unknown error")[:240]
            return False, f"errcode={errcode!r}, errmsg={errmsg}"
        return True, None
