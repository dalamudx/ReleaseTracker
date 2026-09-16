"""Docker/OCI container image tracker"""

import asyncio
import base64
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urljoin

import httpx

from ..models import Release
from ..services.secure_urls import require_https_url, same_origin_https
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

# Anonymous pulls on these registries share a small shared quota (docker.io is
# the notorious one), so by default we skip config-blob reads when there is no
# credential to prove we're not a scrape bot.
_RATE_LIMITED_ANONYMOUS_REGISTRIES = frozenset(
    {
        "registry-1.docker.io",
        "docker.io",
        "quay.io",
    }
)

# Process-local cooldown per registry host. When a registry returns 429 or
# signals exhausted remaining quota, we skip config-blob reads for this long
# to avoid amplifying the problem.
_RATE_LIMIT_COOLDOWN_SECONDS = 600  # 10 minutes
_registry_cooldowns: dict[str, float] = {}

# Config-blob bodies are typically small; cap to defend against malicious /
# mis-configured registries that could send very large JSON.
_MAX_CONFIG_BLOB_BYTES = 256 * 1024  # 256 KiB
_REGISTRY_MAX_REDIRECTS = 5
_REGISTRY_MAX_TAG_PAGES = 100

# Reproducible builds often set config.created to the Unix epoch. Treat any
# timestamp before 2000 as "not a real publish time" and ignore it.
_MIN_CREDIBLE_CREATED = datetime(2000, 1, 1, tzinfo=timezone.utc)

PublishedAtMode = Literal["auto", "prefer_real", "first_observed"]

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

# Preferred architecture for platform selection when resolving multi-arch
# manifest indexes. We only need one platform's config to read `created`; all
# platforms in a multi-arch build share the same upstream build time.
_PLATFORM_PREFERENCES = (
    ("linux", "amd64"),
    ("linux", "arm64"),
    ("linux", "arm"),
)


