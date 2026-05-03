"""Gitea tracker"""

from datetime import datetime

import httpx
import logging

from ..models import Release
from .base import BaseTracker

logger = logging.getLogger(__name__)


class GiteaTracker(BaseTracker):
    """Gitea release tracker"""

    def __init__(
        self,
        name: str,
        repo: str,
        instance: str = "https://gitea.com",
        token: str | None = None,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        # repo format: "owner/repo"
        self.repo = repo
        self.instance = instance.rstrip("/")
        self.token = token

    def _get_headers(self) -> dict:
        """Get request headers"""
        headers = {
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        return headers

    async def fetch_latest(self, fallback_tags: bool = False) -> Release | None:
        """Fetch latest release"""
        releases = await self.fetch_all(limit=1, fallback_tags=fallback_tags)
        return releases[0] if releases else None

    async def fetch_all(self, limit: int = 10, fallback_tags: bool = False) -> list[Release]:
        """Fetch all releases, preferring Releases and falling back to Tags."""
        url = f"{self.instance}/api/v1/repos/{self.repo}/releases"
        params = {
            "limit": min(limit, 50),
            "page": 1,
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    url, headers=self._get_headers(), params=params, timeout=15.0
                )
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                logger.error(f"Gitea releases fetch failed for {self.repo}: {e}")
                raise ValueError(f"Gitea fetch error: {e}") from e

            releases = []
            for item in data:
                try:
                    release = self._parse_release(item)
                    if self._should_include(release):
                        releases.append(release)
                except Exception as e:
                    logger.warning(f"Failed to parse Gitea release: {e}")

            if releases:
                return releases

            if not fallback_tags:
                return releases

            # Fallback: if no releases are fetched, try the tags API
            logger.info(f"No releases found for {self.repo}, falling back to tags fetching.")
            try:
                tags_url = f"{self.instance}/api/v1/repos/{self.repo}/tags"
                tags_params = {
                    "limit": min(limit, 50),
                    "page": 1,
                }
                tags_resp = await client.get(
                    tags_url, headers=self._get_headers(), params=tags_params, timeout=15.0
                )
                tags_resp.raise_for_status()
                tags_data = tags_resp.json()
            except Exception as e:
                logger.error(f"Gitea tags fallback fetch failed for {self.repo}: {e}")
                return []

            for item in tags_data:
                try:
                    release = self._parse_tag(item)
                    if self._should_include(release):
                        releases.append(release)
                except Exception as e:
                    logger.warning(f"Failed to parse Gitea tag: {e}")

            return releases[:limit]

    def _parse_release(self, data: dict) -> Release:
        """Parse Gitea release data"""
        tag_name = data.get("tag_name", "")

        # Determine whether this is a pre-release
        prerelease = data.get("prerelease", False)

        # Published time
        published_at_str = data.get("published_at") or data.get("created_at")
        if published_at_str:
            published_at = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
        else:
            published_at = datetime.now()

        # Commit SHA（read from target_commitish）
        commit_sha = data.get("target_commitish")

        return Release(
            tracker_name=self.name,
            tracker_type="gitea",
            name=data.get("name") or tag_name,
            tag_name=tag_name,
            version=tag_name,
            published_at=published_at,
            url=f"{self.instance}/{self.repo}/releases/tag/{tag_name}",
            prerelease=prerelease,
            body=data.get("body"),
            commit_sha=commit_sha,
        )

    def _parse_tag(self, data: dict) -> Release:
        """Parse Gitea tag data as Release objects in fallback mode"""
        tag_name = data.get("name", "")

        # Gitea tag responses include a commit object; use the commit time as the published time
        commit = data.get("commit", {})
        commit_sha = commit.get("sha") or commit.get("id")

        # Try to get time from commit metadata
        created_str = commit.get("created")
        if created_str:
            try:
                published_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            except ValueError:
                published_at = datetime.now()
        else:
            published_at = datetime.now()

        return Release(
            tracker_name=self.name,
            tracker_type="gitea",
            name=tag_name,
            tag_name=tag_name,
            version=tag_name,
            published_at=published_at,
            url=f"{self.instance}/{self.repo}/src/tag/{tag_name}",
            prerelease=False,  # Tags do not indicate prerelease status, so default to stable releases
            body=None,
            commit_sha=commit_sha,
        )
