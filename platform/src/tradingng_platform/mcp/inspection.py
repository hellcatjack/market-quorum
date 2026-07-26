from __future__ import annotations

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def inspect_inventory(url: str, token: str) -> dict[str, list[str]]:
    """List MCP primitives without invoking a tool or reading a resource."""
    async with (
        httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        ) as http_client,
        streamable_http_client(url, http_client=http_client) as streams,
    ):
        read_stream, write_stream, _ = streams
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            resources = await session.list_resources()
            templates = await session.list_resource_templates()
            prompts = await session.list_prompts()
    return {
        "tools": sorted(item.name for item in tools.tools),
        "resources": sorted(str(item.uri) for item in resources.resources),
        "resource_templates": sorted(str(item.uriTemplate) for item in templates.resourceTemplates),
        "prompts": sorted(item.name for item in prompts.prompts),
    }
