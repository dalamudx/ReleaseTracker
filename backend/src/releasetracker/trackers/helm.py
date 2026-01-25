"""Helm Chart 追踪器"""

from datetime import datetime

import httpx
import yaml

from ..models import Release
from .base import BaseTracker


class HelmTracker(BaseTracker):
    """Helm Chart 追踪器"""

    def __init__(self, name: str, repo: str, chart: str, token: str | None = None, **kwargs):
        super().__init__(name, **kwargs)
        self.repo = repo.rstrip("/")
        self.chart = chart
        self.token = token

    async def fetch_latest(self) -> Release | None:
        """获取最新版本"""
        releases = await self.fetch_all(limit=1)
        return releases[0] if releases else None

    async def fetch_all(self, limit: int = 10) -> list[Release]:
        """获取所有版本"""
        url = f"{self.repo}/index.yaml"
        headers = {}
        
        if self.token:
            # 简单的 Bearer Token 认证
            # 如果需要 Basic Auth，用户可以在 Token 字段填入 "username:password" 格式
            # 但这里我们先假设是 Bearer Token 或类似的 Header 认证
            # 注意：某些 Helm 仓库可能需要 Basic Auth，这里暂简化处理
            headers["Authorization"] = f"Bearer {self.token}"

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10.0)
            response.raise_for_status()
            data = yaml.safe_load(response.text)

            if "entries" not in data or self.chart not in data["entries"]:
                return []

            chart_versions = data["entries"][self.chart]
            releases = [self._parse_chart_version(item) for item in chart_versions]

            # 按时间排序
            releases.sort(key=lambda r: r.published_at, reverse=True)

            return [r for r in releases if self._should_include(r)][:limit]

    def _parse_chart_version(self, data: dict) -> Release:
        """解析 Helm chart 版本数据"""
        version = data["version"]
        created = data.get("created")

        # 尝试解析时间
        if created:
            try:
                published_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                published_at = datetime.now()
        else:
            published_at = datetime.now()

        return Release(
            tracker_name=self.name,
            name=self.chart,
            tag_name=version,
            version=version,
            published_at=published_at,
            url=self.repo,
            prerelease="-" in version or "alpha" in version or "beta" in version or "rc" in version,
        )
