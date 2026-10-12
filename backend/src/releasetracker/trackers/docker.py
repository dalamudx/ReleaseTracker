"""Docker/OCI container image tracker"""

import asyncio
import base64
import logging
import random
import re
from collections.abc import Iterable
from datetime import datetime
from urllib.parse import urljoin

import httpx

from ..models import Release
from ..services.registry_errors import RegistryTagListError, RegistryAuthenticationError
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


_REGISTRY_MAX_REDIRECTS = 5
_REGISTRY_PUSH_TIME_BUDGET_SECONDS = 5.0
_REGISTRY_PUSH_TIME_REQUEST_LIMIT = 10
_REGISTRY_MAX_TAG_PAGES = 100
# Matches both two-part (x.y) and three-part (x.y.z) numeric version tags,
# with an optional suffix separated by '.' or '-'.
# Group 1: major, Group 2: minor, Group 3: patch (optional), Group 4: suffix (optional)
_VERSION_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:[.\-](.*))?$")
_NATURAL_TOKEN = tuple[int, int, str]
_VERSION_KEY = tuple[int, int, int, int, tuple[_NATURAL_TOKEN, ...]]
_FLOATING_ALIAS_NAMES = frozenset(
    {"latest", "stable", "nightly", "edge", "main", "dev", "canary", "beta", "alpha"}
)
_COMMIT_SUFFIX_RE = re.compile(r"(?:^|[._-])[0-9a-f]{7,64}$", re.IGNORECASE)

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
        allow_registry_redirects: bool = False,
        credential_name: str | None = None,
        metadata_token: str | None = None,
        version_format: str = "dotted",
        **kwargs,
    ):
        # Legacy direct callers may still pass this removed option; it has no effect.
        kwargs.pop("published_at_mode", None)
        super().__init__(name, **kwargs)
        self.version_format = version_format
        self.last_complete_tags: tuple[str, ...] | None = None
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
        self.metadata_token = metadata_token
        self._credentials_configured = bool(credential_name or token)
        self.allow_registry_redirects = bool(allow_registry_redirects)
        self._alias_last_observed: dict[str, datetime] | None = None
        self._alias_digest_by_name: dict[str, str] = {}
        self._priority_aliases: tuple[str, ...] = ()
        self._manifest_modified: dict[tuple[str, str], datetime] = {}
        self._registry_metadata_provider: str | None = None

    def configure_incremental_fetch(
        self,
        *,
        alias_last_observed: dict[str, datetime],
        alias_digest_by_name: dict[str, str] | None = None,
        priority_aliases: Iterable[str] | None = None,
    ) -> None:
        """Configure persisted state used for bounded alias reconciliation."""
        self._alias_last_observed = dict(alias_last_observed)
        self._alias_digest_by_name = {
            alias: digest.lower()
            for alias, digest in (alias_digest_by_name or {}).items()
            if alias and digest
        }
        self._priority_aliases = tuple(
            dict.fromkeys(alias for alias in (priority_aliases or ()) if alias)
        )

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
                    if auth_info and same_origin_https(
                        self._registry_url(), auth_info.get("realm", "")
                    ):
                        from urllib.parse import urlparse

                        realm_path = urlparse(auth_info["realm"]).path
                        if realm_path == "/service/token":
                            self._registry_metadata_provider = "harbor"
                        elif realm_path == "/v2/auth":
                            self._registry_metadata_provider = "quay"
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
            self._raise_authentication_error(resp)
            resp.raise_for_status()
            return resp.json().get("token") or resp.json().get("access_token")
        except RegistryAuthenticationError:
            raise
        except Exception as e:
            logger.warning(f"DockerTracker: Fetch Bearer token failed: {e}")
            return None

    def _raise_authentication_error(self, response: httpx.Response) -> None:
        if self._credentials_configured and response.status_code in {401, 403}:
            raise RegistryAuthenticationError(response.status_code)

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
            except httpx.HTTPStatusError as exc:
                # A tag-list 404 includes OCI NAME_UNKNOWN and registries/proxies
                # which return no JSON body. Never expose their raw response.
                self._raise_authentication_error(exc.response)
                if exc.response.status_code in RegistryTagListError.DETAILS:
                    raise RegistryTagListError(exc.response.status_code) from exc
                raise
            except httpx.TimeoutException:
                logger.error(f"DockerTracker: Registry request timed out. URL={url}")
                raise ValueError("Container registry connection timed out")

            try:
                data = resp.json()
            except ValueError:
                logger.error(
                    f"DockerTracker: Failed to parse tags, response is not valid JSON. URL={url}"
                )
                raise ValueError("Container registry tag list is not valid JSON")

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
            self._raise_authentication_error(response)
            return response, bearer_token

        refreshed_token = await self._get_bearer_token(client, scope)
        if refreshed_token is None:
            self._raise_authentication_error(response)
            return response, bearer_token

        retry_headers = {
            "Accept": _MANIFEST_ACCEPT,
            **self._get_auth_header(refreshed_token),
        }
        retry_response = await self._registry_request(
            client, method, url, headers=retry_headers, timeout=self.timeout
        )
        self._raise_authentication_error(retry_response)
        return retry_response, refreshed_token

    async def verify_running_manifest(self, pinned: str, actual: str) -> str:
        """Prove a runtime manifest belongs to a pinned index, never compare config IDs.

        Requests use existing registry auth/SSRF protections. The returned body
        must hash to the requested digest before any descriptor can be trusted.
        Nested indexes are bounded to depth two and eight requests.
        """
        import hashlib

        if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", pinned) or not re.fullmatch(
            r"sha256:[0-9a-fA-F]{64}", actual
        ):
            return "unknown"
        if pinned.lower() == actual.lower():
            return "confirmed"
        scope = f"repository:{self.image}:pull"
        async with httpx.AsyncClient(follow_redirects=False) as client:
            token = await self._get_bearer_token(client, scope)
            todo, seen = [(pinned.lower(), 0)], set()
            incomplete = False
            while todo and len(seen) < 8:
                digest, depth = todo.pop(0)
                if digest in seen:
                    continue
                seen.add(digest)
                response, token = await self._request_manifest(
                    client,
                    "GET",
                    f"{self._registry_url()}/v2/{self.image}/manifests/{digest}",
                    token,
                    scope,
                )
                if (
                    response.status_code != 200
                    or len(response.content) > 2_000_000
                    or "sha256:" + hashlib.sha256(response.content).hexdigest() != digest
                ):
                    return "unknown"
                try:
                    body = response.json()
                except ValueError:
                    return "unknown"
                if not isinstance(body, dict) or body.get("schemaVersion") != 2:
                    return "unknown"
                if not _is_manifest_index(response.headers.get("Content-Type", ""), body):
                    if body.get("mediaType") not in {
                        "application/vnd.oci.image.manifest.v1+json",
                        "application/vnd.docker.distribution.manifest.v2+json",
                    }:
                        return "unknown"
                    continue
                children = body.get("manifests")
                if not isinstance(children, list) or len(children) > 100:
                    return "unknown"
                for child in children:
                    if not isinstance(child, dict):
                        return "unknown"
                    media = child.get("mediaType", "")
                    child_digest = child.get("digest", "")
                    platform = child.get("platform") or {}
                    if (
                        not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", child_digest)
                        or (child.get("annotations") or {}).get("vnd.docker.reference.type")
                        == "attestation-manifest"
                    ):
                        continue
                    if (
                        media
                        in {
                            "application/vnd.oci.image.manifest.v1+json",
                            "application/vnd.docker.distribution.manifest.v2+json",
                        }
                        and platform.get("os") not in {None, "unknown"}
                        and platform.get("architecture") not in {None, "unknown"}
                    ):
                        if child_digest.lower() == actual.lower():
                            return "confirmed"
                    elif "image.index" in media or "manifest.list" in media:
                        if depth < 2:
                            todo.append((child_digest.lower(), depth + 1))
                        else:
                            incomplete = True
                    else:
                        incomplete = True
            return "unknown" if todo or incomplete else "superseded"

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
                    return (
                        None,
                        None,
                        f"manifest_head_http_{head_status}",
                        current_token,
                    )
                else:
                    digest = head_resp.headers.get("Docker-Content-Digest")
                    media_type = head_resp.headers.get("Content-Type")
                    if digest:
                        from .registry_metadata import registry_date

                        modified = registry_date(head_resp.headers.get("Last-Modified"))
                        if modified is not None:
                            self._manifest_modified[(tag, digest.lower())] = modified
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
                        return (
                            None,
                            None,
                            f"manifest_get_http_{get_status}",
                            current_token,
                        )
                    else:
                        digest = get_resp.headers.get("Docker-Content-Digest")
                        media_type = get_resp.headers.get("Content-Type")
                        if digest:
                            from .registry_metadata import registry_date

                            modified = registry_date(get_resp.headers.get("Last-Modified"))
                            if modified is not None:
                                self._manifest_modified[(tag, digest.lower())] = modified
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

    def _tag_to_release(self, tag: str) -> Release:
        """Convert a tag into a Release object"""
        return Release(
            tracker_name=self.name,
            tracker_type="container",
            name=tag,
            tag_name=tag,
            version=tag,
            published_at=None,
            url=f"https://{self.registry}/{self.image}:{tag}",
            prerelease=False,
        )

    async def _enrich_registry_push_times(self, releases: list[Release]) -> None:
        """Optional digest-bound native dates; failures never block manifest fetching."""
        from .registry_metadata import enrich_registry_times

        try:
            await enrich_registry_times(
                self,
                releases,
                budget_seconds=min(self.timeout, _REGISTRY_PUSH_TIME_BUDGET_SECONDS),
                max_requests=_REGISTRY_PUSH_TIME_REQUEST_LIMIT,
            )
        except (TimeoutError, httpx.HTTPError, ValueError) as exc:
            logger.debug(
                "DockerTracker: optional registry time unavailable (%s)", type(exc).__name__
            )

    async def fetch_latest(self, fallback_tags: bool = False) -> Release | None:
        releases = await self.fetch_all(
            limit=1, fallback_tags=fallback_tags, incremental_alias_scan=False
        )
        return releases[0] if releases else None

    async def fetch_all(
        self,
        limit: int = 10,
        fallback_tags: bool = False,
        *,
        incremental_alias_scan: bool = True,
    ) -> list[Release]:
        """Fetch a bounded set of matching tags and their manifest digests.

        No Config blobs or build metadata are read. Observation times are only
        used to schedule bounded incremental probes, never to rank versions.
        """
        self.last_complete_tags = None
        scope = f"repository:{self.image}:pull"
        self._manifest_modified.clear()

        logger.info(
            f"DockerTracker: Fetching tags from {self.registry}/{self.image} (limit={limit})"
        )

        async with httpx.AsyncClient(follow_redirects=False) as client:
            # 1. Fetch Bearer token
            bearer_token = await self._get_bearer_token(client, scope)

            # 2. Fetch tag list
            try:
                all_tags = await self._fetch_tags(client, bearer_token)
                self.last_complete_tags = tuple(all_tags)
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

            from ..versioning import (
                container_channel_eligible,
                container_tag_eligible,
                release_order_key,
            )

            releases = [release for release in releases if self._should_include(release)]
            channels = self.config.get("channels", [])
            candidate_groups = (
                [
                    [
                        release
                        for release in releases
                        if self.should_include_in_channel(release, channel)
                        and container_channel_eligible(
                            release.tag_name, self.version_format, channel
                        )
                    ]
                    for channel in channels
                ]
                if channels
                else [
                    [
                        release
                        for release in releases
                        if container_tag_eligible(release.tag_name, self.version_format)
                    ]
                ]
            )
            selection_priority = list(
                dict.fromkeys(
                    max(group, key=release_order_key).tag_name
                    for group in candidate_groups
                    if group
                )
            )
            if incremental_alias_scan:
                selection_priority.sort(
                    key=lambda tag: (
                        self._alias_last_observed[tag].isoformat()
                        if self._alias_last_observed and tag in self._alias_last_observed
                        else ""
                    )
                )

            candidate_window = _select_incremental_probe_window(
                releases,
                limit,
                self._alias_last_observed if incremental_alias_scan else None,
                priority_aliases=(
                    *selection_priority,
                    *(self._priority_aliases if incremental_alias_scan else ()),
                ),
            )
            candidates_by_tag = {release.tag_name: release for release in releases}
            candidate_tags = [release.tag_name for release in candidate_window]
            priority_tags: list[str] = []
            processed_tags: set[str] = set()
            selected_releases: list[Release] = []

            def prioritize_tag(tag: str | None) -> None:
                if (
                    tag
                    and tag in candidates_by_tag
                    and tag not in processed_tags
                    and tag not in priority_tags
                ):
                    priority_tags.insert(0, tag)

            while len(selected_releases) < limit and (priority_tags or candidate_tags):
                candidate_tag = priority_tags.pop(0) if priority_tags else candidate_tags.pop(0)
                if candidate_tag in processed_tags:
                    continue
                candidate = candidates_by_tag[candidate_tag]
                processed_tags.add(candidate_tag)
                selected_releases.append(candidate)

                digest, _, error_reason, bearer_token = await self._resolve_manifest_digest(
                    client,
                    candidate.tag_name,
                    bearer_token,
                    scope,
                )
                if digest:
                    candidate.commit_sha = digest
                    normalized_digest = digest.lower()
                    previous_digest = self._alias_digest_by_name.get(candidate.tag_name)
                    if previous_digest and previous_digest != normalized_digest:
                        sibling_tags = [
                            alias
                            for alias, sibling_digest in self._alias_digest_by_name.items()
                            if sibling_digest == previous_digest and alias != candidate.tag_name
                        ]
                        for sibling_tag in reversed(sibling_tags):
                            prioritize_tag(sibling_tag)

                    continue

                candidate.candidate_confirmed = False
                # Digest resolution failed. We still emit the candidate so
                # first-time tags can get tracked; the storage layer is
                # Storage retains the last known digest when an optional probe fails.
                logger.warning(
                    "DockerTracker: digest resolution failed, keep candidate without digest "
                    "(image=%s, tag=%s, reason=%s)",
                    self.image,
                    candidate.tag_name,
                    error_reason or "unknown",
                )

            await self._enrich_registry_push_times(selected_releases)
            return selected_releases


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


