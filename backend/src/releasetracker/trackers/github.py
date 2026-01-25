"""GitHub 追踪器"""

from datetime import datetime

import httpx
import logging

from ..models import Release
from .base import BaseTracker



logger = logging.getLogger(__name__)


class GitHubTracker(BaseTracker):
    """GitHub 版本追踪器"""

    def __init__(self, name: str, repo: str, token: str | None = None, **kwargs):
        super().__init__(name, **kwargs)
        self.repo = repo
        self.token = token
        self.base_url = "https://api.github.com"

    def _get_headers(self) -> dict:
        """获取请求头"""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def fetch_latest(self) -> Release | None:
        """获取最新版本"""
        # 复用 fetch_all 获取最新一个，这样逻辑统一且能利用 GraphQL
        releases = await self.fetch_all(limit=1)
        return releases[0] if releases else None

    async def fetch_all(self, limit: int = 10) -> list[Release]:
        """获取所有版本 (GraphQL)"""
        if not self.token:
            logger.error(f"GitHub Token is required for GraphQL API (Repo: {self.repo})")
            raise ValueError(f"GitHub Token is required for tracking {self.repo}")

        query = """
        query ($owner: String!, $name: String!, $limit: Int!) {
          repository(owner: $owner, name: $name) {
            releases(first: $limit, orderBy: {field: CREATED_AT, direction: DESC}) {
              nodes {
                name
                tagName
                description
                publishedAt
                isPrerelease
                url
                tagCommit {
                  oid
                  message
                }
              }
            }
          }
        }
        """
        
        owner, name = self.repo.split("/")
        variables = {
            "owner": owner,
            "name": name,
            "limit": limit
        }

        url = f"{self.base_url}/graphql"
        
        logger.info(f"Fetching releases via GraphQL from {self.repo} with limit {limit}")

        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                response = await client.post(
                    url, 
                    headers=self._get_headers(), 
                    json={"query": query, "variables": variables},
                    timeout=15.0
                )
                response.raise_for_status()
                result = response.json()
                
                if "errors" in result:
                    error_msg = result["errors"][0]["message"]
                    logger.error(f"GraphQL Error: {error_msg}")
                    raise ValueError(f"GitHub GraphQL Error: {error_msg}")

                data = result.get("data", {}).get("repository", {}).get("releases", {}).get("nodes", [])
                
                releases = []
                for item in data:
                    # Fallback to commit message if description is empty
                    body = item.get("description")
                    if not body and item.get("tagCommit"):
                        body = item["tagCommit"]["message"]
                    
                    # Extract commit SHA
                    commit_sha = None
                    if item.get("tagCommit"):
                        commit_sha = item["tagCommit"].get("oid")

                    release = Release(
                        tracker_name=self.name,
                        tracker_type="github",
                        name=item.get("name") or item.get("tagName"),
                        tag_name=item["tagName"],
                        version=item["tagName"].lstrip("v"),
                        url=item["url"],
                        prerelease=item["isPrerelease"],
                        published_at=datetime.fromisoformat(item["publishedAt"].replace("Z", "+00:00")),
                        created_at=datetime.now(),
                        body=body,
                        commit_sha=commit_sha
                    )
                    

                    if self._should_include(release):
                        releases.append(release)
                
                return releases

            except Exception as e:
                logger.error(f"Fetch Error: {e}")
                raise e

