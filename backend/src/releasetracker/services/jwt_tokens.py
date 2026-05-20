"""JWT helpers for ReleaseTracker access and refresh tokens."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import OctKey
from joserfc.jwt import JWTClaimsRegistry

ALGORITHM = "HS256"


class JWTTokenError(ValueError):
    """Raised when a JWT cannot be decoded or validated."""


def encode_jwt(payload: dict[str, Any], secret_key: str) -> str:
    """Encode a signed JWT with the configured symmetric key."""
    claims = payload.copy()
    exp = claims.get("exp")
    if isinstance(exp, datetime):
        claims["exp"] = int(exp.astimezone(timezone.utc).timestamp())
    return jwt.encode(
        {"alg": ALGORITHM}, claims, OctKey.import_key(secret_key), algorithms=[ALGORITHM]
    )


def decode_jwt(token: str, secret_key: str) -> dict[str, Any]:
    """Decode and validate a signed JWT with the configured symmetric key."""
    try:
        decoded = jwt.decode(token, OctKey.import_key(secret_key), algorithms=[ALGORITHM])
        JWTClaimsRegistry().validate(decoded.claims)
    except JoseError as exc:
        raise JWTTokenError("Invalid token") from exc
    return dict(decoded.claims)
