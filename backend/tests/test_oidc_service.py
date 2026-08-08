import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from authlib.jose import JsonWebKey, JsonWebToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from releasetracker.oidc_models import OIDCIdentity, OIDCProvider, OAuthState
from releasetracker.services.oidc_service import OIDCService

ISSUER = "https://issuer.example.com"
CLIENT_ID = "release-tracker"
NONCE = "expected-nonce"


def _signing_key(kid: str = "test-key"):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return JsonWebKey.import_key(private_pem, {"kid": kid})


def _id_token(key, **overrides) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "iss": ISSUER,
        "sub": "admin-subject",
        "aud": CLIENT_ID,
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "iat": int(now.timestamp()),
        "nonce": NONCE,
    }
    claims.update(overrides)
    return (
        JsonWebToken(["RS256"])
        .encode({"alg": "RS256", "kid": key.as_dict()["kid"]}, claims, key)
        .decode()
    )


def _unsigned_id_token() -> str:
    now = datetime.now(timezone.utc)
    header = {"alg": "none", "typ": "JWT"}
    claims = {
        "iss": ISSUER,
        "sub": "admin-subject",
        "aud": CLIENT_ID,
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "iat": int(now.timestamp()),
        "nonce": NONCE,
    }

    def encode(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{encode(header)}.{encode(claims)}."


def _provider() -> OIDCProvider:
    return OIDCProvider(
        name="Provider",
        slug="provider",
        issuer_url=ISSUER,
        discovery_enabled=False,
        client_id=CLIENT_ID,
        authorization_url=f"{ISSUER}/authorize",
        token_url=f"{ISSUER}/token",
        jwks_uri=f"{ISSUER}/jwks",
    )


def _state(flow_type: str, admin_user_id: int | None = None) -> OAuthState:
    return OAuthState(
        state="state",
        provider_slug="provider",
        code_verifier="verifier",
        nonce=NONCE,
        flow_type=flow_type,
        initiating_admin_user_id=admin_user_id,
        expires_at=datetime.now() + timedelta(minutes=5),
    )


async def _user_count(storage) -> int:
    db = await storage._get_connection()
    row = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
    return row[0]


@pytest.mark.asyncio
async def test_validate_id_token_accepts_signed_exact_claims(storage, auth_service, monkeypatch):
    service = OIDCService(storage, auth_service)
    key = _signing_key()

    async def fetch_jwks(_uri):
        return {"keys": [key.as_dict(is_private=False)]}

    monkeypatch.setattr(service, "_fetch_jwks", fetch_jwks)
    identity = await service._validate_id_token(_id_token(key), _provider(), NONCE)
    assert identity == OIDCIdentity(issuer=ISSUER, subject="admin-subject")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim_overrides", "expected_nonce"),
    [
        ({"iss": "https://wrong.example.com"}, NONCE),
        ({"aud": "wrong-client"}, NONCE),
        ({"nonce": "wrong-nonce"}, NONCE),
        ({"exp": 1}, NONCE),
        ({"sub": ""}, NONCE),
    ],
)
async def test_validate_id_token_rejects_claim_mismatch(
    storage, auth_service, monkeypatch, claim_overrides, expected_nonce
):
    service = OIDCService(storage, auth_service)
    key = _signing_key()

    async def fetch_jwks(_uri):
        return {"keys": [key.as_dict(is_private=False)]}

    monkeypatch.setattr(service, "_fetch_jwks", fetch_jwks)
    with pytest.raises(ValueError):
        await service._validate_id_token(
            _id_token(key, **claim_overrides), _provider(), expected_nonce
        )


@pytest.mark.asyncio
async def test_validate_id_token_rejects_alg_none(storage, auth_service, monkeypatch):
    service = OIDCService(storage, auth_service)
    key = _signing_key()

    async def fetch_jwks(_uri):
        return {"keys": [key.as_dict(is_private=False)]}

    monkeypatch.setattr(service, "_fetch_jwks", fetch_jwks)
    with pytest.raises(ValueError, match="validation failed"):
        await service._validate_id_token(_unsigned_id_token(), _provider(), NONCE)


@pytest.mark.asyncio
async def test_validate_id_token_rejects_bad_signature(storage, auth_service, monkeypatch):
    service = OIDCService(storage, auth_service)
    signing_key = _signing_key("shared-kid")
    unrelated_key = _signing_key("shared-kid")

    async def fetch_jwks(_uri):
        return {"keys": [unrelated_key.as_dict(is_private=False)]}

    monkeypatch.setattr(service, "_fetch_jwks", fetch_jwks)
    with pytest.raises(ValueError, match="validation failed"):
        await service._validate_id_token(_id_token(signing_key), _provider(), NONCE)


@pytest.mark.asyncio
async def test_callback_rejects_state_for_another_provider_before_exchange(storage, auth_service):
    service = OIDCService(storage, auth_service)
    with pytest.raises(ValueError, match="state provider"):
        await service.handle_callback(
            provider_slug="other-provider",
            code="code",
            redirect_uri="https://app.example.com/callback",
            oauth_state=_state("login"),
        )


@pytest.mark.asyncio
async def test_exact_binding_resolves_existing_admin_without_provisioning(storage, auth_service):
    service = OIDCService(storage, auth_service)
    admin_user_id = await storage.get_admin_user_id()
    assert admin_user_id is not None
    before_count = await _user_count(storage)
    identity = OIDCIdentity(issuer=ISSUER, subject="admin-subject")

    bound_user = await service._resolve_admin_identity(identity, _state("bind", admin_user_id))
    login_user = await service._resolve_admin_identity(identity, _state("login"))

    assert bound_user.id == admin_user_id
    assert login_user.id == admin_user_id
    assert await storage.get_admin_oidc_binding() == (ISSUER, "admin-subject")
    assert await _user_count(storage) == before_count
    persisted = await storage.get_user_by_id(admin_user_id)
    assert persisted is not None
    assert persisted.username == "admin"
    assert persisted.password_hash is not None
    assert persisted.oauth_provider is None
    assert persisted.oauth_sub is None


@pytest.mark.asyncio
async def test_legacy_user_oauth_columns_do_not_create_admin_binding(storage, auth_service):
    service = OIDCService(storage, auth_service)
    admin_user_id = await storage.get_admin_user_id()
    assert admin_user_id is not None
    db = await storage._get_connection()
    await db.execute(
        "UPDATE users SET oauth_provider = ?, oauth_sub = ? WHERE id = ?",
        ("provider", "admin-subject", admin_user_id),
    )
    await db.commit()

    assert await storage.get_admin_oidc_binding() is None
    with pytest.raises(ValueError, match="not bound"):
        await service._resolve_admin_identity(
            OIDCIdentity(issuer=ISSUER, subject="admin-subject"),
            _state("login"),
        )


@pytest.mark.asyncio
async def test_unbound_and_mismatched_oidc_identity_fail_without_provisioning(
    storage, auth_service
):
    service = OIDCService(storage, auth_service)
    before_count = await _user_count(storage)
    before_sessions = await storage.count_active_sessions()

    with pytest.raises(ValueError, match="not bound"):
        await service._resolve_admin_identity(
            OIDCIdentity(issuer=ISSUER, subject="admin-subject"),
            _state("login"),
        )

    await storage.bind_admin_oidc_identity(ISSUER, "admin-subject")
    with pytest.raises(ValueError, match="not bound"):
        await service._resolve_admin_identity(
            OIDCIdentity(issuer=ISSUER, subject="attacker-subject"),
            _state("login"),
        )

    assert await _user_count(storage) == before_count
    assert await storage.count_active_sessions() == before_sessions
