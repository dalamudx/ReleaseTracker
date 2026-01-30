"""GitLab 追踪器"""

from datetime import datetime
from urllib.parse import quote
import asyncio

import httpx
import logging

from ..models import Release
from .base import BaseTracker

logger = logging.getLogger(__name__)


class GitLabTracker(BaseTracker):
    """GitLab 版本追踪器"""

    def __init__(
        self,
        name: str,
        project: str,
        instance: str = "https://gitlab.com",
        token: str | None = None,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self.project = project
        self.instance = instance.rstrip("/")
        self.token = token

    def _get_headers(self) -> dict:
        """获取请求头"""
        headers = {}
        if self.token:
            headers["PRIVATE-TOKEN"] = self.token
        return headers

    async def fetch_latest(self) -> Release | None:
        """获取最新版本"""
        releases = await self.fetch_all(limit=1)
        return releases[0] if releases else None

    async def fetch_all(self, limit: int = 10) -> list[Release]:
        """获取所有版本"""
        # URL 编码项目路径
        project_id = quote(self.project, safe="")
        url = f"{self.instance}/api/v4/projects/{project_id}/releases"
        params = {"per_page": min(limit, 100)}

        async with httpx.AsyncClient() as client:
            response = await client.get(
                url, headers=self._get_headers(), params=params, timeout=10.0
            )
            response.raise_for_status()
            data = response.json()

            # 检查是否有缺少 commit 信息的情况 (某些 GitLab 版本或配置可能导致 releases 接口不返回 commit)
            tasks = []
            items_to_enrich = []

            for item in data:
                if not item.get("commit"):
                    items_to_enrich.append(item)
                    tag_name = quote(item["tag_name"], safe="")
                    tag_url = (
                        f"{self.instance}/api/v4/projects/{project_id}/repository/tags/{tag_name}"
                    )
                    tasks.append(client.get(tag_url, headers=self._get_headers(), timeout=10.0))

            if tasks:
                logger.info(f"Fetching missing commit info for {len(tasks)} releases from tags API")
                responses = await asyncio.gather(*tasks, return_exceptions=True)

                for i, res in enumerate(responses):
                    item = items_to_enrich[i]
                    if isinstance(res, httpx.Response) and res.status_code == 200:
                        tag_data = res.json()
                        if tag_data.get("commit"):
                            item["commit"] = tag_data["commit"]
                            logger.debug(
                                f"Retrieved commit info for {item['tag_name']}: {item['commit'].get('id')}"
                            )
                    else:
                        logger.warning(f"Failed to fetch tag details for {item['tag_name']}: {res}")

            releases = [self._parse_release(item) for item in data]
            return [r for r in releases if self._should_include(r)][:limit]

    def _parse_release(self, data: dict) -> Release:
        """解析 GitLab release 数据"""
        tag_name = data["tag_name"]

        release = Release(
            tracker_name=self.name,
            name=data.get("name") or tag_name,
            tag_name=tag_name,
            version=tag_name,
            published_at=datetime.fromisoformat(
                data["released_at"].replace("Z", "+00:00")
                if data.get("released_at")
                else data["created_at"].replace("Z", "+00:00")
            ),
            url=f"{self.instance}/{self.project}/-/releases/{tag_name}",
            prerelease=False,  # GitLab 没有明确的 prerelease 标记
            body=data.get("description"),  # Release Notes
            commit_sha=data.get("commit", {}).get("id"),  # Extract commit SHA
        )

        if not data.get("commit"):
            logger.warning(
                f"No commit info found for GitLab release {tag_name} in {self.project}. Data keys: {data.keys()}"
            )
        else:
            logger.debug(
                f"Parsed GitLab release {tag_name}: SHA={data.get('commit', {}).get('id')}"
            )

        return release
