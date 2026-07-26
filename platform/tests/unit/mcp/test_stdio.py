import os
import subprocess
import sys

import pytest

from tradingng_platform.auth.principal import Principal
from tradingng_platform.config import Settings
from tradingng_platform.mcp import stdio
from tradingng_platform.mcp.context import current_principal


def test_stdio_requires_service_token():
    environment = os.environ.copy()
    environment.pop("TRADINGNG_MCP_TOKEN", None)
    environment["TRADINGNG_DATABASE_URL"] = (
        "postgresql+psycopg://tradingng:test@127.0.0.1:5432/tradingng"
    )
    environment["PYTHONPATH"] = "platform/src"

    result = subprocess.run(
        [sys.executable, "-m", "tradingng_platform.mcp.stdio"],
        cwd=os.getcwd(),
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 2
    assert result.stderr.strip() == "TRADINGNG_MCP_TOKEN is required"
    assert result.stdout == ""


@pytest.mark.asyncio
async def test_stdio_validates_token_and_runs_shared_registry(monkeypatch):
    principal = Principal(
        issuer="issuer",
        subject="stdio-client",
        actor_type="service",
        scopes=frozenset({"assessments:read", "system:read"}),
    )

    class _Verifier:
        tokens = []

        async def verify(self, token):
            self.tokens.append(token)
            return principal

    class _Database:
        sessions = object()
        closed = False

        async def close(self):
            self.closed = True

    class _Server:
        async def run_stdio_async(self):
            assert current_principal() == principal

    captured = {}

    def _factory(services):
        captured["services"] = services
        return _Server()

    monkeypatch.setenv("TRADINGNG_MCP_TOKEN", "short-lived-service-token")
    monkeypatch.setattr(stdio, "create_mcp_server", _factory)
    verifier = _Verifier()
    database = _Database()
    settings = Settings(database_url="postgresql+psycopg://tradingng:test@127.0.0.1:5432/tradingng")

    await stdio.run_stdio(settings=settings, verifier=verifier, database=database)

    assert verifier.tokens == ["short-lived-service-token"]
    assert captured["services"].assessments.sessions is database.sessions
    assert database.closed is True
