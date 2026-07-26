from contextvars import ContextVar, Token

from tradingng_platform.auth.principal import Principal

_principal: ContextVar[Principal | None] = ContextVar("mcp_principal", default=None)


def set_principal(principal: Principal) -> Token:
    return _principal.set(principal)


def reset_principal(token: Token) -> None:
    _principal.reset(token)


def current_principal() -> Principal:
    principal = _principal.get()
    if principal is None:
        raise PermissionError("MCP principal is not available")
    return principal
