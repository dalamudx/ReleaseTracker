"""OIDC authentication data models"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from .services.secure_urls import require_https_url


class OIDCProvider(BaseModel):
    """OIDC provider configuration"""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    name: str  # Display name
    slug: str  # URL slug (unique key)

    # OIDC Discovery
    issuer_url: str | None = None
    discovery_enabled: bool = True

    # Client credentials
    client_id: str
    client_secret: str | None = None  # Stored encrypted and never returned externally

    # Endpoints configured manually or discovered automatically
    authorization_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    jwks_uri: str | None = None

    # Configuration
    scopes: str = "openid email profile"
    enabled: bool = True

    # Metadata
    icon_url: str | None = None
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator(
        "issuer_url",
        "authorization_url",
        "token_url",
        "userinfo_url",
        "jwks_uri",
    )
    @classmethod
    def validate_security_sensitive_url(cls, value: str | None, info):
        if value is None:
            return None
        return require_https_url(value, field=f"OIDC {info.field_name}")


class OAuthState(BaseModel):
    """Temporary OAuth state storage for a validated login or admin binding flow."""

    state: str
    provider_slug: str
    code_verifier: str
    nonce: str
    flow_type: Literal["login", "bind"]
    initiating_admin_user_id: int | None = None
    browser_binding_hash: str
    expires_at: datetime


class OIDCIdentity(BaseModel):
    """Cryptographically validated stable OIDC identity."""

    issuer: str
    subject: str


class OIDCUserInfo(BaseModel):
    """OIDC user information from the userinfo endpoint or ID token"""

    sub: str  # Subject（unique identifier）
    email: str
    email_verified: bool = False
    name: str | None = None
    preferred_username: str | None = None
    picture: str | None = None
    provider_slug: str
