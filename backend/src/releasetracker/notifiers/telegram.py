"""Telegram Bot API notifier."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from ..services.outbound_http import OutboundResponse
from .webhook import WebhookNotifier


class TelegramNotifier(WebhookNotifier):
    """Send ReleaseTracker events using the Telegram Bot sendMessage API."""

    provider_name = "Telegram"

    async def notify(self, event: str, payload: Any) -> bool:
        if event not in self.events:
            return False
        generic_payload = await self.prepare(event, payload)
        content = generic_payload.get("_rendered_markdown") or generic_payload.get("content") or ""
        embed = (generic_payload.get("embeds") or [{}])[0]
        title = embed.get("title") or generic_payload.get("tracker") or "ReleaseTracker"
        detail_url = embed.get("url")

        # Parse query params from URL to extract chat_id or message_thread_id
        chat_id = None
        message_thread_id = None
        try:
            parsed = urllib.parse.urlparse(self.url)
            query_params = urllib.parse.parse_qs(parsed.query)
            if "chat_id" in query_params:
                chat_id = query_params["chat_id"][0]
            if "message_thread_id" in query_params:
                message_thread_id = query_params["message_thread_id"][0]
        except Exception:
            pass

        text = content
        if not text.startswith("#") and not text.startswith("*"):
            text = f"*{title}*\n\n{text}"

        # Clean markdown headers: ### Title -> *Title*
        text = re.sub(r"^###\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
        text = re.sub(r"^##\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
        text = re.sub(r"^#\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)

        if detail_url and detail_url not in text:
            view_text = "查看详情" if self.language == "zh" else "View details"
            text += f"\n\n[{view_text}]({detail_url})"

        # Telegram text limit: 4096 UTF-8 characters
        telegram_payload: dict[str, Any] = {
            "text": str(text)[:4096],
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }
        if chat_id:
            telegram_payload["chat_id"] = chat_id
        if message_thread_id:
            try:
                telegram_payload["message_thread_id"] = int(message_thread_id)
            except ValueError:
                pass

        return await self.send_payload(telegram_payload)

    def _validate_success_response(self, response: OutboundResponse) -> tuple[bool, str | None]:
        try:
            data = response.json()
        except Exception:
            return False, "response body is not valid JSON"
        if not isinstance(data, dict):
            return False, "response body is not a JSON object"

        if data.get("ok") is not True:
            desc = str(data.get("description") or "unknown error")[:240]
            code = data.get("error_code")
            return False, f"error_code={code!r}, description={desc}"
        return True, None
