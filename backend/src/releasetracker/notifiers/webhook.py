"""Webhook 通知器"""

import httpx
import logging
import emoji
import asyncio

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
        """发送 Webhook 通知（含 429 限速处理）"""
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
            "text": message,  # Slack/DingTalk
            # 详细数据 (Discord Embeds)
            "embeds": [
                {
                    "title": f"{release.tracker_name} {release.version}",
                    "description": (
                        emoji.emojize(
                            emoji.emojize(release.body[:2000], language="alias"), language="en"
                        )
                        if release.body
                        else "No release notes"
                    ),
                    "url": release.url,
                    "color": (
                        15258703 if release.prerelease else 5763719
                    ),  # Orange for pre, Green for stable
                    "fields": [
                        {"name": "Tag", "value": release.tag_name, "inline": True},
                        {"name": "Channel", "value": release.channel_name or "N/A", "inline": True},
                        {
                            "name": "Published",
                            "value": release.published_at.isoformat(),
                            "inline": True,
                        },
                    ],
                    "footer": {"text": f"Event: {event}"},
                    "timestamp": release.published_at.isoformat(),
                }
            ],
        }

        async with httpx.AsyncClient() as client:
            for attempt in range(4):
                try:
                    response = await client.post(
                        self.url,
                        json=payload,
                        timeout=10.0,
                    )

                    if response.status_code == 429:
                        # Discord/Slack 429: 消息已被服务端接收，不需要重发
                        # 只需等待限速窗口结束即可，不 retry（否则会重复发送）
                        if attempt >= 3:
                            logger.warning(
                                f"Webhook 429 Too Many Requests after {attempt + 1} attempts, giving up"
                            )
                            return

                        wait_time = 1.0
                        # 优先读取 Retry-After 响应头（标准格式）
                        retry_after = response.headers.get("Retry-After")
                        if retry_after:
                            try:
                                wait_time = float(retry_after)
                            except ValueError:
                                pass
                        else:
                            # Discord 的 429 Body 中包含 retry_after（单位：毫秒）
                            try:
                                data = response.json()
                                if isinstance(data, dict) and "retry_after" in data:
                                    # Discord 返回毫秒，转换为秒
                                    raw = float(data["retry_after"])
                                    wait_time = raw / 1000.0 if raw > 60 else raw
                            except Exception:
                                pass

                        # 加上 0.5s 安全余量，避免边界情况
                        wait_time = min(wait_time + 0.5, 30.0)
                        logger.warning(
                            f"Webhook 429 Too Many Requests (attempt {attempt + 1}/4). "
                            f"Waiting {wait_time:.1f}s before retry..."
                        )
                        await asyncio.sleep(wait_time)
                        # 注意：Discord 在 204 No Content 时才表示消息实际投递
                        # 429 表示请求被限速拒绝，消息未投递，需要重试
                        continue

                    response.raise_for_status()
                    logger.debug(
                        f"Webhook notification sent successfully: {self.name} (attempt {attempt + 1})"
                    )
                    return

                except httpx.HTTPStatusError as e:
                    # 非 429 的 HTTP 错误（4xx/5xx），不重试
                    logger.error(
                        f"Webhook notification failed with HTTP {e.response.status_code}: {self.name}"
                    )
                    return
                except Exception as e:
                    if attempt < 3:
                        wait = 2.0**attempt  # 指数退避：1s, 2s, 4s
                        logger.warning(
                            f"Webhook notification error (attempt {attempt + 1}/4), retrying in {wait}s: {e}"
                        )
                        await asyncio.sleep(wait)
                        continue
                    logger.error(f"Webhook notification failed after 4 attempts: {e}")
                    return
