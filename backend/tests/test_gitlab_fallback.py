from datetime import datetime, timezone

import httpx
import pytest

from releasetracker.trackers.gitlab import GitLabTracker


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
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "params": params or {}})
        for matcher, response in self.responses:
            if matcher(url):
                return response(url)
        raise AssertionError(f"Unexpected URL requested: {url}")


@pytest.mark.asyncio
async def test_gitlab_fetch_all_falls_back_to_tags_when_releases_endpoint_returns_403(monkeypatch):
    fake_client = _FakeAsyncClient(
        responses=[
            (
                lambda url: url.endswith("/releases"),
                lambda url: _FakeResponse(403, {"message": "Forbidden"}, url),
            ),
            (
                lambda url: url.endswith("/repository/tags"),
                lambda url: _FakeResponse(
                    200,
                    [
                        {
                            "name": "v3.1.0",
                            "message": "tag message",
                            "commit": {
                                "id": "abc123",
                                "committed_date": datetime(
                                    2026, 1, 2, tzinfo=timezone.utc
                                ).isoformat(),
                            },
                        }
                    ],
                    url,
                ),
            ),
        ]
    )
    monkeypatch.setattr("releasetracker.trackers.gitlab.httpx.AsyncClient", lambda: fake_client)

    tracker = GitLabTracker(name="antora", project="antora/antora")
    releases = await tracker.fetch_all(limit=1, fallback_tags=True)

    assert len(releases) == 1
    assert releases[0].tag_name == "v3.1.0"
    assert releases[0].version == "v3.1.0"
    assert releases[0].commit_sha == "abc123"
    assert [call["url"].split("/api/v4/projects/")[1] for call in fake_client.calls] == [
        "antora%2Fantora/releases",
        "antora%2Fantora/repository/tags",
    ]


@pytest.mark.asyncio
async def test_gitlab_fetch_all_raises_403_when_fallback_tags_disabled(monkeypatch):
    fake_client = _FakeAsyncClient(
        responses=[
            (
                lambda url: url.endswith("/releases"),
                lambda url: _FakeResponse(403, {"message": "Forbidden"}, url),
            )
        ]
    )
    monkeypatch.setattr("releasetracker.trackers.gitlab.httpx.AsyncClient", lambda: fake_client)

    tracker = GitLabTracker(name="antora", project="antora/antora")

    with pytest.raises(httpx.HTTPStatusError):
        await tracker.fetch_all(limit=1, fallback_tags=False)
