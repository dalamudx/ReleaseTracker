from datetime import datetime, timezone

import httpx
import pytest

from releasetracker.trackers.github import GitHubTracker


class _FakeResponse:
    def __init__(self, status_code: int, payload, url: str):
        self.status_code = status_code
        self._payload = payload
        self.request = httpx.Request("GET", url)
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"Client error '{self.status_code}' for url '{self.url}'",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, get_responses=None, post_responses=None):
        self.get_responses = get_responses or []
        self.post_responses = post_responses or []
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"method": "GET", "url": url, "params": params or {}})
        for matcher, response in self.get_responses:
            if matcher(url):
                return response(url)
        raise AssertionError(f"Unexpected GET URL requested: {url}")

    async def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"method": "POST", "url": url, "json": json or {}})
        for matcher, response in self.post_responses:
            if matcher(url, json or {}):
                return response(url)
        raise AssertionError(f"Unexpected POST URL requested: {url}")


@pytest.mark.asyncio
async def test_github_fetch_all_rest_first_uses_rest_releases(monkeypatch):
    fake_client = _FakeAsyncClient(
        get_responses=[
            (
                lambda url: url.endswith("/repos/owner/repo/releases"),
                lambda url: _FakeResponse(
                    200,
                    [
                        {
                            "name": "Release v1.2.3",
                            "tag_name": "v1.2.3",
                            "body": "rest notes",
                            "published_at": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
                            "prerelease": False,
                            "html_url": "https://github.com/owner/repo/releases/tag/v1.2.3",
                            "target_commitish": "abc123",
                        }
                    ],
                    url,
                ),
            ),
            (
                lambda url: url.endswith("/repos/owner/repo/commits/v1.2.3"),
                lambda url: _FakeResponse(200, {"sha": "abc123"}, url),
            ),
        ]
    )
    monkeypatch.setattr(
        "releasetracker.trackers.github.httpx.AsyncClient", lambda **kwargs: fake_client
    )

    tracker = GitHubTracker(name="repo", repo="owner/repo", fetch_mode="rest_first")
    releases = await tracker.fetch_all(limit=1)

    assert len(releases) == 1
    assert releases[0].tag_name == "v1.2.3"
    assert releases[0].body == "rest notes"
    assert releases[0].commit_sha == "abc123"
    assert [call["method"] for call in fake_client.calls] == ["GET", "GET"]


@pytest.mark.asyncio
async def test_github_fetch_all_graphql_first_does_not_fall_back_to_rest(monkeypatch):
    fake_client = _FakeAsyncClient(
        post_responses=[
            (
                lambda url, payload: url.endswith("/graphql"),
                lambda url: _FakeResponse(
                    200, {"errors": [{"message": "API rate limit exceeded"}]}, url
                ),
            )
        ],
    )
    monkeypatch.setattr(
        "releasetracker.trackers.github.httpx.AsyncClient", lambda **kwargs: fake_client
    )

    tracker = GitHubTracker(
        name="repo",
        repo="owner/repo",
        token="ghp_test",
        fetch_mode="graphql_first",
    )

    with pytest.raises(ValueError, match="API rate limit exceeded"):
        await tracker.fetch_all(limit=1)

    assert tracker.last_fallback_hint is None
    assert [call["method"] for call in fake_client.calls] == ["POST"]


@pytest.mark.asyncio
async def test_github_fetch_all_rest_first_does_not_fall_back_to_graphql(monkeypatch):
    fake_client = _FakeAsyncClient(
        get_responses=[
            (
                lambda url: url.endswith("/repos/owner/repo/releases"),
                lambda url: _FakeResponse(403, {"message": "rate limit exceeded"}, url),
            )
        ],
        post_responses=[
            (
                lambda url, payload: url.endswith("/graphql"),
                lambda url: _FakeResponse(200, {"data": {"repository": {"releases": {"nodes": []}}}}, url),
            )
        ],
    )
    monkeypatch.setattr(
        "releasetracker.trackers.github.httpx.AsyncClient", lambda **kwargs: fake_client
    )

    tracker = GitHubTracker(
        name="repo",
        repo="owner/repo",
        token="ghp_test",
        fetch_mode="rest_first",
    )

    with pytest.raises(httpx.HTTPStatusError):
        await tracker.fetch_all(limit=1)

    assert tracker.last_fallback_hint is None
    assert [call["method"] for call in fake_client.calls] == ["GET"]


