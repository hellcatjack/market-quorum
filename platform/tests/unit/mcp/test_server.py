from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from tradingng_platform.api.app import create_app
from tradingng_platform.auth.principal import Principal
from tradingng_platform.config import Settings
from tradingng_platform.mcp.server import protected_resource_metadata


class _Verifier:
    async def verify(self, token: str) -> Principal:
        if token != "valid-oidc-token":
            raise ValueError("invalid token")
        return Principal(
            issuer="https://issuer.example",
            subject="mcp-client",
            actor_type="service",
            scopes=frozenset({"system:read"}),
        )


def _environment(monkeypatch):
    monkeypatch.setenv(
        "TRADINGNG_DATABASE_URL",
        "postgresql+psycopg://tradingng:test@127.0.0.1:5432/tradingng",
    )
    monkeypatch.setenv("TRADINGNG_TOKEN_PEPPER", "unit-test-token-pepper")
    monkeypatch.setenv("TRADINGNG_WEBHOOK_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TRADINGNG_MCP_ALLOWED_ORIGINS", "https://trusted.example")
    monkeypatch.setenv("TRADINGNG_OIDC_ISSUER", "http://127.0.0.1:8080/realms/tradingng")
    monkeypatch.setenv("TRADINGNG_MCP_RESOURCE_URI", "https://ushome.amycat.com/mcp")


def _initialize(client: TestClient, **headers):
    return client.post(
        "/mcp",
        headers={
            "Authorization": "Bearer valid-oidc-token",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **headers,
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
        follow_redirects=False,
    )


def test_protected_resource_metadata_uses_canonical_mcp_resource(monkeypatch):
    _environment(monkeypatch)
    settings = Settings()

    assert protected_resource_metadata(settings) == {
        "resource": "https://ushome.amycat.com/mcp",
        "authorization_servers": ["http://127.0.0.1:8080/realms/tradingng"],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [
            "assessments:read",
            "assessments:submit",
            "assessments:cancel",
            "assessments:admin",
            "validations:read",
            "validations:write",
            "system:read",
            "artifacts:read",
        ],
    }


def test_streamable_http_initializes_at_exact_mcp_url(monkeypatch):
    _environment(monkeypatch)
    app = create_app(mcp_oidc=_Verifier())

    with TestClient(app, base_url="http://127.0.0.1:8010") as client:
        response = _initialize(client)

    assert response.status_code == 200
    assert response.json()["result"]["protocolVersion"] == "2025-11-25"


def test_streamable_http_fails_closed_for_authentication_and_origin(monkeypatch):
    _environment(monkeypatch)
    app = create_app(mcp_oidc=_Verifier())

    with TestClient(app, base_url="http://127.0.0.1:8010") as client:
        missing = client.post("/mcp", json={})
        unauthenticated_oversized = client.post(
            "/mcp",
            headers={"Content-Type": "application/json"},
            content=b"x" * (1024 * 1024 + 1),
        )
        forbidden_origin = _initialize(client, Origin="https://evil.example")

    assert missing.status_code == 401
    assert unauthenticated_oversized.status_code == 401
    assert "resource_metadata=" in missing.headers["WWW-Authenticate"]
    assert forbidden_origin.status_code == 403
