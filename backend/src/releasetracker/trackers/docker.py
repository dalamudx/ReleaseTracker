"""Docker/OCI container image tracker"""

import asyncio
import base64
import logging
import random
import re
from datetime import datetime, timedelta

import httpx

from ..models import Release
from .base import BaseTracker

logger = logging.getLogger(__name__)

# Default registry and auth service for common registries
_REGISTRY_AUTH = {
    "registry-1.docker.io": {
        "realm": "https://auth.docker.io/token",
        "service": "registry.docker.io",
    },
    "ghcr.io": {
        "realm": "https://ghcr.io/token",
        "service": "ghcr.io",
    },
}

_DEFAULT_REGISTRY = "registry-1.docker.io"

# Matches both two-part (x.y) and three-part (x.y.z) numeric version tags,
# with an optional suffix separated by '.' or '-'.
# Group 1: major, Group 2: minor, Group 3: patch (optional), Group 4: suffix (optional)
_VERSION_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:[.\-](.*))?$")
_NATURAL_TOKEN = tuple[int, int, str]
_VERSION_KEY = tuple[int, int, int, int, tuple[_NATURAL_TOKEN, ...]]

_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ]
)


class DockerTracker(BaseTracker):
    """OCI container image tracker compatible with Docker Hub, GHCR, and private registries"""

    def __init__(
        self,
        name: str,
        image: str,
        registry: str | None = None,
        token: str | None = None,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        # Normalize registry and image: runtime configuration uses registry plus repository path as the canonical structure,
        # but UI or legacy bridge paths may still pass ghcr.io/owner/repo. OCI API path/scope must remove the registry host.
        self.registry = _normalize_registry(registry)
        self.image = _normalize_image_for_registry(
            image, self.registry
        )  # such as "library/nginx" or "owner/repo"

        # Tokens support two formats:
        #   "username:password" → Basic Auth
        #   "ghp_xxxx" / raw Bearer Token
        self.token = token

    def _registry_url(self) -> str:
        return f"https://{self.registry}"

    async def _get_bearer_token(self, client: httpx.AsyncClient, scope: str) -> str | None:
        """Fetch access token from auth service via Bearer Token flow"""
        # Find the auth endpoint from known mappings, otherwise discover it from WWW-Authenticate
        auth_info = _REGISTRY_AUTH.get(self.registry)
        if not auth_info:
            # For unknown registries, send a probe request first and read auth parameters from the 401 response headers
            probe_url = f"{self._registry_url()}/v2/"
            try:
                resp = await client.get(probe_url, timeout=self.timeout)
                if resp.status_code == 401:
                    www_auth = resp.headers.get("www-authenticate", "")
                    auth_info = _parse_www_authenticate(www_auth)
                else:
                    return None  # Authentication is not required
            except Exception:
                return None

        if not auth_info:
            return None

        realm = auth_info.get("realm", "")
        service = auth_info.get("service", "")

        params = {"scope": scope, "service": service}
        headers = {}

        if self.token:
            if ":" in self.token:
                # username:password format → Basic Auth
                encoded = base64.b64encode(self.token.encode()).decode()
                headers["Authorization"] = f"Basic {encoded}"
            else:
                # Raw token, such as a GitHub PAT, -> Bearer
                headers["Authorization"] = f"Bearer {self.token}"

        try:
            resp = await client.get(realm, params=params, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json().get("token") or resp.json().get("access_token")
        except Exception as e:
            logger.warning(f"DockerTracker: Fetch Bearer token 失败: {e}")
            return None

    def _get_auth_header(self, bearer_token: str | None) -> dict:
        """Build authentication headers for actual registry requests"""
        if bearer_token:
            return {"Authorization": f"Bearer {bearer_token}"}
        if self.token and ":" in self.token:
            encoded = base64.b64encode(self.token.encode()).decode()
            return {"Authorization": f"Basic {encoded}"}
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    async def _fetch_tags(self, client: httpx.AsyncClient, bearer_token: str | None) -> list[str]:
        """Fetch all tags for an image"""
        url = f"{self._registry_url()}/v2/{self.image}/tags/list"
        headers = self._get_auth_header(bearer_token)
        tags = []

        while url:
            try:
                resp = await client.get(url, headers=headers, timeout=self.timeout)
                resp.raise_for_status()
            except httpx.TimeoutException:
                logger.error(f"DockerTracker: 请求 Registry 超时。URL={url}")
                raise ValueError("连接容器仓库超时")

            try:
                data = resp.json()
            except ValueError:
                logger.error(f"DockerTracker: 解析 Tags 失败，返回的并不是合法的 JSON。URL={url}")
                break

            tags.extend(data.get("tags") or [])
            # Pagination: Link header
            link = resp.headers.get("link", "")
            url = _parse_link_header(link, self._registry_url())

        return tags

    async def _request_manifest(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        bearer_token: str | None,
        scope: str,
    ) -> tuple[httpx.Response, str | None]:
        headers = {
            "Accept": _MANIFEST_ACCEPT,
            **self._get_auth_header(bearer_token),
        }
        response = await client.request(method, url, headers=headers, timeout=self.timeout)
        if response.status_code != 401:
            return response, bearer_token

        refreshed_token = await self._get_bearer_token(client, scope)
        if refreshed_token is None:
            return response, bearer_token

        retry_headers = {
            "Accept": _MANIFEST_ACCEPT,
            **self._get_auth_header(refreshed_token),
        }
        retry_response = await client.request(
            method, url, headers=retry_headers, timeout=self.timeout
        )
        return retry_response, refreshed_token

    async def _resolve_manifest_digest(
        self,
        client: httpx.AsyncClient,
        tag: str,
        bearer_token: str | None,
        scope: str,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        manifest_url = f"{self._registry_url()}/v2/{self.image}/manifests/{tag}"
        current_token = bearer_token
        last_error: str | None = None

        for attempt in range(3):
            try:
                head_resp, current_token = await self._request_manifest(
                    client,
                    "HEAD",
                    manifest_url,
                    current_token,
                    scope,
                )
                head_status = head_resp.status_code

                if head_status == 404:
                    return None, None, "manifest_not_found", current_token
                if head_status == 401:
                    return None, None, "manifest_unauthorized", current_token
                if head_status == 429 or 500 <= head_status < 600:
                    last_error = f"manifest_head_http_{head_status}"
                elif 400 <= head_status < 500:
                    return None, None, f"manifest_head_http_{head_status}", current_token
                else:
                    digest = head_resp.headers.get("Docker-Content-Digest")
                    media_type = head_resp.headers.get("Content-Type")
                    if digest:
                        return digest, media_type, None, current_token

                    get_resp, current_token = await self._request_manifest(
                        client,
                        "GET",
                        manifest_url,
                        current_token,
                        scope,
                    )
                    get_status = get_resp.status_code

                    if get_status == 404:
                        return None, None, "manifest_not_found", current_token
                    if get_status == 401:
                        return None, None, "manifest_unauthorized", current_token
                    if get_status == 429 or 500 <= get_status < 600:
                        last_error = f"manifest_get_http_{get_status}"
                    elif 400 <= get_status < 500:
                        return None, None, f"manifest_get_http_{get_status}", current_token
                    else:
                        digest = get_resp.headers.get("Docker-Content-Digest")
                        media_type = get_resp.headers.get("Content-Type")
                        if digest:
                            return digest, media_type, None, current_token
                        return None, media_type, "manifest_digest_header_missing", current_token
            except httpx.TimeoutException:
                last_error = "manifest_timeout"
            except httpx.RequestError as exc:
                last_error = f"manifest_transport_error:{type(exc).__name__}"

            if attempt < 2:
                if attempt == 0:
                    await asyncio.sleep(random.uniform(0.1, 0.3))
                else:
                    await asyncio.sleep(random.uniform(0.3, 0.9))

        return None, None, last_error, current_token

    def _tag_to_release(self, tag: str, created_at: datetime | None = None) -> Release:
        """Convert a tag into a Release object"""
        return Release(
            tracker_name=self.name,
            tracker_type="container",
            name=tag,
            tag_name=tag,
            version=tag,
            published_at=created_at or datetime.now(),
            url=f"https://{self.registry}/{self.image}:{tag}",
            prerelease=False,
        )

    async def fetch_latest(self, fallback_tags: bool = False) -> Release | None:
        releases = await self.fetch_all(limit=1, fallback_tags=fallback_tags)
        return releases[0] if releases else None

    async def fetch_all(self, limit: int = 10, fallback_tags: bool = False) -> list[Release]:
        """Fetch the first limit tags matching filter rules"""
        scope = f"repository:{self.image}:pull"

        logger.info(f"DockerTracker: 从 {self.registry}/{self.image} 获取 Tags（limit={limit}）")

        async with httpx.AsyncClient(follow_redirects=True) as client:
            # 1. Fetch Bearer token
            bearer_token = await self._get_bearer_token(client, scope)

            # 2. Fetch tag list
            try:
                all_tags = await self._fetch_tags(client, bearer_token)
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"DockerTracker: Fetch tag list失败 {e.response.status_code}: {e.response.text}"
                )
                raise ValueError(f"获取镜像 Tag 失败: {e.response.status_code} {e.response.text}")
            except httpx.TimeoutException:
                logger.error(f"DockerTracker: Fetch tag list超时 (Repository {self.image})")
                raise ValueError("连接容器仓库超时，请检查网络或增加超时设定")

            if not all_tags:
                return []

            # 3. Sort by version number, with latest last
            sorted_tags = _sort_tags(all_tags)

            # 4. Convert raw tags and then apply user-defined filters.
            releases = []
            for tag in sorted_tags:
                releases.append(self._tag_to_release(tag))

            releases = [release for release in releases if self._should_include(release)]
            _apply_semver_published_at(releases)

            candidate_window = releases[:limit]
            for candidate in candidate_window:
                digest, _, error_reason, bearer_token = await self._resolve_manifest_digest(
                    client,
                    candidate.tag_name,
                    bearer_token,
                    scope,
                )
                if digest:
                    candidate.commit_sha = digest
                    continue

                logger.warning(
                    "DockerTracker: digest resolution failed, keep candidate without digest "
                    "(image=%s, tag=%s, reason=%s)",
                    self.image,
                    candidate.tag_name,
                    error_reason or "unknown",
                )

            return candidate_window


# ============================================================
# Utility functions
# ============================================================


def _parse_www_authenticate(header: str) -> dict | None:
    """Parse realm/service from a WWW-Authenticate Bearer header"""
    if not header.lower().startswith("bearer "):
        return None
    result = {}
    rest = header[7:]  # Remove "Bearer "
    for part in rest.split(","):
        part = part.strip()
        if "=" in part:
            key, _, val = part.partition("=")
            result[key.strip()] = val.strip().strip('"')
    return result if result else None


def _parse_link_header(link: str, base_url: str) -> str | None:
    """Parse Link header to get the next page URL"""
    if not link:
        return None
    # format: <url>; rel="next"
    for part in link.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            url_part = part.split(";")[0].strip().strip("<>")
            if url_part.startswith("/"):
                return base_url + url_part
            return url_part
    return None


def _normalize_registry(registry: str | None) -> str:
    normalized = (registry or _DEFAULT_REGISTRY).strip().rstrip("/")
    if normalized.startswith("https://"):
        normalized = normalized[len("https://") :]
    elif normalized.startswith("http://"):
        normalized = normalized[len("http://") :]

    normalized = normalized.rstrip("/")
    if normalized == "docker.io":
        return _DEFAULT_REGISTRY
    return normalized or _DEFAULT_REGISTRY


def _normalize_image_for_registry(image: str, registry: str) -> str:
    normalized = image.strip().strip("/")
    if normalized.startswith("https://"):
        normalized = normalized[len("https://") :]
    elif normalized.startswith("http://"):
        normalized = normalized[len("http://") :]
    normalized = normalized.strip("/")

    registry_aliases = {registry}
    if registry == _DEFAULT_REGISTRY:
        registry_aliases.update({"docker.io", "registry-1.docker.io"})

    for registry_alias in registry_aliases:
        prefix = f"{registry_alias}/"
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


def _version_key(tag: str) -> _VERSION_KEY:
    m = _VERSION_TAG_RE.match(tag)
    if not m:
        return (0, 0, 0, -1, _natural_suffix_key(tag))
    major = int(m.group(1))
    minor = int(m.group(2))
    patch = int(m.group(3)) if m.group(3) is not None else 0
    suffix = m.group(4) or ""
    return (major, minor, patch, 1 if not suffix else 0, _natural_suffix_key(suffix))


def _natural_suffix_key(value: str) -> tuple[_NATURAL_TOKEN, ...]:
    normalized = value.lower().replace("_", ".").replace("-", ".")
    tokens = [token for token in re.split(r"(\d+)", normalized) if token]
    return tuple((1, int(token), "") if token.isdigit() else (0, 0, token) for token in tokens)


def _version_parts(tag: str) -> tuple[int, int, int, bool, str] | None:
    match = _VERSION_TAG_RE.match(tag)
    if not match:
        return None

    patch = int(match.group(3)) if match.group(3) is not None else 0
    return (
        int(match.group(1)),
        int(match.group(2)),
        patch,
        match.group(3) is not None,
        match.group(4) or "",
    )


def _sort_tags(tags: list[str]) -> list[str]:
    """
    Sort the tag list:
    - Version tags (v1.2.3 / 1.2.3 / 22.04) sort by version descending
    - Special tags such as latest and nightly sort first for single-tag tracking
    - Other non-version tags sort by natural token order descending
    """
    special = []
    semver = []
    other = []

    for tag in tags:
        if tag in ("latest", "stable", "nightly", "edge", "main"):
            special.append(tag)
        elif _VERSION_TAG_RE.match(tag):
            semver.append(tag)
        else:
            other.append(tag)

    semver.sort(key=_version_key, reverse=True)
    other.sort(key=_natural_suffix_key, reverse=True)

    return special + semver + other


def _apply_semver_published_at(releases: list[Release]) -> None:
    base_time = datetime.now()
    total = len(releases)
    for index, release in enumerate(releases):
        release.published_at = base_time + timedelta(seconds=total - index)