class DockerTracker(BaseTracker):
    """OCI container image tracker compatible with Docker Hub, GHCR, and private registries"""

    def __init__(
        self,
        name: str,
        image: str,
        registry: str | None = None,
        token: str | None = None,
        published_at_mode: PublishedAtMode = "auto",
        allow_registry_redirects: bool = False,
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
        self.published_at_mode: PublishedAtMode = published_at_mode
        self.allow_registry_redirects = bool(allow_registry_redirects)

    def _registry_url(self) -> str:
        return f"https://{self.registry}"

    def _validate_registry_request_url(self, url: str) -> str:
        """Accept only HTTPS requests to the configured registry origin.

        Registry-provided pagination links and redirects are attacker-controlled.
        A configured registry credential or a bearer token must never be sent to
        another origin.
        """
        validated = require_https_url(url, field="Container registry URL")
        if not same_origin_https(self._registry_url(), validated):
            raise ValueError("Container registry request refused a cross-origin URL")
        return validated

    @staticmethod
    def _is_redirect(response: httpx.Response) -> bool:
        return 300 <= response.status_code < 400

    @classmethod
    def _reject_redirect(cls, response: httpx.Response) -> None:
        if cls._is_redirect(response):
            raise ValueError("Container registry redirect is not allowed")

    @staticmethod
    def _strip_credential_headers(headers: dict[str, str] | None) -> dict[str, str]:
        if not headers:
            return {}
        credential_headers = {
            "authorization",
            "proxy-authorization",
            "cookie",
            "x-docker-token",
            "x-registry-auth",
        }
        return {
            name: value for name, value in headers.items() if name.lower() not in credential_headers
        }

    @staticmethod
    def _resolve_redirect_url(current_url: str, location: str | None, *, field: str) -> str:
        if not location:
            raise ValueError("Container registry redirect is missing Location")
        return require_https_url(urljoin(current_url, location), field=field)

    async def _request_with_redirect_policy(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        origin_url: str,
        allow_cross_origin_without_credentials: bool,
        **kwargs,
    ) -> httpx.Response:
        """Send a request with explicit bounded redirect policy.

        Registry metadata endpoints may only redirect within their original
        HTTPS origin. Blob downloads may redirect to HTTPS storage origins, but
        credentials are stripped irreversibly as soon as the chain changes
        origin. Automatic httpx redirects remain disabled for every hop.
        """

        current_url = require_https_url(url, field="Container registry request URL")
        origin_url = require_https_url(origin_url, field="Container registry origin URL")
        current_headers = dict(headers or {})
        request_params = kwargs.pop("params", None)
        request_kwargs = {**kwargs, "follow_redirects": False}
        visited: set[str] = set()
        stripped_for_cross_origin = False

        if not self.allow_registry_redirects:
            single_hop_kwargs = dict(request_kwargs)
            if request_params is not None:
                single_hop_kwargs["params"] = request_params
            response = await client.request(
                method,
                current_url,
                headers=current_headers,
                **single_hop_kwargs,
            )
            response.extensions["releasetracker_effective_url"] = _response_effective_url(
                response,
                current_url,
            )
            self._reject_redirect(response)
            return response

        for redirect_count in range(_REGISTRY_MAX_REDIRECTS + 1):
            hop_kwargs = dict(request_kwargs)
            if redirect_count == 0 and request_params is not None:
                hop_kwargs["params"] = request_params
            if stripped_for_cross_origin:
                current_headers = self._strip_credential_headers(current_headers)
                client.cookies.clear()

            response = await client.request(
                method,
                current_url,
                headers=current_headers,
                **hop_kwargs,
            )
            effective_url = _response_effective_url(response, current_url)
            response.extensions["releasetracker_effective_url"] = effective_url
            if not self._is_redirect(response):
                return response

            if redirect_count >= _REGISTRY_MAX_REDIRECTS:
                raise ValueError("Container registry redirect chain exceeded limit")

            visited.add(effective_url)
            next_url = self._resolve_redirect_url(
                effective_url,
                response.headers.get("location"),
                field="Container registry redirect URL",
            )
            if next_url in visited:
                raise ValueError("Container registry redirect loop detected")

            next_is_same_origin = same_origin_https(origin_url, next_url)
            if not allow_cross_origin_without_credentials and not next_is_same_origin:
                raise ValueError("Container registry redirect refused a cross-origin URL")
            if allow_cross_origin_without_credentials and not next_is_same_origin:
                stripped_for_cross_origin = True
            if stripped_for_cross_origin:
                current_headers = self._strip_credential_headers(current_headers)

            current_url = next_url

        raise ValueError("Container registry redirect chain exceeded limit")

    async def _registry_config_blob_request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        **kwargs,
    ) -> httpx.Response:
        """Fetch an OCI config blob with bounded credential-free storage redirects."""
        validated_url = self._validate_registry_request_url(url)
        if method.upper() != "GET" or f"/v2/{self.image}/blobs/" not in validated_url:
            raise ValueError(
                "Container registry config blob redirect is only allowed for blob GETs"
            )

        return await self._request_with_redirect_policy(
            client,
            method,
            validated_url,
            headers=headers,
            origin_url=self._registry_url(),
            allow_cross_origin_without_credentials=True,
            **kwargs,
        )

    async def _registry_request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        **kwargs,
    ) -> httpx.Response:
        """Send an authenticated request only to the configured HTTPS origin."""
        return await self._request_with_redirect_policy(
            client,
            method,
            self._validate_registry_request_url(url),
            headers=headers,
            origin_url=self._registry_url(),
            allow_cross_origin_without_credentials=False,
            **kwargs,
        )

    def _should_fetch_config_blob(self) -> bool:
        """Decide whether config-blob reading is worth attempting for this registry.

        - `prefer_real`: try on every registry; user accepts the request cost.
        - `first_observed`: never try; entirely skip the extra request.
        - `auto` (default):
            - If we have a credential (`self.token`), always try.
            - Otherwise skip on registries known to aggressively rate-limit
              anonymous pulls (docker.io, quay.io).
            - Skip on any registry currently in cooldown (recently 429'd).
        """
        if self.published_at_mode == "first_observed":
            return False
        if self.published_at_mode == "prefer_real":
            return not _is_registry_cooling_down(self.registry)

        # auto mode
        if _is_registry_cooling_down(self.registry):
            return False
        if self.token:
            return True
        return self.registry not in _RATE_LIMITED_ANONYMOUS_REGISTRIES

    async def _get_bearer_token(self, client: httpx.AsyncClient, scope: str) -> str | None:
        """Fetch access token from auth service via Bearer Token flow"""
        # Find the auth endpoint from known mappings, otherwise discover it from WWW-Authenticate
        auth_info = _REGISTRY_AUTH.get(self.registry)
        if not auth_info:
            # For unknown registries, send a probe request first and read auth parameters from the 401 response headers
            probe_url = f"{self._registry_url()}/v2/"
            try:
                resp = await self._registry_request(client, "GET", probe_url, timeout=self.timeout)
                if resp.status_code == 401:
                    www_auth = resp.headers.get("www-authenticate", "")
                    auth_info = _parse_www_authenticate(www_auth)
                else:
                    return None  # Authentication is not required
            except (httpx.TimeoutException, httpx.RequestError):
                return None

        if not auth_info:
            return None

        realm = auth_info.get("realm", "")
        service = auth_info.get("service", "")

        params = {"scope": scope, "service": service}
        realm = require_https_url(realm, field="Container registry authorization realm")
        # A registry can advertise a separate bearer-token service. Never
        # disclose the configured registry credential to that service unless
        # it is the exact same HTTPS origin as the registry itself.
        headers = (
            self._get_auth_header(None) if same_origin_https(self._registry_url(), realm) else {}
        )

        try:
            resp = await self._request_with_redirect_policy(
                client,
                "GET",
                realm,
                params=params,
                headers=headers,
                timeout=self.timeout,
                origin_url=realm,
                allow_cross_origin_without_credentials=False,
            )
            resp.raise_for_status()
            return resp.json().get("token") or resp.json().get("access_token")
        except Exception as e:
            logger.warning(f"DockerTracker: Fetch Bearer token failed: {e}")
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

        visited_pages: set[str] = set()
        while url:
            url = self._validate_registry_request_url(url)
            if url in visited_pages:
                raise ValueError("Container registry pagination loop detected")
            if len(visited_pages) >= _REGISTRY_MAX_TAG_PAGES:
                raise ValueError("Container registry pagination exceeded limit")
            visited_pages.add(url)

            try:
                resp = await self._registry_request(
                    client, "GET", url, headers=headers, timeout=self.timeout
                )
                resp.raise_for_status()
            except httpx.TimeoutException:
                logger.error(f"DockerTracker: Registry request timed out. URL={url}")
                raise ValueError("Container registry connection timed out")

            try:
                data = resp.json()
            except ValueError:
                logger.error(
                    f"DockerTracker: Failed to parse tags, response is not valid JSON. URL={url}"
                )
                break

            tags.extend(data.get("tags") or [])
            # Pagination: Link header
            link = resp.headers.get("link", "")
            page_url = resp.extensions.get("releasetracker_effective_url", url)
            next_url = _parse_link_header(link, page_url)
            url = self._validate_registry_request_url(next_url) if next_url else None

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
        response = await self._registry_request(
            client, method, url, headers=headers, timeout=self.timeout
        )
        if response.status_code != 401:
            return response, bearer_token

        refreshed_token = await self._get_bearer_token(client, scope)
        if refreshed_token is None:
            return response, bearer_token

        retry_headers = {
            "Accept": _MANIFEST_ACCEPT,
            **self._get_auth_header(refreshed_token),
        }
        retry_response = await self._registry_request(
            client, method, url, headers=retry_headers, timeout=self.timeout
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
                    if head_status == 429:
                        _mark_registry_rate_limited(self.registry)
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
                        if get_status == 429:
                            _mark_registry_rate_limited(self.registry)
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

    async def _fetch_image_created(
        self,
        client: httpx.AsyncClient,
        tag: str,
        bearer_token: str | None,
        scope: str,
    ) -> tuple[datetime | None, str | None]:
        """Fetch the image's build time from the OCI config blob.

        Returns (created_at, current_bearer_token). `created_at` is None when
        any step fails (missing config, unusable platforms, HTTP errors,
        implausible timestamp, etc.) so the caller can fall back gracefully.

        The extra HTTP cost is:
          * one GET manifest (pulls the body we already HEAD'd for digest);
          * if it's a multi-arch index, one more GET for a platform manifest;
          * one GET config blob.

        That's at most 3 calls per tag — only invoked when the tag is new to
        the database, so amortised cost per scheduler tick is near zero once
        the tracker is in steady state.
        """
        manifest_url = f"{self._registry_url()}/v2/{self.image}/manifests/{tag}"
        current_token = bearer_token

        # Step 1: GET the manifest body to find the config digest.
        try:
            response, current_token = await self._request_manifest(
                client, "GET", manifest_url, current_token, scope
            )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            logger.debug(
                "DockerTracker: config blob fetch transport error on %s:%s: %s",
                self.image,
                tag,
                exc,
            )
            return None, current_token

        if response.status_code == 429:
            _mark_registry_rate_limited(self.registry)
            return None, current_token
        if response.status_code >= 400:
            return None, current_token

        try:
            manifest_json = response.json()
        except ValueError:
            return None, current_token

        media_type = response.headers.get("Content-Type", "") or manifest_json.get("mediaType", "")

        # Step 2: if we got an index / manifest list, pick one platform and
        # GET that sub-manifest. Otherwise the body already contains config.
        if _is_manifest_index(media_type, manifest_json):
            sub_digest = _pick_platform_manifest(manifest_json)
            if sub_digest is None:
                return None, current_token
            sub_url = f"{self._registry_url()}/v2/{self.image}/manifests/{sub_digest}"
            try:
                response, current_token = await self._request_manifest(
                    client, "GET", sub_url, current_token, scope
                )
            except (httpx.TimeoutException, httpx.RequestError):
                return None, current_token

            if response.status_code == 429:
                _mark_registry_rate_limited(self.registry)
                return None, current_token
            if response.status_code >= 400:
                return None, current_token
            try:
                manifest_json = response.json()
            except ValueError:
                return None, current_token

        config_descriptor = manifest_json.get("config")
        if not isinstance(config_descriptor, dict):
            return None, current_token
        config_digest = config_descriptor.get("digest")
        if not isinstance(config_digest, str) or not config_digest:
            return None, current_token

        # Step 3: fetch the config blob and read `created`.
        blob_url = f"{self._registry_url()}/v2/{self.image}/blobs/{config_digest}"
        try:
            blob_resp = await self._registry_config_blob_request(
                client,
                "GET",
                blob_url,
                headers=self._get_auth_header(current_token),
                timeout=self.timeout,
            )
        except (ValueError, httpx.TimeoutException, httpx.RequestError):
            return None, current_token

        if blob_resp.status_code == 429:
            _mark_registry_rate_limited(self.registry)
            return None, current_token
        if blob_resp.status_code >= 400:
            return None, current_token

        # Defend against oversized / malicious config bodies.
        raw_body = blob_resp.content[:_MAX_CONFIG_BLOB_BYTES]
        try:
            import json

            config_json = json.loads(raw_body)
        except ValueError:
            return None, current_token

        created_str = config_json.get("created")
        if not isinstance(created_str, str) or not created_str.strip():
            return None, current_token

        try:
            created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except ValueError:
            return None, current_token

        # Reject reproducible-build placeholder (epoch) and other clearly
        # implausible times so the UI doesn't show "55 years ago" for those.
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if created_at < _MIN_CREDIBLE_CREATED:
            return None, current_token

        return created_at, current_token

    def _tag_to_release(self, tag: str, created_at: datetime | None = None) -> Release:
        """Convert a tag into a Release object"""
        return Release(
            tracker_name=self.name,
            tracker_type="container",
            name=tag,
            tag_name=tag,
            version=tag,
            published_at=created_at or datetime.now(),
            published_at_source=("artifact_created" if created_at else "first_observed"),
            url=f"https://{self.registry}/{self.image}:{tag}",
            prerelease=False,
        )

    async def fetch_latest(self, fallback_tags: bool = False) -> Release | None:
        releases = await self.fetch_all(limit=1, fallback_tags=fallback_tags)
        return releases[0] if releases else None

    async def fetch_all(self, limit: int = 10, fallback_tags: bool = False) -> list[Release]:
        """Fetch the first `limit` tags matching filter rules.

        The `published_at` handling works in two layers:
          * Per-tag: when the tracker can read the image's config blob, we
            replace the placeholder timestamp with the real build time.
          * Across tags: the remaining tags keep the default placeholder
            (now() offset by rank). The storage layer takes care of NOT
            overwriting an already-stored published_at when the digest hasn't
            changed, so steady state is stable even when config-blob reading
            is skipped.
        """
        scope = f"repository:{self.image}:pull"

        logger.info(
            f"DockerTracker: Fetching tags from {self.registry}/{self.image} (limit={limit})"
        )

        async with httpx.AsyncClient(follow_redirects=False) as client:
            # 1. Fetch Bearer token
            bearer_token = await self._get_bearer_token(client, scope)

            # 2. Fetch tag list
            try:
                all_tags = await self._fetch_tags(client, bearer_token)
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"DockerTracker: Fetch tag list failed {e.response.status_code}: {e.response.text}"
                )
                raise ValueError(
                    f"Failed to fetch image tags: {e.response.status_code} {e.response.text}"
                )
            except httpx.TimeoutException:
                logger.error(f"DockerTracker: Fetch tag list timed out (Repository {self.image})")
                raise ValueError(
                    "Container registry connection timed out; check the network or increase the timeout setting"
                )

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
            allow_config_blob = self._should_fetch_config_blob()

            for candidate in candidate_window:
                digest, _, error_reason, bearer_token = await self._resolve_manifest_digest(
                    client,
                    candidate.tag_name,
                    bearer_token,
                    scope,
                )
                if digest:
                    candidate.commit_sha = digest

                    # Try to upgrade the placeholder published_at to the real
                    # image build time, but only when the registry policy
                    # allows it and we haven't been rate-limited recently.
                    if allow_config_blob and not _is_registry_cooling_down(self.registry):
                        real_created, bearer_token = await self._fetch_image_created(
                            client,
                            candidate.tag_name,
                            bearer_token,
                            scope,
                        )
                        if real_created is not None:
                            candidate.published_at = real_created
                            candidate.published_at_source = "artifact_created"

                    continue

                # Digest resolution failed. We still emit the candidate so
                # first-time tags can get tracked; the storage layer is
                # responsible for preserving the previous published_at when
                # a digest is already on file for the same tag.
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


