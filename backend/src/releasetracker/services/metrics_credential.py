"""Metrics scrape verifier and admin-readable encrypted token."""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..storage.sqlite import SQLiteStorage

METRICS_TOKEN_SETTING_KEY = "system.metrics_token_digest"
MIN_TOKEN_LENGTH = 16
MAX_TOKEN_LENGTH = 512
_DIGEST_LENGTH = 64
_HEX_DIGITS = frozenset("0123456789abcdef")


def encode_token(token: str) -> str:
    """Return the persisted verifier for a token, or an empty string to revoke it.

    The error message never repeats the rejected credential.
    """
    if not isinstance(token, str):
        raise ValueError("Metrics token must be text")
    if not token:
        return ""
    if (
        not token.isascii()
        or not token.isprintable()
        or any(character.isspace() for character in token)
        or not MIN_TOKEN_LENGTH <= len(token) <= MAX_TOKEN_LENGTH
    ):
        raise ValueError(
            f"Metrics token must be {MIN_TOKEN_LENGTH}-{MAX_TOKEN_LENGTH} "
            "printable ASCII characters without whitespace"
        )
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def matches(digest: str, supplied: str) -> bool:
    """Compare a presented token with the stored verifier in constant time."""
    if not digest or not supplied or not supplied.isascii():
        return False
    return hmac.compare_digest(hashlib.sha256(supplied.encode("ascii")).hexdigest(), digest)


async def get_digest(storage: "SQLiteStorage") -> str:
    """Return the stored verifier, or an empty string when scraping is disabled.

    A revoked credential keeps its row with an empty digest, so a later legacy
    import cannot resurrect a token the administrator deliberately disabled.
    """
    record = await storage.get_setting(METRICS_TOKEN_SETTING_KEY) or ""
    digest = record.partition(":")[0]
    if len(digest) != _DIGEST_LENGTH or not _HEX_DIGITS.issuperset(digest):
        return ""
    return digest


async def read_token(storage: "SQLiteStorage") -> tuple[bool, str | None]:
    async with storage.encryption_rotation_lock:
        record = await storage.get_setting(METRICS_TOKEN_SETTING_KEY) or ""
        digest, _, encrypted = record.partition(":")
        if not encrypted:
            # Old installations and environment imports only have a verifier.
            return len(digest) == _DIGEST_LENGTH and _HEX_DIGITS.issuperset(digest), None
        token = storage.fernet.decrypt(encrypted.encode("ascii")).decode("ascii")
        if not matches(digest, token):
            raise RuntimeError("Metrics credential verifier mismatch")
        return True, token


async def save_token(storage: "SQLiteStorage", token: str) -> bool:
    """Keep the verifier and ciphertext together so replacement and revocation are atomic."""
    digest = encode_token(token)
    async with storage.encryption_rotation_lock:
        record = ""
        if token:
            encrypted = storage.fernet.encrypt(token.encode("ascii")).decode("ascii")
            record = f"{digest}:{encrypted}"
        await storage.set_setting(METRICS_TOKEN_SETTING_KEY, record)
    return bool(digest)
