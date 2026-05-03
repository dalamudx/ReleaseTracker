"""GitLab tracker"""

from datetime import datetime
from urllib.parse import quote
import asyncio

import httpx
import logging

from ..models import Release
from .base import BaseTracker

logger = logging.getLogger(__name__)


class GitLabTracker(BaseTracker):
    """GitLab release tracker"""

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
        """Get request headers"""
        headers = {}
        if self.token:
            headers["PRIVATE-TOKEN"] = self.token
        return headers

    async def fetch_latest(self, fallback_tags: bool = False) -> Release | None:
        """Fetch latest release"""
        releases = await self.fetch_all(limit=1, fallback_tags=fallback_tags)
        return releases[0] if releases else None

    async def _fetch_tags_fallback(
        self, client: httpx.AsyncClient, project_id: str, limit: int
    ) -> list[Release]:
        logger.info(f"Falling back to GitLab tags for {self.project}.")
        tag_url = f"{self.instance}/api/v4/projects/{project_id}/repository/tags"
        tags_resp = await client.get(
            tag_url,
            headers=self._get_headers(),
            params={"per_page": min(limit, 100), "order_by": "version"},
            timeout=10.0,
        )
        tags_resp.raise_for_status()
        tags_data = tags_resp.json()

        releases: list[Release] = []
        for item in tags_data:
            try:
                release = self._parse_tag(item)
                if self._should_include(release):
                    releases.append(release)
            except Exception as e:
                logger.warning(f"Failed to parse GitLab tag: {e}")

        return releases[:limit]

    async def fetch_all(self, limit: int = 10, fallback_tags: bool = False) -> list[Release]:
        """Fetch all releases"""
        # URL-encode the project path
        project_id = quote(self.project, safe="")
        url = f"{self.instance}/api/v4/projects/{project_id}/releases"
        params = {"per_page": min(limit, 100)}

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    url, headers=self._get_headers(), params=params, timeout=10.0
                )
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError:
                if not fallback_tags:
                    raise
                logger.warning(
                    f"GitLab releases fetch failed for {self.project}, trying tags fallback",
                    exc_info=True,
                )
                return await self._fetch_tags_fallback(client, project_id, limit)

            # Check for missing commit information because some GitLab versions or configurations may omit commit data from the releases API
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
            releases = [r for r in releases if self._should_include(r)][:limit]

            if releases:
                return releases

            if not fallback_tags:
                return releases

            # Fallback: if no releases are fetched, try the Tags API
            return await self._fetch_tags_fallback(client, project_id, limit)

    def _parse_release(self, data: dict) -> Release:
        """Parse GitLab release data"""
        tag_name = data["tag_name"]

        release = Release(
            tracker_name=self.name,
            tracker_type="gitlab",
            name=data.get("name") or tag_name,
            tag_name=tag_name,
            version=tag_name,
            published_at=datetime.fromisoformat(
                data["released_at"].replace("Z", "+00:00")
                if data.get("released_at")
                else data["created_at"].replace("Z", "+00:00")
            ),
            url=f"{self.instance}/{self.project}/-/releases/{tag_name}",
            prerelease=False,  # GitLab does not provide an explicit prerelease flag
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

    def _parse_tag(self, data: dict) -> Release:
        """Parse GitLab tag data as Release objects in fallback mode"""
        tag_name = data.get("name", "")
        commit = data.get("commit", {})
        commit_sha = commit.get("id")

        # Use commit time as published time
        committed_date = commit.get("committed_date") or commit.get("authored_date")
        if committed_date:
            try:
                published_at = datetime.fromisoformat(committed_date.replace("Z", "+00:00"))
            except ValueError:
                published_at = datetime.now()
        else:
            published_at = datetime.now()

        return Release(
            tracker_name=self.name,
            tracker_type="gitlab",
            name=tag_name,
            tag_name=tag_name,
            version=tag_name,
            published_at=published_at,
            url=f"{self.instance}/{self.project}/-/tags/{tag_name}",
            prerelease=False,
            body=data.get("message"),
            commit_sha=commit_sha,
        )
