"""Redirect-safe HTTP requests that carry configured credentials."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from .secure_urls import require_https_url, same_origin_https

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5


async def credentialed_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    max_redirects: int = _MAX_REDIRECTS,
    **kwargs: Any,
) -> httpx.Response:
    """Send credentials only over HTTPS and across same-origin HTTPS redirects."""
    current_url = require_https_url(url, field="Credentialed request URL")
    initial_url = current_url
    visited: set[str] = set()
    current_method = method.upper()
    request_kwargs = dict(kwargs)

    for redirect_count in range(max_redirects + 1):
        if current_url in visited:
            raise ValueError("Credentialed request redirect loop detected")
        visited.add(current_url)

        response = await client.request(
            current_method,
            current_url,
            headers=headers,
            follow_redirects=False,
            **request_kwargs,
        )
        if response.status_code not in _REDIRECT_STATUSES:
            return response

        if redirect_count >= max_redirects:
            raise ValueError("Credentialed request exceeded the redirect limit")
        location = response.headers.get("location")
        if not location:
            raise ValueError("Credentialed request redirect is missing Location")

        next_url = urljoin(current_url, location)
        require_https_url(next_url, field="Credentialed redirect URL")
        if not same_origin_https(initial_url, next_url):
            raise ValueError("Credentialed request refused a cross-origin redirect")

        if response.status_code == 303 and current_method != "HEAD":
            current_method = "GET"
            request_kwargs.pop("data", None)
            request_kwargs.pop("json", None)
            request_kwargs.pop("content", None)
        current_url = next_url

    raise ValueError("Credentialed request exceeded the redirect limit")
