from datetime import datetime, timezone

import httpx
import pytest

from releasetracker.models import Release, TrackerSource
from releasetracker.services.changelog import RepositoryChangelogFetcher


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "https://example.test"),
                response=httpx.Response(self.status_code),
            )


class _FakeAsyncClient:
    calls = []
    response_status: int = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "params": params or {}, "timeout": timeout})
        return _FakeResponse("# changelog", self.response_status)


def _release():
    return Release(
        tracker_name="tracker",
        tracker_type="github",
        name="v1.2.3",
        tag_name="v1.2.3",
        version="1.2.3",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        url="https://example.test/release",
    )


@pytest.mark.asyncio
async def test_github_raw_fetch_reuses_bearer_token(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr("releasetracker.services.changelog.httpx.AsyncClient", lambda **kwargs: _FakeAsyncClient())
    source = TrackerSource(source_key="repo", source_type="github", source_config={"repo": "owner/repo"})

    content = await RepositoryChangelogFetcher(token="ghp_token").fetch_file(
        source, "CHANGELOG.md", "release_tag", _release(), None
    )

    assert content == "# changelog"
    assert _FakeAsyncClient.calls[0]["url"] == "https://api.github.com/repos/owner/repo/contents/CHANGELOG.md"
    assert _FakeAsyncClient.calls[0]["headers"]["Authorization"] == "Bearer ghp_token"
    assert _FakeAsyncClient.calls[0]["params"] == {"ref": "v1.2.3"}


@pytest.mark.asyncio
async def test_gitlab_raw_fetch_reuses_private_token(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr("releasetracker.services.changelog.httpx.AsyncClient", lambda **kwargs: _FakeAsyncClient())
    source = TrackerSource(
        source_key="repo",
        source_type="gitlab",
        source_config={"instance": "https://gitlab.example.com", "project": "group/project"},
    )

    await RepositoryChangelogFetcher(token="gl_token").fetch_file(
        source, "docs/releases/1.2.3.md", "default_branch", _release(), None
    )

    assert _FakeAsyncClient.calls[0]["url"] == "https://gitlab.example.com/api/v4/projects/group%2Fproject/repository/files/docs%2Freleases%2F1.2.3.md/raw"
    assert _FakeAsyncClient.calls[0]["headers"] == {"PRIVATE-TOKEN": "gl_token"}
    assert _FakeAsyncClient.calls[0]["params"] == {"ref": "HEAD"}


@pytest.mark.asyncio
async def test_gitea_raw_fetch_reuses_token_header(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr("releasetracker.services.changelog.httpx.AsyncClient", lambda **kwargs: _FakeAsyncClient())
    source = TrackerSource(
        source_key="repo",
        source_type="gitea",
        source_config={"instance": "https://gitea.example.com", "repo": "owner/repo"},
    )

    await RepositoryChangelogFetcher(token="gitea_token").fetch_file(
        source, "CHANGELOG.md", "configured_ref", _release(), "main"
    )

    assert _FakeAsyncClient.calls[0]["url"] == "https://gitea.example.com/api/v1/repos/owner/repo/raw/CHANGELOG.md"
    assert _FakeAsyncClient.calls[0]["headers"]["Authorization"] == "token gitea_token"
    assert _FakeAsyncClient.calls[0]["params"] == {"ref": "main"}


class _FakeAsyncClient404(_FakeAsyncClient):
    async def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "params": params or {}, "timeout": timeout})
        return _FakeResponse("", 404)


@pytest.mark.asyncio
async def test_missing_changelog_file_raises_http_error(monkeypatch):
    _FakeAsyncClient404.calls = []
    monkeypatch.setattr("releasetracker.services.changelog.httpx.AsyncClient", lambda **kwargs: _FakeAsyncClient404())
    source = TrackerSource(source_key="repo", source_type="github", source_config={"repo": "owner/repo"})

    with pytest.raises(httpx.HTTPStatusError):
        await RepositoryChangelogFetcher(token=None).fetch_file(
            source, "CHANGELOG.md", "default_branch", _release(), None
        )
