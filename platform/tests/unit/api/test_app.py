import re
from types import SimpleNamespace

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import BaseModel

from tradingng_platform.api.app import create_app
from tradingng_platform.api.auth import current_principal
from tradingng_platform.api.routes.health import readiness
from tradingng_platform.auth.principal import Principal


def _settings_environment(monkeypatch):
    monkeypatch.setenv(
        "TRADINGNG_DATABASE_URL",
        "postgresql+psycopg://tradingng:test@127.0.0.1:5432/tradingng",
    )
    monkeypatch.setenv("TRADINGNG_TOKEN_PEPPER", "unit-test-pepper-with-enough-entropy")
    monkeypatch.setenv("TRADINGNG_WEBHOOK_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv(
        "TRADINGNG_ALLOWED_ORIGINS",
        "https://tradingng.internal,https://backup.internal",
    )


def _principal():
    return Principal(
        issuer="https://issuer.example",
        subject="alice",
        actor_type="user",
        scopes=frozenset({"assessments:read"}),
        display_name="Alice",
        email="alice@example.com",
        roles=frozenset({"Viewer"}),
    )


def test_liveness_is_public(monkeypatch):
    _settings_environment(monkeypatch)
    with TestClient(create_app()) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


async def test_readiness_reports_only_the_database_dialect(monkeypatch):
    class Session:
        async def execute(self, statement):
            return None

        async def scalar(self, statement):
            return "20260726_0007"

    class SessionContext:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, exc_type, exc_value, traceback):
            return False

    class Database:
        def sessions(self):
            return SessionContext()

    class Response:
        def raise_for_status(self):
            return None

    class GatewayClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            return False

        async def get(self, url, timeout):
            return Response()

    monkeypatch.setattr("tradingng_platform.api.routes.health.httpx.AsyncClient", GatewayClient)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                database=Database(),
                settings=SimpleNamespace(
                    database_dialect="mysql",
                    gateway_url="http://127.0.0.1:8000",
                ),
            )
        )
    )

    assert await readiness(request) == {
        "status": "ok",
        "database": {"dialect": "mysql"},
    }


def test_protected_route_requires_bearer(monkeypatch):
    _settings_environment(monkeypatch)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Bearer token is required",
            "request_id": response.headers["X-Request-ID"],
        }
    }
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_request_id_is_preserved_and_me_uses_dependency_override(monkeypatch):
    _settings_environment(monkeypatch)
    app = create_app()
    app.dependency_overrides[current_principal] = _principal
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/me",
            headers={"X-Request-ID": "caller-123"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "caller-123"
    assert response.json()["subject"] == "alice"
    assert response.json()["scopes"] == ["assessments:read"]


def test_invalid_request_id_is_replaced(monkeypatch):
    _settings_environment(monkeypatch)
    with TestClient(create_app()) as client:
        response = client.get("/health/live", headers={"X-Request-ID": "../bad id"})

    assert response.headers["X-Request-ID"] != "../bad id"
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


def test_cross_site_browser_write_is_rejected(monkeypatch):
    _settings_environment(monkeypatch)
    app = create_app()
    app.dependency_overrides[current_principal] = _principal
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assessments",
            headers={
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "cross-site",
            },
            json={},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_rejected"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_allowed_origin_receives_exact_cors_header(monkeypatch):
    _settings_environment(monkeypatch)
    with TestClient(create_app()) as client:
        response = client.options(
            "/api/v1/me",
            headers={
                "Origin": "https://tradingng.internal",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "https://tradingng.internal"


def test_validation_error_does_not_echo_request_values(monkeypatch):
    _settings_environment(monkeypatch)
    app = create_app()

    class SensitiveBody(BaseModel):
        secret: int

    @app.post("/api/v1/validation-probe")
    async def validation_probe(body: SensitiveBody):
        return body

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/validation-probe",
            json={"secret": "must-not-leak"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "must-not-leak" not in response.text
