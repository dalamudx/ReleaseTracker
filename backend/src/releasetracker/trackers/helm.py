"""Helm Chart tracker"""

from datetime import datetime

import httpx
import yaml

from ..models import Release
from .base import BaseTracker


class HelmTracker(BaseTracker):
    """Helm Chart tracker"""

    def __init__(self, name: str, repo: str, chart: str, token: str | None = None, **kwargs):
        super().__init__(name, **kwargs)
        self.repo = repo.rstrip("/")
        self.chart = chart
        self.token = token

    async def fetch_latest(self, fallback_tags: bool = False) -> Release | None:
        """Fetch latest release"""
        releases = await self.fetch_all(limit=1, fallback_tags=fallback_tags)
        return releases[0] if releases else None

    async def fetch_all(self, limit: int = 10, fallback_tags: bool = False) -> list[Release]:
        """Fetch all releases"""
        url = f"{self.repo}/index.yaml"
        headers = {}

        if self.token:
            # Simple Bearer Token authentication
            # If Basic Auth is required, users can enter "username:password" format
            # For now assume Bearer Token or similar header authentication
            # Note: some Helm repositories may require Basic Auth; this is simplified for now
            headers["Authorization"] = f"Bearer {self.token}"

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10.0)
            response.raise_for_status()
            data = yaml.safe_load(response.text)

            if "entries" not in data or self.chart not in data["entries"]:
                return []

            chart_versions = data["entries"][self.chart]
            releases = [self._parse_chart_version(item) for item in chart_versions]

            # Sort by time
            releases.sort(key=lambda r: r.published_at, reverse=True)

            return [r for r in releases if self._should_include(r)][:limit]

    def _parse_chart_version(self, data: dict) -> Release:
        """Parse Helm chart version data"""
        chart_version = data["version"]
        app_version = data.get("appVersion") or chart_version
        created = data.get("created")
        digest = data.get("digest")

        # Try to parse time
        if created:
            try:
                published_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                published_at = datetime.now()
        else:
            published_at = datetime.now()

        return Release(
            tracker_name=self.name,
            tracker_type="helm",
            name=self.chart,
            tag_name=chart_version,
            version=app_version,
            app_version=app_version,
            chart_version=chart_version,
            published_at=published_at,
            url=self.repo,
            prerelease=("-" in app_version)
            or ("alpha" in app_version)
            or ("beta" in app_version)
            or ("rc" in app_version),
            commit_sha=digest,
        )
