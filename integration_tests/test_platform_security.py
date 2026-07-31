from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from tradingng_platform.api.app import create_app

ROOT = Path(__file__).resolve().parents[1]


class _RejectingOidc:
    async def verify(self, token):
        raise ValueError("rejected")


def _environment(monkeypatch):
    monkeypatch.setenv(
        "TRADINGNG_DATABASE_URL",
        "postgresql+psycopg://tradingng:test@127.0.0.1:5432/tradingng",
    )
    monkeypatch.setenv("TRADINGNG_TOKEN_PEPPER", "offline-security-test-pepper")
    monkeypatch.setenv(
        "TRADINGNG_WEBHOOK_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    monkeypatch.setenv("TRADINGNG_ALLOWED_ORIGINS", "https://tradingng.internal:8443")
    monkeypatch.setenv(
        "TRADINGNG_MCP_ALLOWED_ORIGINS", "https://tradingng.internal:8443"
    )


def test_rest_mcp_and_browser_boundaries_fail_closed(monkeypatch):
    _environment(monkeypatch)
    app = create_app(mcp_oidc=_RejectingOidc())
    with TestClient(app, base_url="http://127.0.0.1:8010") as client:
        rest = client.get("/api/v1/assessments")
        mcp = client.post("/mcp", json={})
        csrf = client.post(
            "/api/v1/assessments",
            headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
            json={},
        )
        forbidden_origin = client.post(
            "/mcp",
            headers={
                "Origin": "https://evil.example",
                "Authorization": "Bearer invalid",
            },
            json={},
        )

    assert rest.status_code == 401
    assert mcp.status_code == 401
    assert "resource_metadata=" in mcp.headers["WWW-Authenticate"]
    assert csrf.status_code == 403
    assert forbidden_origin.status_code == 403
    assert "invalid" not in forbidden_origin.text


def test_public_routing_exposes_only_authenticated_physical_lan_gateway():
    caddy = (ROOT / "deploy/caddy/tradingng.caddy").read_text()
    gateway_unit = (ROOT / "systemd/user/tradingng-codex-gateway.service").read_text()
    assert caddy.count("reverse_proxy 127.0.0.1:8000") == 1
    assert "route /openai/* {" in caddy
    assert "path /openai/v1/models /openai/v1/chat/completions" in caddy
    assert "remote_ip 192.168.1.0/24" in caddy
    assert 'header Authorization "Bearer {$CODEX_GATEWAY_LAN_API_KEY}"' in caddy
    assert "header_up -Authorization" in caddy
    assert "/openai/internal/status" not in caddy
    assert "gateway_audit" not in caddy
    assert "codex-gateway-audit" not in gateway_unit
    assert (
        "ExecStart=/app/devs/TradingNG/.venv/bin/python -m codex_gateway"
        in gateway_unit
    )
