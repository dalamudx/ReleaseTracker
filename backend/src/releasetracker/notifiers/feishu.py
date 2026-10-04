"""Feishu (Lark) robot notifier."""

from __future__ import annotations

from typing import Any

from ..services.outbound_http import OutboundResponse
from .webhook import WebhookNotifier


class FeishuNotifier(WebhookNotifier):
    """Send ReleaseTracker events using the Feishu / Lark robot card protocol."""

    provider_name = "Feishu"

    async def notify(self, event: str, payload: Any) -> bool:
        if event not in self.events:
            return False
        generic_payload = await self.prepare(event, payload)
        content = generic_payload.get("_rendered_markdown") or generic_payload.get("content") or ""
        embed = (generic_payload.get("embeds") or [{}])[0]
        title = embed.get("title") or generic_payload.get("tracker") or "ReleaseTracker"
        detail_url = embed.get("url")

        # Color templates: blue (info/new), green (success), red (failed/error), orange (warning/action)
        if "success" in event or "new_release" in event:
            header_color = "green"
        elif "failed" in event or "error" in event or "blocked" in event:
            header_color = "red"
        elif "approval" in event or "warning" in event:
            header_color = "orange"
        else:
            header_color = "blue"

        elements: list[dict[str, Any]] = [
            {
                "tag": "markdown",
                "content": str(content)[:20000],
            }
        ]
        if detail_url:
            elements.append(
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {
                                "tag": "plain_text",
                                "content": "查看详情" if self.language == "zh" else "View details",
                            },
                            "type": "primary",
                            "url": detail_url,
                        }
                    ],
                }
            )

        card_payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": str(title)[:100],
                    },
                    "template": header_color,
                },
                "elements": elements,
            },
        }
        return await self.send_payload(card_payload)

    def _validate_success_response(self, response: OutboundResponse) -> tuple[bool, str | None]:
        try:
            data = response.json()
        except Exception:
            return False, "response body is not valid JSON"
        if not isinstance(data, dict):
            return False, "response body is not a JSON object"

        code = data.get("code") if "code" in data else data.get("StatusCode")
        if code is not None and code != 0:
            msg = str(data.get("msg") or data.get("StatusMessage") or "unknown error")[:240]
            return False, f"code={code!r}, msg={msg}"
        return True, None
