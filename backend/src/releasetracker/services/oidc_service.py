"""OIDC authentication for the explicitly bound single administrator."""

import base64
import hashlib
import logging
import secrets
from datetime import datetime
from urllib.parse import urlencode

import httpx
from authlib.jose import JoseError, JsonWebToken

from ..models import Session, TokenPair, User
from ..oidc_models import OIDCIdentity, OIDCProvider, OAuthState
from ..services.auth import AuthService
from ..services.jwt_tokens import decode_jwt
from ..storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)

_ALLOWED_ID_TOKEN_ALGORITHMS = [
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
]
_ID_TOKEN_JWT = JsonWebToken(_ALLOWED_ID_TOKEN_ALGORITHMS)


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE verifier and SHA-256 challenge."""
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return code_verifier, code_challenge


def _require_user_id(user: User) -> int:
    if user.id is None:
        raise ValueError("OIDC administrator must have a persisted id")
    return user.id


class OIDCService:
    """Validate an OIDC identity and resolve it only to the existing administrator."""

    def __init__(self, storage: SQLiteStorage, auth_service: AuthService):
        self.storage = storage
        self.auth_service = auth_service

    async def _get_provider_endpoints(self, provider: OIDCProvider) -> OIDCProvider:
        """Resolve provider metadata and fail closed on issuer or JWKS drift."""
        if not provider.issuer_url:
            raise ValueError("OIDC provider issuer URL is required")

        if provider.discovery_enabled:
            discovery_url = f"{provider.issuer_url.rstrip('/')}/.well-known/openid-configuration"
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.get(discovery_url)
                    response.raise_for_status()
                    config = response.json()
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                logger.warning("OIDC discovery failed for provider=%s", provider.slug)
                raise ValueError("OIDC discovery failed") from exc

            if config.get("issuer") != provider.issuer_url:
                raise ValueError("OIDC discovery issuer does not match configured issuer")
            provider.authorization_url = config.get("authorization_endpoint")
            provider.token_url = config.get("token_endpoint")
            provider.jwks_uri = config.get("jwks_uri")
            logger.info("OIDC discovery succeeded for provider=%s", provider.slug)

        if not provider.authorization_url or not provider.token_url or not provider.jwks_uri:
            raise ValueError("OIDC authorization, token, and JWKS endpoints are required")
        return provider

    async def get_authorization_url(
        self,
        provider_slug: str,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        nonce: str,
    ) -> str:
        """Build a nonce- and PKCE-protected OIDC authorization URL."""
        provider = await self.storage.get_oauth_provider(provider_slug)
        if not provider or not provider.enabled:
            raise ValueError(f"Provider {provider_slug} does not exist or is disabled")
        provider = await self._get_provider_endpoints(provider)
        return f"{provider.authorization_url}?{urlencode({'response_type': 'code', 'client_id': provider.client_id, 'redirect_uri': redirect_uri, 'scope': provider.scopes, 'state': state, 'nonce': nonce, 'code_challenge': code_challenge, 'code_challenge_method': 'S256'})}"

    async def handle_callback(
        self,
        provider_slug: str,
        code: str,
        redirect_uri: str,
        oauth_state: OAuthState,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[User, TokenPair]:
        """Exchange a code, validate its ID token, and issue a local admin session."""
        if oauth_state.provider_slug != provider_slug:
            raise ValueError("OIDC state provider does not match callback provider")

        provider = await self.storage.get_oauth_provider(provider_slug)
        if not provider or not provider.enabled:
            raise ValueError(f"Provider {provider_slug} does not exist or is disabled")
        provider = await self._get_provider_endpoints(provider)

        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": provider.client_id,
            "code_verifier": oauth_state.code_verifier,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                kwargs = {
                    "data": token_data,
                    "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                }
                if provider.client_secret:
                    kwargs["auth"] = (provider.client_id, provider.client_secret)
                token_response = await http.post(str(provider.token_url), **kwargs)
                token_response.raise_for_status()
                token = token_response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("OIDC token exchange failed for provider=%s", provider.slug)
            raise ValueError("OIDC token exchange failed") from exc

        id_token = token.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise ValueError("OIDC ID token is required")

        identity = await self._validate_id_token(id_token, provider, oauth_state.nonce)
        user = await self._resolve_admin_identity(identity, oauth_state)
        token_pair = self.auth_service._create_token_pair(user)
        session = Session(
            user_id=_require_user_id(user),
            token_hash=self.auth_service._hash_token(token_pair.access_token),
            refresh_token_hash=self.auth_service._hash_token(token_pair.refresh_token),
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=datetime.fromtimestamp(
                decode_jwt(token_pair.access_token, self.auth_service.secret_key)["exp"]
            ),
        )
        await self.storage.create_session(session)
        return user, token_pair

    async def _fetch_jwks(self, jwks_uri: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                response = await http.get(jwks_uri)
                response.raise_for_status()
                jwks = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ValueError("Unable to retrieve OIDC signing keys") from exc
        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            raise ValueError("OIDC JWKS is invalid")
        return jwks

    async def _validate_id_token(
        self, id_token: str, provider: OIDCProvider, expected_nonce: str
    ) -> OIDCIdentity:
        """Validate signature and all identity-bearing OIDC claims before reading them."""
        if not provider.issuer_url or not provider.jwks_uri:
            raise ValueError("OIDC issuer and JWKS endpoint are required")
        jwks = await self._fetch_jwks(str(provider.jwks_uri))
        claims_options = {
            "iss": {"essential": True, "value": provider.issuer_url},
            "sub": {"essential": True},
            "aud": {"essential": True, "value": provider.client_id},
            "exp": {"essential": True},
            "iat": {"essential": True},
            "nonce": {"essential": True, "value": expected_nonce},
        }
        try:
            claims = _ID_TOKEN_JWT.decode(
                id_token,
                jwks,
                claims_options=claims_options,
            )
            claims.validate()
        except (JoseError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("OIDC ID token validation failed") from exc

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("OIDC subject must be non-empty")
        return OIDCIdentity(issuer=provider.issuer_url, subject=subject.strip())

    async def _resolve_admin_identity(
        self, identity: OIDCIdentity, oauth_state: OAuthState
    ) -> User:
        """Bind or compare the validated identity, then load the existing admin user."""
        try:
            admin_user_id = await self.storage.get_admin_user_id()
        except ValueError as exc:
            raise ValueError("Administrator identity is not configured") from exc
        if admin_user_id is None:
            raise ValueError("Administrator identity is not configured")

        user = await self.storage.get_user_by_id(admin_user_id)
        if user is None or user.status != "active":
            raise ValueError("Administrator account is unavailable")

        requested = (identity.issuer, identity.subject)
        if oauth_state.flow_type == "bind":
            if oauth_state.initiating_admin_user_id != admin_user_id:
                raise ValueError("OIDC binding administrator no longer matches")
            await self.storage.bind_admin_oidc_identity(*requested)
        else:
            binding = await self.storage.get_admin_oidc_binding()
            if binding is None or binding != requested:
                raise ValueError("OIDC identity is not bound to the administrator")
        return user
