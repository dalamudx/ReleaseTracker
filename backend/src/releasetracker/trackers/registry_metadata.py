"""Bounded provider-native registry dates, never image Config or observation dates."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from ..release_times import parse_time

if TYPE_CHECKING:
    from ..models import Release
    from .docker import DockerTracker


def registry_date(value):
    """Providers use ISO-8601, HTTP-date, or epoch seconds; reject invalid values."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, timezone.utc) if value > 0 else None
        except (ValueError, OverflowError, OSError):
            return None
    result = parse_time(value)
    if result is None and isinstance(value, str):
        try:
            result = parse_time(parsedate_to_datetime(value))
        except (ValueError, TypeError, OverflowError):
            pass
    return result


def github_package_token(token: str | None) -> str | None:
    """Only explicitly configured GitHub tokens, never exchanged OCI bearer tokens.

    ghcr.io and api.github.com are a fixed first-party pair. Arbitrary registry
    secrets are NOT sent to GitHub. Legacy non-identifiable tokens stay unknown.
    """
    value = (token or "").split(":", 1)[-1].strip()
    return value if re.fullmatch(r"(?:gh[pous]_|github_pat_)[A-Za-z0-9_]+", value) else None


class RegistryMetadataClient:
    def __init__(self, tracker: DockerTracker, client: httpx.AsyncClient, max_requests: int):
        self.tracker = tracker
        self.client = client
        self.remaining = max_requests
        self.stopped = False

    async def json(self, origin: str, path: str, headers: dict | None = None):
        if self.stopped or self.remaining <= 0:
            return None
        self.remaining -= 1
        response = await self.tracker._request_with_redirect_policy(
            self.client,
            "GET",
            origin + path,
            headers={"Accept": "application/json", **(headers or {})},
            auth=None,
            timeout=min(self.tracker.timeout, 2.0),
            origin_url=origin,
            allow_cross_origin_without_credentials=False,
        )
        if response.status_code in {401, 403, 429}:
            self.stopped = True
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    async def hub(self, releases: list[Release]):
        if len(self.tracker.image.split("/")) != 2:
            return
        namespace, repository = (quote(part, safe="") for part in self.tracker.image.split("/"))
        for release in releases:
            payload = await self.json(
                "https://hub.docker.com",
                f"/v2/namespaces/{namespace}/repositories/{repository}/tags/{quote(release.tag_name, safe='')}",
            )
            if not isinstance(payload, dict) or payload.get("name") != release.tag_name:
                continue
            if str(payload.get("digest", "")).lower() == release.commit_sha.lower():
                release.registry_pushed_at = registry_date(payload.get("tag_last_pushed"))

    async def quay(self, releases: list[Release]):
        repository = "/".join(quote(part, safe="") for part in self.tracker.image.split("/"))
        # Robot Basic credentials are not Quay API OAuth tokens. Keep them on
        # this origin only; private API access may require a suitable API token.
        headers = self.metadata_headers()
        for release in releases:
            payload = await self.json(
                self.tracker._registry_url(),
                f"/api/v1/repository/{repository}/tag/?onlyActiveTags=true&specificTag={quote(release.tag_name, safe='')}",
                headers,
            )
            tags = payload.get("tags") if isinstance(payload, dict) else None
            for tag in tags if isinstance(tags, list) else []:
                if not isinstance(tag, dict) or tag.get("name") != release.tag_name:
                    continue
                if str(tag.get("manifest_digest", "")).lower() != release.commit_sha.lower():
                    continue
                release.registry_created_at = registry_date(tag.get("start_ts"))
                modified = registry_date(tag.get("last_modified"))
                if modified is not None:
                    release.registry_modified_at = modified
                break

    def metadata_headers(self) -> dict[str, str]:
        if self.tracker.metadata_token is not None:
            return (
                {"Authorization": f"Bearer {self.tracker.metadata_token}"}
                if self.tracker.metadata_token
                else {}
            )
        return self.tracker._get_auth_header(None)

    async def github(self, releases: list[Release]):
        token = github_package_token(
            self.tracker.metadata_token
            if self.tracker.metadata_token is not None
            else self.tracker.token
        )
        if not token or "/" not in self.tracker.image:
            return
        owner, package = self.tracker.image.split("/", 1)
        origin = "https://api.github.com"
        headers = {"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"}
        owner_info = await self.json(origin, f"/users/{quote(owner, safe='')}", headers)
        if (
            not isinstance(owner_info, dict)
            or str(owner_info.get("login", "")).lower() != owner.lower()
        ):
            return
        kind = {"Organization": "orgs", "User": "users"}.get(owner_info.get("type"))
        if kind is None:
            return
        unresolved = list(releases)
        page = 1
        while unresolved and self.remaining > 0 and not self.stopped:
            versions = await self.json(
                origin,
                f"/{kind}/{quote(owner, safe='')}/packages/container/{quote(package, safe='')}/versions?per_page=100&page={page}",
                headers,
            )
            if not isinstance(versions, list):
                return
            for version in versions:
                if not isinstance(version, dict):
                    continue
                metadata = version.get("metadata")
                container = metadata.get("container") if isinstance(metadata, dict) else None
                tags = container.get("tags") if isinstance(container, dict) else None
                if not isinstance(tags, list):
                    continue
                for release in list(unresolved):
                    if (
                        str(version.get("name", "")).lower() == release.commit_sha.lower()
                        and release.tag_name in tags
                    ):
                        # updated_at is not a tag push timestamp. created_at is
                        # the stable package-version (digest) registration date.
                        release.registry_created_at = registry_date(version.get("created_at"))
                        unresolved.remove(release)
            if len(versions) < 100:
                return
            page += 1

    async def harbor(self, releases: list[Release]):
        parts = self.tracker.image.split("/", 1)
        if len(parts) != 2:
            return
        project, repository = parts
        # Harbor's router requires double-encoding repository path separators.
        repository = quote(quote(repository, safe=""), safe="")
        headers = self.metadata_headers()
        for release in releases:
            payload = await self.json(
                self.tracker._registry_url(),
                f"/api/v2.0/projects/{quote(project, safe='')}/repositories/{repository}/artifacts/{quote(release.commit_sha, safe='')}?with_tag=true",
                headers,
            )
            if (
                not isinstance(payload, dict)
                or str(payload.get("digest", "")).lower() != release.commit_sha.lower()
            ):
                continue
            tags = payload.get("tags")
            if not isinstance(tags, list) or not any(
                isinstance(tag, dict) and tag.get("name") == release.tag_name for tag in tags
            ):
                continue
            release.registry_pushed_at = registry_date(payload.get("push_time"))


async def enrich_registry_times(
    tracker: DockerTracker, releases: list[Release], *, budget_seconds: float, max_requests: int
):
    candidates = [
        release
        for release in releases
        if release.commit_sha
        and re.fullmatch(r"sha256:[0-9a-f]{64}", release.commit_sha.lower())
        and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", release.tag_name)
    ]
    if not candidates or any(part in {"", ".", ".."} for part in tracker.image.split("/")):
        return
    for release in candidates:
        # Last-Modified is bound to the very response that supplied this digest.
        # It is explicitly a modification date, NEVER silently a push date.
        release.registry_modified_at = tracker._manifest_modified.get(
            (release.tag_name, release.commit_sha.lower())
        )
    provider = {"registry-1.docker.io": "hub", "ghcr.io": "github", "quay.io": "quay"}.get(
        tracker.registry, tracker._registry_metadata_provider
    )
    if provider not in {"hub", "github", "quay", "harbor"}:
        return
    async with asyncio.timeout(budget_seconds):
        async with httpx.AsyncClient(follow_redirects=False) as client:
            metadata = RegistryMetadataClient(tracker, client, max_requests)
            await getattr(metadata, provider)(candidates)
