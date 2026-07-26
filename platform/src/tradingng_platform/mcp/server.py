from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from tradingng_platform.config import Settings
from tradingng_platform.mcp.prompts import register_prompts
from tradingng_platform.mcp.resources import register_resources
from tradingng_platform.mcp.services import McpServices
from tradingng_platform.mcp.tools import register_tools

MCP_SCOPES = [
    "assessments:read",
    "assessments:submit",
    "assessments:cancel",
    "validations:read",
    "validations:write",
    "system:read",
    "artifacts:read",
]


def protected_resource_metadata(settings: Settings) -> dict[str, object]:
    return {
        "resource": str(settings.mcp_resource_uri),
        "authorization_servers": [str(settings.oidc_issuer)],
        "bearer_methods_supported": ["header"],
        "scopes_supported": MCP_SCOPES,
    }


def create_mcp_server(
    services: McpServices,
    *,
    allowed_hosts: tuple[str, ...] = ("127.0.0.1:*", "localhost:*", "[::1]:*"),
    allowed_origins: tuple[str, ...] = (),
) -> FastMCP:
    server = FastMCP(
        "TradingNG",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            allowed_hosts=list(allowed_hosts),
            allowed_origins=list(allowed_origins),
        ),
    )
    register_tools(server, services)
    register_resources(server, services)
    register_prompts(server)
    return server
