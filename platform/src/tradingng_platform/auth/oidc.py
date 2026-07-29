import asyncio
import time
from typing import Any

import httpx
import jwt

from tradingng_platform.auth.principal import Principal

FORMAL_ROLES = frozenset({"Admin", "User"})
USER_SCOPES = frozenset(
    {
        "assessments:read",
        "assessments:submit",
        "assessments:cancel",
        "assessments:review",
        "validations:read",
        "validations:write",
        "artifacts:read",
    }
)
ADMIN_SCOPES = USER_SCOPES | frozenset(
    {
        "assessments:admin",
        "system:read",
        "users:manage",
    }
)
ROLE_SCOPES = {
    "User": USER_SCOPES,
    "Admin": ADMIN_SCOPES,
}


def _human_scopes(token_scopes: frozenset[str], roles: frozenset[str]) -> frozenset[str]:
    allowed: set[str] = set()
    for role in roles:
        allowed.update(ROLE_SCOPES.get(role, ()))
    return token_scopes & allowed


class OidcVerifier:
    def __init__(
        self,
        issuer: str,
        audience: str,
        ttl_seconds: int = 300,
        client: httpx.AsyncClient | None = None,
    ):
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.ttl_seconds = ttl_seconds
        self.client = client
        self._jwks: dict[str, Any] | None = None
        self._expires_at = 0.0
        self._cache_lock = asyncio.Lock()

    async def _get_json(self, url: str) -> dict[str, Any]:
        if self.client is not None:
            response = await self.client.get(url)
        else:
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
        response.raise_for_status()
        document = response.json()
        if not isinstance(document, dict):
            raise jwt.InvalidTokenError("OIDC endpoint did not return an object")
        return document

    async def _load_jwks(self) -> dict[str, Any]:
        if self._jwks is not None and time.monotonic() < self._expires_at:
            return self._jwks

        async with self._cache_lock:
            if self._jwks is not None and time.monotonic() < self._expires_at:
                return self._jwks

            discovery = await self._get_json(f"{self.issuer}/.well-known/openid-configuration")
            if discovery.get("issuer") != self.issuer:
                raise jwt.InvalidTokenError("OIDC discovery issuer does not match")
            jwks_uri = discovery.get("jwks_uri")
            if not isinstance(jwks_uri, str) or not jwks_uri:
                raise jwt.InvalidTokenError("OIDC discovery is missing jwks_uri")

            jwks = await self._get_json(jwks_uri)
            if not isinstance(jwks.get("keys"), list):
                raise jwt.InvalidTokenError("JWKS document is missing keys")

            self._jwks = jwks
            self._expires_at = time.monotonic() + self.ttl_seconds
            return jwks

    async def verify(self, token: str) -> Principal:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise jwt.InvalidTokenError("token header is missing kid")

        jwks = await self._load_jwks()
        matching_keys = [
            key
            for key in jwks["keys"]
            if isinstance(key, dict) and key.get("kid") == kid and key.get("use", "sig") == "sig"
        ]
        if len(matching_keys) != 1:
            raise jwt.InvalidTokenError("token must have exactly one matching signing key")

        public_key = jwt.PyJWK.from_dict(matching_keys[0]).key
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256", "ES256"],
            audience=self.audience,
            issuer=self.issuer,
            options={"require": ["exp", "iat", "iss", "sub", "aud"]},
        )
        scopes = frozenset(str(claims.get("scope", "")).split())
        realm_access = claims.get("realm_access", {})
        raw_roles = realm_access.get("roles", ()) if isinstance(realm_access, dict) else ()
        roles = frozenset(str(role) for role in raw_roles)
        client_identifier = claims.get("client_id", claims.get("azp"))
        has_user_session = any(key in claims for key in ("sid", "session_state", "auth_time"))
        actor_type = (
            "service"
            if isinstance(client_identifier, str)
            and not claims.get("email")
            and not has_user_session
            else "user"
        )
        if actor_type == "user":
            scopes = _human_scopes(scopes, roles)
        return Principal(
            issuer=self.issuer,
            subject=claims["sub"],
            actor_type=actor_type,
            scopes=scopes,
            display_name=claims.get("name", claims.get("preferred_username", "")),
            email=claims.get("email"),
            roles=roles,
        )
