import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tradingng_platform.auth.oidc import OidcVerifier

ISSUER = "https://issuer.example/realms/tradingng"
AUDIENCE = "tradingng-api"
KEY_ID = "test-key"


def _encode_token(private_key, **overrides):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "alice-sub",
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 300,
        "scope": "assessments:read assessments:submit",
        "realm_access": {"roles": ["Analyst"]},
        "name": "Alice",
        "email": "alice@example.com",
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": KEY_ID})


@pytest.fixture
def oidc_server():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    public_jwk["kid"] = KEY_ID
    request_counts = {"discovery": 0, "jwks": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            request_counts["discovery"] += 1
            return httpx.Response(
                200,
                json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/keys"},
            )
        if request.url.path.endswith("/keys"):
            request_counts["jwks"] += 1
            return httpx.Response(200, json={"keys": [public_jwk]})
        return httpx.Response(404)

    return private_key, httpx.MockTransport(handle), request_counts


async def test_verify_maps_principal_and_caches_jwks(oidc_server):
    private_key, transport, request_counts = oidc_server
    token = _encode_token(private_key)
    async with httpx.AsyncClient(transport=transport) as client:
        verifier = OidcVerifier(ISSUER, AUDIENCE, client=client)

        principal = await verifier.verify(token)
        await verifier.verify(token)

    assert principal.subject == "alice-sub"
    assert principal.scopes == frozenset({"assessments:read", "assessments:submit"})
    assert principal.roles == frozenset({"Analyst"})
    assert request_counts == {"discovery": 1, "jwks": 1}


@pytest.mark.parametrize(
    ("role", "expected_scopes"),
    [
        (
            "Viewer",
            {
                "assessments:read",
                "validations:read",
                "system:read",
                "artifacts:read",
            },
        ),
        (
            "Analyst",
            {
                "assessments:read",
                "assessments:submit",
                "assessments:cancel",
                "validations:read",
                "validations:write",
                "system:read",
                "artifacts:read",
            },
        ),
        (
            "Admin",
            {
                "assessments:read",
                "assessments:submit",
                "assessments:cancel",
                "assessments:admin",
                "validations:read",
                "validations:write",
                "system:read",
                "artifacts:read",
            },
        ),
    ],
)
async def test_human_scopes_are_bounded_by_realm_role(oidc_server, role, expected_scopes):
    private_key, transport, _ = oidc_server
    all_scopes = {
        "assessments:read",
        "assessments:submit",
        "assessments:cancel",
        "assessments:admin",
        "validations:read",
        "validations:write",
        "system:read",
        "artifacts:read",
    }
    token = _encode_token(
        private_key,
        scope=" ".join(sorted(all_scopes)),
        realm_access={"roles": [role]},
    )
    async with httpx.AsyncClient(transport=transport) as client:
        principal = await OidcVerifier(ISSUER, AUDIENCE, client=client).verify(token)

    assert principal.actor_type == "user"
    assert principal.scopes == frozenset(expected_scopes)


async def test_service_account_scopes_are_not_role_bounded(oidc_server):
    private_key, transport, _ = oidc_server
    token = _encode_token(
        private_key,
        email=None,
        azp="tradingng-mcp",
        scope="assessments:submit system:read",
        realm_access={},
    )
    async with httpx.AsyncClient(transport=transport) as client:
        principal = await OidcVerifier(ISSUER, AUDIENCE, client=client).verify(token)

    assert principal.actor_type == "service"
    assert principal.scopes == frozenset({"assessments:submit", "system:read"})


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "wrong-audience"},
        {"exp": int(time.time()) - 60},
    ],
)
async def test_verify_rejects_invalid_claims(oidc_server, claims):
    private_key, transport, _ = oidc_server
    token = _encode_token(private_key, **claims)
    async with httpx.AsyncClient(transport=transport) as client:
        verifier = OidcVerifier(ISSUER, AUDIENCE, client=client)

        with pytest.raises(jwt.InvalidTokenError):
            await verifier.verify(token)


async def test_verify_requires_exactly_one_matching_key(oidc_server):
    private_key, transport, _ = oidc_server
    token = jwt.encode(
        {
            "iss": ISSUER,
            "sub": "alice-sub",
            "aud": AUDIENCE,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "unknown-key"},
    )
    async with httpx.AsyncClient(transport=transport) as client:
        verifier = OidcVerifier(ISSUER, AUDIENCE, client=client)

        with pytest.raises(jwt.InvalidTokenError, match="matching signing key"):
            await verifier.verify(token)
