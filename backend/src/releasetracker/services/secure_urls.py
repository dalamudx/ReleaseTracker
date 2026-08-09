"""Strict URL validation for security-sensitive public and upstream endpoints."""

from __future__ import annotations

import re
from urllib.parse import SplitResult, urlsplit, urlunsplit

_ENCODED_PATH_DELIMITER_RE = re.compile(r"%(?:2e|2f|5c)", re.IGNORECASE)


def _parse_https_url(value: str, *, field: str) -> tuple[str, SplitResult]:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    if "\\" in normalized or any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in normalized
    ):
        raise ValueError(f"{field} must be a valid absolute HTTPS URL")

    try:
        parsed = urlsplit(normalized)
        # Accessing port forces urllib to reject malformed ports.
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid absolute HTTPS URL") from exc

    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(f"{field} must be a valid absolute HTTPS URL")
    return normalized, parsed


def require_https_url(value: str, *, field: str = "URL") -> str:
    """Return a stripped absolute HTTPS URL or raise ``ValueError``."""
    normalized, _ = _parse_https_url(value, field=field)
    return normalized


def require_canonical_https_base_url(value: str) -> str:
    """Validate and normalize the canonical public application base URL."""
    _, parsed = _parse_https_url(value, field="System BASE URL")
    if parsed.query:
        raise ValueError("System BASE URL must not contain a query or fragment")

    path = parsed.path.rstrip("/")
    path_segments = path.split("/")
    if (
        "//" in path
        or _ENCODED_PATH_DELIMITER_RE.search(path)
        or any(segment in {".", ".."} for segment in path_segments)
    ):
        raise ValueError("System BASE URL path must be canonical")

    # Lower-casing the scheme/host and dropping a default port produces one stable
    # redirect origin while retaining an optional deployment sub-path.
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    netloc = host if port in {None, 443} else f"{host}:{port}"
    return urlunsplit(("https", netloc, path, "", ""))


def same_origin_https(left: str, right: str) -> bool:
    """Return whether two validated HTTPS URLs have the same effective origin."""
    _, left_parsed = _parse_https_url(left, field="Credentialed request URL")
    _, right_parsed = _parse_https_url(right, field="Credentialed redirect URL")

    def origin(parsed: SplitResult) -> tuple[str, str, int]:
        return parsed.scheme, parsed.hostname.lower(), parsed.port or 443

    return origin(left_parsed) == origin(right_parsed)
