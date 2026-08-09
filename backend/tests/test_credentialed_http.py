from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from releasetracker.models import TrackerSource
from releasetracker.services.credentialed_http import credentialed_request
from releasetracker.services.secure_urls import (
    require_canonical_https_base_url,
    require_https_url,
)
from releasetracker.trackers.gitea import GiteaTracker
from releasetracker.trackers.gitlab import GitLabTracker
from releasetracker.trackers.helm import HelmTracker


@pytest.mark.asyncio
async def test_credentialed_request_retains_credentials_on_same_origin_https_redirect():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/final"})
        return httpx.Response(200, text="ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await credentialed_request(
            client,
            "GET",
            "https://git.example.com/start",
            headers={"Authorization": "Bearer secret"},
        )

    assert response.status_code == 200
    assert [request.url.path for request in requests] == ["/start", "/final"]
    assert requests[1].headers["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    ["https://attacker.example/final", "http://git.example.com/final"],
)
async def test_credentialed_request_rejects_cross_origin_or_downgrade_redirect(location):
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(302, headers={"Location": location})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await credentialed_request(
                client,
                "GET",
                "https://git.example.com/start",
                headers={"Authorization": "Bearer secret"},
            )

    assert requests == 1


@pytest.mark.asyncio
async def test_credentialed_request_rejects_http_before_network():
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="HTTPS"):
            await credentialed_request(
                client,
                "GET",
                "http://git.example.com/releases",
                headers={"Authorization": "Bearer secret"},
            )

    assert requests == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com\\@attacker.example/path",
        "https://example.com/path\nignored",
    ],
)
def test_security_sensitive_urls_reject_ambiguous_delimiters(url):
    with pytest.raises(ValueError, match="HTTPS"):
        require_https_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/app/../admin",
        "https://example.com/app/%2e%2e/admin",
        "https://example.com/app%2fadmin",
        "https://example.com/app//admin",
    ],
)
def test_canonical_base_url_rejects_noncanonical_paths(url):
    with pytest.raises(ValueError, match="canonical"):
        require_canonical_https_base_url(url)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: GitLabTracker("gitlab", "group/project", "http://gitlab.test", token="secret"),
        lambda: GiteaTracker("gitea", "owner/repo", "http://gitea.test", token="secret"),
        lambda: HelmTracker("helm", "http://helm.test", "chart", token="secret"),
    ],
)
def test_credentialed_trackers_reject_http_configuration(factory):
    with pytest.raises(ValueError, match="HTTPS"):
        factory()


def test_tracker_source_rejects_http_when_credential_is_configured():
    with pytest.raises(ValidationError, match="HTTPS"):
        TrackerSource(
            source_key="gitlab",
            source_type="gitlab",
            credential_name="private-token",
            source_config={
                "project": "group/project",
                "instance": "http://gitlab.test",
            },
        )


def test_anonymous_legacy_http_tracker_configuration_remains_allowed():
    source = TrackerSource(
        source_key="helm",
        source_type="helm",
        source_config={"repo": "http://helm.test", "chart": "chart"},
    )
    tracker = HelmTracker("helm", source.source_config["repo"], "chart")
    assert tracker.repo == "http://helm.test"
