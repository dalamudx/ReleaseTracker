"""OIDC authentication data models"""

from datetime import datetime
from pydantic import BaseModel


class OIDCProvider(BaseModel):
    """OIDC provider configuration"""

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


class OAuthState(BaseModel):
    """Temporary OAuth state storage to prevent CSRF"""

    state: str
    provider_slug: str
    code_verifier: str  # PKCE code_verifier
    expires_at: datetime


class OIDCUserInfo(BaseModel):
    """OIDC user information from the userinfo endpoint or ID token"""

    sub: str  # Subject（unique identifier）
    email: str
    email_verified: bool = False
    name: str | None = None
    preferred_username: str | None = None
    picture: str | None = None
    provider_slug: str
