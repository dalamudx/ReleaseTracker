"""Enterprise WeChat robot notifier."""

from __future__ import annotations

from typing import Any

from ..services.outbound_http import OutboundResponse
from .webhook import WebhookNotifier

_WECOM_MARKDOWN_MAX_BYTES = 4_096


class WeComNotifier(WebhookNotifier):
    """Send ReleaseTracker events using the Enterprise WeChat robot protocol."""

    provider_name = "WeCom"

    async def notify(self, event: str, payload: Any) -> bool:
        if event not in self.events:
            return False
        generic_payload = await self.prepare(event, payload)
        content = generic_payload.get("_rendered_markdown") or _build_wecom_markdown(
            generic_payload
        )
        return await self.send_payload({"msgtype": "markdown", "markdown": {"content": content}})

    def _validate_success_response(self, response: OutboundResponse) -> tuple[bool, str | None]:
        try:
            data = response.json()
        except Exception:
            return False, "response body is not valid JSON"
        if not isinstance(data, dict):
            return False, "response body is not a JSON object"

        errcode = data.get("errcode")
        if errcode != 0:
            errmsg = str(data.get("errmsg") or "unknown error")[:240]
            return False, f"errcode={errcode!r}, errmsg={errmsg}"
        return True, None


def _build_wecom_markdown(payload: dict[str, Any]) -> str:
    embeds = payload.get("embeds")
    if isinstance(embeds, list) and embeds and isinstance(embeds[0], dict):
        embed = embeds[0]
        heading = (
            payload.get("content")
            if payload.get("tracker") and payload.get("version")
            else embed.get("title") or payload.get("content")
        )
        lines = [f"### {heading or 'ReleaseTracker'}"]
        description = str(embed.get("description") or "").strip()
        if description:
            lines.extend(("", description))

        fields = embed.get("fields")
        if isinstance(fields, list):
            for field in fields:
                if not isinstance(field, dict):
                    continue
                name = str(field.get("name") or "").strip()
                value = str(field.get("value") or "").strip()
                if name and value:
                    lines.append(f"> **{name}：** {value}")

        url = str(embed.get("url") or "").strip()
        if url:
            lines.extend(("", f"[查看详情]({url})"))
        footer = embed.get("footer")
        if isinstance(footer, dict) and footer.get("text"):
            lines.extend(("", str(footer["text"])))
        content = "\n".join(lines)
    else:
        content = str(
            payload.get("message")
            or payload.get("content")
            or payload.get("text")
            or "ReleaseTracker"
        )

    return _truncate_utf8(content, _WECOM_MARKDOWN_MAX_BYTES)


def _truncate_utf8(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    suffix = "…"
    budget = limit - len(suffix.encode("utf-8"))
    return encoded[:budget].decode("utf-8", errors="ignore") + suffix