def _response_effective_url(response: httpx.Response, fallback_url: str) -> str:
    try:
        return str(response.request.url)
    except RuntimeError:
        return fallback_url


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
    """Parse Link header to get the next page URL."""
    if not link:
        return None
    # format: <url>; rel="next"
    for part in link.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            url_part = part.split(";")[0].strip().strip("<>")
            if not url_part:
                return None
            return urljoin(base_url, url_part)
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
    """Assign placeholder `published_at` values that preserve the rank order.

    The storage layer treats container releases specially: when the digest
    has not changed, the stored `published_at` is retained rather than
    overwritten with this placeholder. So each tag's timestamp only sticks
    the FIRST time we see it; after that the DB keeps the real first-seen
    time even if this function runs again on the next scheduler tick.
    """
    base_time = datetime.now()
    total = len(releases)
    for index, release in enumerate(releases):
        release.published_at = base_time + timedelta(seconds=total - index)


def _is_manifest_index(media_type: str, manifest_json: dict) -> bool:
    if isinstance(media_type, str):
        lowered = media_type.lower()
        if "manifest.list" in lowered or "image.index" in lowered:
            return True
    body_media_type = manifest_json.get("mediaType")
    if isinstance(body_media_type, str):
        lowered_body = body_media_type.lower()
        if "manifest.list" in lowered_body or "image.index" in lowered_body:
            return True
    # Some older Docker media types still use "manifests" array.
    return isinstance(manifest_json.get("manifests"), list)