def _is_likely_floating_alias(tag: str) -> bool:
    if tag.lower() in _FLOATING_ALIAS_NAMES:
        return True
    version_parts = _version_parts(tag)
    if version_parts is None:
        return False
    suffix = version_parts[4]
    return not suffix or _COMMIT_SUFFIX_RE.search(suffix) is None


def _select_incremental_probe_window(
    releases: list[Release],
    limit: int,
    alias_last_observed: dict[str, datetime] | None,
    *,
    priority_aliases: Iterable[str] = (),
) -> list[Release]:
    """Select at most ``limit`` aliases while eventually reconciling the full tag set.

    Exact aliases supplied by an authenticated event are checked first but still consume
    the same fixed budget. A missing observation state otherwise preserves the historical
    ranked-window behaviour. With persisted state, never-observed tags are visited first.
    Once all tags have been seen, common floating aliases receive a small reserved slice
    and the remaining budget rotates through the least recently observed aliases.
    """
    if limit <= 0:
        return []

    ranked = list(releases)
    releases_by_tag = {release.tag_name: release for release in ranked}
    selected: list[Release] = []
    selected_tags: set[str] = set()
    for alias in priority_aliases:
        release = releases_by_tag.get(alias)
        if release is None or release.tag_name in selected_tags:
            continue
        selected.append(release)
        selected_tags.add(release.tag_name)
        if len(selected) >= limit:
            return selected

    if alias_last_observed is None:
        selected.extend(release for release in ranked if release.tag_name not in selected_tags)
        return selected[:limit]

    rank_by_tag = {release.tag_name: rank for rank, release in enumerate(ranked)}
    unseen = [
        release
        for release in ranked
        if release.tag_name not in alias_last_observed and release.tag_name not in selected_tags
    ]
    observed = [
        release
        for release in ranked
        if release.tag_name in alias_last_observed and release.tag_name not in selected_tags
    ]

    def by_oldest_observation(release: Release) -> tuple[str, int]:
        return (
            alias_last_observed[release.tag_name].isoformat(),
            rank_by_tag[release.tag_name],
        )

    if unseen:
        # Keep one slot for the stalest mutable-looking alias so a republished
        # floating tag cannot be starved by a large backlog of unseen history.
        rotating_floating = sorted(
            (release for release in observed if _is_likely_floating_alias(release.tag_name)),
            key=by_oldest_observation,
        )
        if rotating_floating:
            selected.append(rotating_floating[0])
            selected_tags.add(rotating_floating[0].tag_name)
        for release in unseen:
            if len(selected) >= limit:
                return selected
            selected.append(release)
            selected_tags.add(release.tag_name)

    floating_budget = min(3, max(1, limit // 3))
    named_floating = [
        release
        for release in observed
        if release.tag_name.lower() in _FLOATING_ALIAS_NAMES
        and release.tag_name not in selected_tags
    ]
    for release in named_floating[:floating_budget]:
        if len(selected) >= limit:
            return selected
        selected.append(release)
        selected_tags.add(release.tag_name)

    remaining = [release for release in observed if release.tag_name not in selected_tags]
    remaining.sort(key=by_oldest_observation)
    selected.extend(remaining[: limit - len(selected)])
    return selected


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
