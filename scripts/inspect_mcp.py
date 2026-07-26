"""List TradingNG MCP primitives without invoking any tool or reading a resource."""

from __future__ import annotations

import argparse
import asyncio
import os

from tradingng_platform.mcp.inspection import inspect_inventory


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Exact Streamable HTTP MCP URL")
    parser.add_argument(
        "--token-env",
        default="TRADINGNG_MCP_TOKEN",
        help="Environment variable containing the short-lived OIDC token",
    )
    return parser.parse_args()


def main() -> None:
    arguments = _parse_args()
    token = os.environ.get(arguments.token_env)
    if not token:
        raise SystemExit(f"{arguments.token_env} is required")
    inventory = asyncio.run(inspect_inventory(arguments.url, token))
    for category in ("tools", "resources", "resource_templates", "prompts"):
        print(f"{category}:")
        for name in inventory[category]:
            print(f"  {name}")


if __name__ == "__main__":
    main()
