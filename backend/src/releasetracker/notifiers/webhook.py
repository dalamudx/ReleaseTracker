"""Webhook 通知器"""

import httpx
import logging

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

        payload = {
            "event": event,
            "tracker": release.tracker_name,
            "release": {
                "name": release.name,
                "tag": release.tag_name,
                "version": release.version,
                "url": release.url,
                "published_at": release.published_at.isoformat(),
                "prerelease": release.prerelease,
            },
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