def _pick_platform_manifest(index_json: dict) -> str | None:
    """Pick a platform manifest from an index, preferring amd64/arm64 Linux.

    Returns the digest string of the chosen manifest, or None if none match.
    We explicitly skip attestation manifests (`vnd.in-toto+json` etc.) that
    some registries add to image indexes.
    """
    manifests = index_json.get("manifests")
    if not isinstance(manifests, list):
        return None

    # Filter to real image manifests (skip attestation / signature artifacts).
    candidates: list[dict] = []
    for entry in manifests:
        if not isinstance(entry, dict):
            continue
        media_type = entry.get("mediaType", "")
        if not isinstance(media_type, str):
            continue
        lowered = media_type.lower()
        if "manifest" not in lowered or "signature" in lowered or "attestation" in lowered:
            continue
        if "in-toto" in lowered:
            continue
        candidates.append(entry)

    if not candidates:
        return None

    # Prefer preferred (os, arch) pairs in order.
    for os_name, arch_name in _PLATFORM_PREFERENCES:
        for entry in candidates:
            platform = entry.get("platform") or {}
            if not isinstance(platform, dict):
                continue
            if (
                platform.get("os") == os_name
                and platform.get("architecture") == arch_name
                and platform.get("variant") in (None, "", "v8")
            ):
                digest = entry.get("digest")
                if isinstance(digest, str) and digest:
                    return digest

    # Fall back to first Linux image, then any image manifest.
    for entry in candidates:
        platform = entry.get("platform") or {}
        if isinstance(platform, dict) and platform.get("os") == "linux":
            digest = entry.get("digest")
            if isinstance(digest, str) and digest:
                return digest

    first_digest = candidates[0].get("digest")
    return first_digest if isinstance(first_digest, str) and first_digest else None


def _is_registry_cooling_down(registry: str) -> bool:
    deadline = _registry_cooldowns.get(registry)
    if deadline is None:
        return False
    if time.monotonic() < deadline:
        return True
    # expired — clean up so the dict doesn't grow unbounded
    _registry_cooldowns.pop(registry, None)
    return False


def _mark_registry_rate_limited(registry: str, seconds: float | None = None) -> None:
    cooldown = seconds if seconds is not None else _RATE_LIMIT_COOLDOWN_SECONDS
    _registry_cooldowns[registry] = time.monotonic() + cooldown
    logger.info(
        "DockerTracker: registry %s rate-limited, skipping config blob reads for %ss",
        registry,
        cooldown,
    )
