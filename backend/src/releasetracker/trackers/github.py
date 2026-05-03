"""GitHub tracker"""

from datetime import datetime

import httpx
import logging

from ..models import Release
from .base import BaseTracker

logger = logging.getLogger(__name__)


class GitHubTracker(BaseTracker):
    """GitHub release tracker"""

    def __init__(
        self,
        name: str,
        repo: str,
        token: str | None = None,
        fetch_mode: str = "rest_first",
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self.repo = repo
        self.token = token
        self.fetch_mode = fetch_mode
        self.base_url = "https://api.github.com"
        self.last_fallback_hint: str | None = None

    def _get_headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def fetch_latest(self, fallback_tags: bool = False) -> Release | None:
        releases = await self.fetch_all(limit=1, fallback_tags=fallback_tags)
        return releases[0] if releases else None

    def _parse_rest_release(self, item: dict) -> Release:
        tag_name = item["tag_name"]
        return Release(
            tracker_name=self.name,
            tracker_type="github",
            name=item.get("name") or tag_name,
            tag_name=tag_name,
            version=tag_name,
            url=item["html_url"],
            prerelease=item.get("prerelease", False),
            published_at=datetime.fromisoformat(item["published_at"].replace("Z", "+00:00")),
            created_at=datetime.now(),
            body=item.get("body"),
            commit_sha=None,
        )

    async def _fetch_rest_release_commit_sha(
        self,
        client: httpx.AsyncClient,
        owner: str,
        name: str,
        tag_name: str,
    ) -> str | None:
        response = await client.get(
            f"{self.base_url}/repos/{owner}/{name}/commits/{tag_name}",
            headers=self._get_headers(),
            timeout=15.0,
        )
        response.raise_for_status()
        data = response.json()
        sha = data.get("sha")
        return sha if isinstance(sha, str) and sha.strip() else None

    async def _fetch_graphql_releases(
        self,
        client: httpx.AsyncClient,
        owner: str,
        name: str,
        limit: int,
        fallback_tags: bool,
    ) -> list[Release]:
        if not self.token:
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
        variables = {"owner": owner, "name": name, "limit": limit}
        response = await client.post(
            f"{self.base_url}/graphql",
            headers=self._get_headers(),
            json={"query": query, "variables": variables},
            timeout=15.0,
        )
        response.raise_for_status()
        result = response.json()
        if "errors" in result:
            raise ValueError(f"GitHub GraphQL Error: {result['errors'][0]['message']}")

        nodes = result.get("data", {}).get("repository", {}).get("releases", {}).get("nodes", [])

        releases: list[Release] = []
        for item in nodes:
            body = item.get("description")
            if not body and item.get("tagCommit"):
                body = item["tagCommit"].get("message")
            commit_sha = item.get("tagCommit", {}).get("oid") if item.get("tagCommit") else None
            release = Release(
                tracker_name=self.name,
                tracker_type="github",
                name=item.get("name") or item.get("tagName"),
                tag_name=item["tagName"],
                version=item["tagName"],
                url=item["url"],
                prerelease=item["isPrerelease"],
                published_at=datetime.fromisoformat(item["publishedAt"].replace("Z", "+00:00")),
                created_at=datetime.now(),
                body=body,
                commit_sha=commit_sha,
            )
            if self._should_include(release):
                releases.append(release)

        if releases or not fallback_tags:
            return releases

        tag_query = """
        query ($owner: String!, $name: String!, $limit: Int!) {
          repository(owner: $owner, name: $name) {
            refs(refPrefix: "refs/tags/", first: $limit, orderBy: {field: TAG_COMMIT_DATE, direction: DESC}) {
              nodes {
                name
                target {
                  __typename
                  ... on Commit {
                    oid
                    message
                    committedDate
                  }
                  ... on Tag {
                    oid
                    message
                    target {
                      __typename
                      ... on Commit {
                        oid
                        message
                        committedDate
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """
        tag_response = await client.post(
            f"{self.base_url}/graphql",
            headers=self._get_headers(),
            json={"query": tag_query, "variables": variables},
            timeout=15.0,
        )
        tag_response.raise_for_status()
        tag_result = tag_response.json()
        if "errors" in tag_result:
            raise ValueError(f"GitHub GraphQL Tag Error: {tag_result['errors'][0]['message']}")

        tag_nodes = (
            tag_result.get("data", {}).get("repository", {}).get("refs", {}).get("nodes", [])
        )

        for item in tag_nodes:
            tag_name = item.get("name")
            target = item.get("target") or {}
            commit_sha = None
            body = ""
            published_at = datetime.now()
            if target.get("__typename") == "Commit":
                commit_sha = target.get("oid")
                body = target.get("message", "")
                if target.get("committedDate"):
                    published_at = datetime.fromisoformat(
                        target["committedDate"].replace("Z", "+00:00")
                    )
            elif target.get("__typename") == "Tag":
                body = target.get("message", "")
                underlying = target.get("target") or {}
                if underlying.get("__typename") == "Commit":
                    commit_sha = underlying.get("oid")
                    if underlying.get("committedDate"):
                        published_at = datetime.fromisoformat(
                            underlying["committedDate"].replace("Z", "+00:00")
                        )

            release = Release(
                tracker_name=self.name,
                tracker_type="github",
                name=tag_name,
                tag_name=tag_name,
                version=tag_name,
                url=f"https://github.com/{owner}/{name}/releases/tag/{tag_name}",
                prerelease=False,
                published_at=published_at,
                created_at=datetime.now(),
                body=body,
                commit_sha=commit_sha,
            )
            if self._should_include(release):
                releases.append(release)

        return releases

    async def _fetch_rest_tag_commit(
        self, client: httpx.AsyncClient, commit_url: str
    ) -> tuple[str | None, str, datetime]:
        response = await client.get(commit_url, headers=self._get_headers(), timeout=15.0)
        response.raise_for_status()
        data = response.json()
        committed_date = data.get("commit", {}).get("committer", {}).get("date")
        return (
            data.get("sha"),
            data.get("commit", {}).get("message") or "",
            (
                datetime.fromisoformat(committed_date.replace("Z", "+00:00"))
                if committed_date
                else datetime.now()
            ),
        )

    async def _fetch_rest_releases(
        self,
        client: httpx.AsyncClient,
        owner: str,
        name: str,
        limit: int,
        fallback_tags: bool,
    ) -> list[Release]:
        response = await client.get(
            f"{self.base_url}/repos/{owner}/{name}/releases",
            headers=self._get_headers(),
            params={"per_page": limit},
            timeout=15.0,
        )
        response.raise_for_status()
        data = response.json()

        releases: list[Release] = []
        for item in data:
            release = self._parse_rest_release(item)
            try:
                release.commit_sha = await self._fetch_rest_release_commit_sha(
                    client,
                    owner,
                    name,
                    release.tag_name,
                )
            except httpx.HTTPError:
                release.commit_sha = None
            if self._should_include(release):
                releases.append(release)

        if releases or not fallback_tags:
            return releases

        tags_response = await client.get(
            f"{self.base_url}/repos/{owner}/{name}/tags",
            headers=self._get_headers(),
            params={"per_page": limit},
            timeout=15.0,
        )
        tags_response.raise_for_status()
        tags = tags_response.json()

        for item in tags:
            tag_name = item.get("name")
            commit_sha = item.get("commit", {}).get("sha")
            body = ""
            published_at = datetime.now()
            commit_url = item.get("commit", {}).get("url")
            if commit_url:
                try:
                    commit_sha, body, published_at = await self._fetch_rest_tag_commit(
                        client, commit_url
                    )
                except httpx.HTTPError:
                    pass
            release = Release(
                tracker_name=self.name,
                tracker_type="github",
                name=tag_name,
                tag_name=tag_name,
                version=tag_name,
                url=f"https://github.com/{owner}/{name}/releases/tag/{tag_name}",
                prerelease=False,
                published_at=published_at,
                created_at=datetime.now(),
                body=body,
                commit_sha=commit_sha,
            )
            if self._should_include(release):
                releases.append(release)

        return releases

    async def fetch_all(self, limit: int = 10, fallback_tags: bool = False) -> list[Release]:
        owner, name = self.repo.split("/")
        self.last_fallback_hint = None
        async with httpx.AsyncClient(follow_redirects=True) as client:
            if self.fetch_mode == "rest_first":
                return await self._fetch_rest_releases(client, owner, name, limit, fallback_tags)

            if self.fetch_mode == "graphql_first":
                return await self._fetch_graphql_releases(client, owner, name, limit, fallback_tags)

            raise ValueError(f"Unsupported GitHub fetch mode: {self.fetch_mode}")
