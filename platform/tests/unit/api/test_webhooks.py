import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from tradingng_platform.api.app import create_app
from tradingng_platform.api.auth import current_principal
from tradingng_platform.auth.principal import Principal
from tradingng_platform.webhooks.contracts import WebhookView
from tradingng_platform.webhooks.worker import EndpointRejected

WEBHOOK_ID = uuid.UUID("00000000-0000-0000-0000-000000000301")
NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


class _Webhooks:
    async def create(self, principal, command, request_id):
        if command.endpoint.endswith("/blocked"):
            raise EndpointRejected("webhook endpoint resolved to a non-public address")
        return WebhookView(
            id=WEBHOOK_ID,
            endpoint=command.endpoint,
            event_types=command.event_types,
            status="active",
            created_at=NOW,
        )

    async def list(self, principal):
        return [
            WebhookView(
                id=WEBHOOK_ID,
                endpoint="https://hooks.example/events",
                event_types={"assessment.succeeded"},
                status="active",
                created_at=NOW,
            )
        ]

    async def deactivate(self, principal, webhook_id, request_id):
        return None


def _app(monkeypatch):
    monkeypatch.setenv(
        "TRADINGNG_DATABASE_URL",
        "postgresql+psycopg://tradingng:test@127.0.0.1:5432/tradingng",
    )
    monkeypatch.setenv("TRADINGNG_TOKEN_PEPPER", "unit-test-pepper-with-enough-entropy")
    monkeypatch.setenv("TRADINGNG_WEBHOOK_ENCRYPTION_KEY", Fernet.generate_key().decode())
    app = create_app()
    app.dependency_overrides[current_principal] = lambda: Principal(
        "issuer",
        "admin",
        "user",
        frozenset({"assessments:admin"}),
        roles=frozenset({"Admin"}),
    )
    return app


def test_webhook_management_contract_never_returns_secret(monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        app.state.webhooks = _Webhooks()
        created = client.post(
            "/api/v1/webhooks",
            json={
                "endpoint": "https://hooks.example/events",
                "event_types": ["assessment.succeeded"],
                "secret": "must-never-be-returned",
            },
        )
        listed = client.get("/api/v1/webhooks")
        disabled = client.delete(f"/api/v1/webhooks/{WEBHOOK_ID}")

    assert created.status_code == 201
    assert created.json()["id"] == str(WEBHOOK_ID)
    assert "secret" not in created.text
    assert "secret" not in listed.text
    assert disabled.status_code == 204


def test_webhook_rejects_unknown_events_and_unsafe_endpoint(monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        app.state.webhooks = _Webhooks()
        unknown = client.post(
            "/api/v1/webhooks",
            json={
                "endpoint": "https://hooks.example/events",
                "event_types": ["assessment.unknown"],
                "secret": "a-long-enough-secret",
            },
        )
        blocked = client.post(
            "/api/v1/webhooks",
            json={
                "endpoint": "https://hooks.example/blocked",
                "event_types": ["assessment.failed"],
                "secret": "a-long-enough-secret",
            },
        )

    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "invalid_request"
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "webhook_endpoint_rejected"
