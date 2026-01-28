"""Webhook 通知器"""

import httpx
import logging
import emoji

from ..models import Release
from .base import BaseNotifier

logger = logging.getLogger(__name__)


class WebhookNotifier(BaseNotifier):
    """Webhook 通知器"""

    def __init__(self, name: str, url: str, events: list[str] | None = None, **kwargs):
        super().__init__(name, **kwargs)
        self.url = url
        self.events = events or ["new_release"]

    async def notify(self, event: str, release: Release):
        """发送 Webhook 通知"""
        if event not in self.events:
            return

        # 构建基础消息
        message = f"[{release.tracker_name}] {event.replace('_', ' ').title()}: {release.version}"
        if release.prerelease:
            message += " (Pre-release)"
        
        # 为了兼容各类 Webhook (Discord/Slack/DingTalk等)，同时提供结构化数据和纯文本
        payload = {
            # 通用字段
            "event": event,
            "tracker": release.tracker_name,
            "version": release.version,
            
            # Discord/Slack 兼容字段
            "content": message,  # Discord
            "text": message,     # Slack/DingTalk
            
            # 详细数据 (Discord Embeds)
            "embeds": [{
                "title": f"{release.tracker_name} {release.version}",
                "description": emoji.emojize(emoji.emojize(release.body[:2000], language='alias'), language='en') if release.body else "No release notes",
                "url": release.url,
                "color": 15258703 if release.prerelease else 5763719, # Orange for pre, Green for stable
                "fields": [
                    {"name": "Tag", "value": release.tag_name, "inline": True},
                    {"name": "Channel", "value": release.channel_name or "N/A", "inline": True},
                    {"name": "Published", "value": release.published_at.isoformat(), "inline": True}
                ],
                "footer": {"text": f"Event: {event}"},
                "timestamp": release.published_at.isoformat()
            }]
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.url,
                    json=payload,
                    timeout=10.0,
                )
                response.raise_for_status()
            except Exception as e:
                # 记录错误但不抛出，避免阻塞主流程
                logger.error(f"Webhook notification failed: {e}")