@pytest.mark.asyncio
async def test_github_rest_fallback_tags_stays_in_rest_mode(monkeypatch):
    fake_client = _FakeAsyncClient(
        get_responses=[
            (
                lambda url: url.endswith("/repos/owner/repo/releases"),
                lambda url: _FakeResponse(200, [], url),
            ),
            (
                lambda url: url.endswith("/repos/owner/repo/tags"),
                lambda url: _FakeResponse(
                    200,
                    [
                        {
                            "name": "v3.0.0",
                            "commit": {
                                "sha": "tag-sha",
                                "url": "https://api.github.com/repos/owner/repo/commits/tag-sha",
                            },
                        }
                    ],
                    url,
                ),
            ),
            (
                lambda url: url.endswith("/repos/owner/repo/commits/tag-sha"),
                lambda url: _FakeResponse(
                    200,
                    {
                        "sha": "tag-sha",
                        "commit": {
                            "message": "tag commit",
                            "committer": {
                                "date": datetime(2026, 3, 1, tzinfo=timezone.utc).isoformat()
                            },
                        },
                    },
                    url,
                ),
            ),
        ],
        post_responses=[
            (
                lambda url, payload: url.endswith("/graphql"),
                lambda url: _FakeResponse(200, {"data": {}}, url),
            )
        ],
    )
    monkeypatch.setattr(
        "releasetracker.trackers.github.httpx.AsyncClient", lambda **kwargs: fake_client
    )

    tracker = GitHubTracker(name="repo", repo="owner/repo", fetch_mode="rest_first")
    releases = await tracker.fetch_all(limit=1, fallback_tags=True)

    assert [release.tag_name for release in releases] == ["v3.0.0"]
    assert [call["method"] for call in fake_client.calls] == ["GET", "GET", "GET"]


@pytest.mark.asyncio
async def test_github_graphql_fallback_tags_stays_in_graphql_mode(monkeypatch):
    fake_client = _FakeAsyncClient(
        get_responses=[
            (
                lambda url: url.endswith("/repos/owner/repo/releases"),
                lambda url: _FakeResponse(200, [], url),
            )
        ],
        post_responses=[
            (
                lambda url, payload: url.endswith("/graphql")
                and "releases(first" in payload.get("query", ""),
                lambda url: _FakeResponse(
                    200,
                    {"data": {"repository": {"releases": {"nodes": []}}}},
                    url,
                ),
            ),
            (
                lambda url, payload: url.endswith("/graphql")
                and "refs(refPrefix" in payload.get("query", ""),
                lambda url: _FakeResponse(
                    200,
                    {
                        "data": {
                            "repository": {
                                "refs": {
                                    "nodes": [
                                        {
                                            "name": "v3.0.0",
                                            "target": {
                                                "__typename": "Commit",
                                                "oid": "tag-sha",
                                                "message": "tag commit",
                                                "committedDate": datetime(
                                                    2026, 3, 1, tzinfo=timezone.utc
                                                ).isoformat(),
                                            },
                                        }
                                    ]
                                }
                            }
                        }
                    },
                    url,
                ),
            ),
        ],
    )
    monkeypatch.setattr(
        "releasetracker.trackers.github.httpx.AsyncClient", lambda **kwargs: fake_client
    )

    tracker = GitHubTracker(
        name="repo",
        repo="owner/repo",
        token="ghp_test",
        fetch_mode="graphql_first",
    )
    releases = await tracker.fetch_all(limit=1, fallback_tags=True)

    assert [release.tag_name for release in releases] == ["v3.0.0"]
    assert [call["method"] for call in fake_client.calls] == ["POST", "POST"]


@pytest.mark.asyncio
async def test_github_rest_releases_do_not_use_target_commitish_branch_as_identity(monkeypatch):
    fake_client = _FakeAsyncClient(
        get_responses=[
            (
                lambda url: url.endswith("/repos/navidrome/navidrome/releases"),
                lambda url: _FakeResponse(
                    200,
                    [
                        {
                            "name": "v0.61.2",
                            "tag_name": "v0.61.2",
                            "body": "latest notes",
                            "published_at": datetime(2026, 4, 12, tzinfo=timezone.utc).isoformat(),
                            "prerelease": False,
                            "html_url": "https://github.com/navidrome/navidrome/releases/tag/v0.61.2",
                            "target_commitish": "master",
                        },
                        {
                            "name": "v0.60.0",
                            "tag_name": "v0.60.0",
                            "body": "older notes",
                            "published_at": datetime(2026, 3, 12, tzinfo=timezone.utc).isoformat(),
                            "prerelease": False,
                            "html_url": "https://github.com/navidrome/navidrome/releases/tag/v0.60.0",
                            "target_commitish": "master",
                        },
                    ],
                    url,
                ),
            ),
            (
                lambda url: url.endswith("/repos/navidrome/navidrome/commits/v0.61.2"),
                lambda url: _FakeResponse(200, {"sha": "sha-latest"}, url),
            ),
            (
                lambda url: url.endswith("/repos/navidrome/navidrome/commits/v0.60.0"),
                lambda url: _FakeResponse(200, {"sha": "sha-older"}, url),
            ),
        ]
    )
    monkeypatch.setattr(
        "releasetracker.trackers.github.httpx.AsyncClient", lambda **kwargs: fake_client
    )

    tracker = GitHubTracker(
        name="navidrome",
        repo="navidrome/navidrome",
        fetch_mode="rest_first",
    )
    releases = await tracker.fetch_all(limit=2)

    assert [release.tag_name for release in releases] == ["v0.61.2", "v0.60.0"]
    assert [release.commit_sha for release in releases] == ["sha-latest", "sha-older"]
