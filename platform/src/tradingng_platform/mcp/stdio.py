from __future__ import annotations

import asyncio
import os
import sys

from tradingng_platform.auth.oidc import OidcVerifier
from tradingng_platform.config import Settings
from tradingng_platform.db import Database
from tradingng_platform.mcp.context import reset_principal, set_principal
from tradingng_platform.mcp.server import create_mcp_server
from tradingng_platform.mcp.services import McpServices


class MissingMcpToken(Exception):
    pass


async def run_stdio(
    *,
    settings: Settings | None = None,
    verifier: OidcVerifier | None = None,
    database: Database | None = None,
) -> None:
    raw_token = os.environ.get("TRADINGNG_MCP_TOKEN")
    if not raw_token:
        raise MissingMcpToken

    app_settings = settings or Settings()
    oidc = verifier or OidcVerifier(
        str(app_settings.oidc_issuer),
        str(app_settings.mcp_resource_uri),
        app_settings.oidc_jwks_ttl_seconds,
    )
    principal = await oidc.verify(raw_token)
    resolved_database = database or Database(app_settings)
    principal_token = set_principal(principal)
    try:
        server = create_mcp_server(McpServices.from_database(resolved_database, app_settings))
        await server.run_stdio_async()
    finally:
        reset_principal(principal_token)
        await resolved_database.close()


def main() -> None:
    try:
        asyncio.run(run_stdio())
    except MissingMcpToken:
        print("TRADINGNG_MCP_TOKEN is required", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
