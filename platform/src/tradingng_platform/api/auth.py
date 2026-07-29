from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request

from tradingng_platform.api.errors import ApiError
from tradingng_platform.auth.principal import Principal


async def current_principal(request: Request) -> Principal:
    authorization = request.headers.get("Authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token.strip():
        raise ApiError(401, "authentication_required", "Bearer token is required")
    token = token.strip()
    try:
        if token.startswith("tng_"):
            return await request.app.state.api_tokens.verify(token)
        principal = await request.app.state.oidc.verify(token)
        identity_access = getattr(request.app.state, "identity_access", None)
        if identity_access is None:
            from tradingng_platform.identity.access import IdentityAccessService
            from tradingng_platform.identity.repository import IdentityRepository

            identity_access = IdentityAccessService(
                IdentityRepository(request.app.state.database.sessions)
            )
        return await identity_access.enforce(principal)
    except ApiError:
        raise
    except Exception:
        raise ApiError(401, "invalid_token", "Bearer token is invalid or expired") from None


def require_scopes(*scopes: str) -> Callable:
    async def dependency(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> Principal:
        try:
            principal.require(*scopes)
        except PermissionError:
            raise ApiError(403, "insufficient_scope", "Required scope is missing") from None
        return principal

    return dependency


def require_admin_scope(scope: str = "users:manage") -> Callable:
    async def dependency(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> Principal:
        if "Admin" not in principal.roles or scope not in principal.scopes:
            raise ApiError(403, "insufficient_scope", "Administrator permission is required")
        return principal

    return dependency
