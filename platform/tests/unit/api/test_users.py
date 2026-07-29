from datetime import datetime, timezone
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tradingng_platform.api.app import create_app
from tradingng_platform.api.auth import current_principal
from tradingng_platform.auth.oidc import ADMIN_SCOPES, USER_SCOPES
from tradingng_platform.auth.principal import Principal
from tradingng_platform.identity.contracts import (
    SessionSummary,
    TemporaryCredential,
    UserActionFlags,
    UserDetailView,
    UserPage,
    UserView,
)
from tradingng_platform.identity.errors import identity_error

USER_ID = UUID("00000000-0000-0000-0000-000000000042")


def _settings_environment(monkeypatch):
    monkeypatch.setenv(
        "TRADINGNG_DATABASE_URL",
        "postgresql+psycopg://tradingng:test@127.0.0.1:5432/tradingng",
    )
    monkeypatch.setenv("TRADINGNG_TOKEN_PEPPER", "unit-test-pepper-with-enough-entropy")
    monkeypatch.setenv("TRADINGNG_WEBHOOK_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _principal(role="Admin", scopes=ADMIN_SCOPES):
    return Principal(
        issuer="https://issuer.example/realms/tradingng",
        subject="admin-sub",
        actor_type="user",
        scopes=scopes,
        display_name="Admin",
        email="admin@example.com",
        roles=frozenset({role}),
    )


def _user_view():
    return UserView(
        id=USER_ID,
        subject="target-sub",
        username="target",
        display_name="Target User",
        email="target@example.com",
        role="User",
        enabled=True,
        synced_at=datetime(2026, 7, 29, 12, tzinfo=timezone.utc),
    )


def _detail():
    return UserDetailView(
        user=_user_view(),
        sessions=SessionSummary(active_count=1, last_access_at=None),
        allowed_actions=UserActionFlags(
            edit_profile=True,
            change_role=True,
            change_enabled=True,
            reset_password=True,
            logout=True,
        ),
        action_reasons={},
    )


class FakeIdentityAdmin:
    def __init__(self):
        self.calls = []
        self.error = None

    def _raise(self):
        if self.error:
            raise self.error

    async def list_users(self, principal, **filters):
        self._raise()
        self.calls.append(("list", filters))
        return UserPage(items=(_user_view(),), page=1, page_size=20, total=1)

    async def get_user(self, principal, user_id):
        self._raise()
        self.calls.append(("get", user_id))
        return _detail()

    async def create_user(self, principal, command, request_id):
        self._raise()
        self.calls.append(("create", command, request_id))
        return TemporaryCredential(_user_view(), SecretStr("temporary-secret"))

    async def update_user(self, principal, user_id, command, request_id):
        self._raise()
        self.calls.append(("update", user_id, command, request_id))
        return _detail()

    async def reset_password(self, principal, user_id, request_id):
        self._raise()
        self.calls.append(("reset", user_id, request_id))
        return TemporaryCredential(_user_view(), SecretStr("reset-secret"))

    async def logout_user(self, principal, user_id, request_id):
        self._raise()
        self.calls.append(("logout", user_id, request_id))
        return _detail()


def _client(monkeypatch, principal=None):
    _settings_environment(monkeypatch)
    service = FakeIdentityAdmin()
    app = create_app(identity_admin=service)
    app.dependency_overrides[current_principal] = lambda: principal or _principal()
    return TestClient(app), service


def test_admin_can_call_all_user_management_operations(monkeypatch):
    client, service = _client(monkeypatch)
    with client:
        listed = client.get("/api/v1/admin/users?role=User&status=active&page=1&page_size=20")
        detail = client.get(f"/api/v1/admin/users/{USER_ID}")
        created = client.post(
            "/api/v1/admin/users",
            json={
                "username": "target",
                "display_name": "Target User",
                "email": "target@example.com",
                "role": "User",
            },
        )
        updated = client.patch(
            f"/api/v1/admin/users/{USER_ID}",
            json={"display_name": "Updated Target"},
        )
        reset = client.post(f"/api/v1/admin/users/{USER_ID}/reset-password", json={})
        logged_out = client.post(f"/api/v1/admin/users/{USER_ID}/logout", json={})

    assert listed.status_code == 200 and listed.json()["total"] == 1
    assert detail.status_code == 200 and detail.json()["user"]["id"] == str(USER_ID)
    assert created.status_code == 201
    assert created.json()["temporary_password"] == "temporary-secret"
    assert updated.status_code == 200
    assert reset.status_code == 200, reset.text
    assert reset.json()["temporary_password"] == "reset-secret"
    assert logged_out.status_code == 200
    assert [call[0] for call in service.calls] == [
        "list", "get", "create", "update", "reset", "logout"
    ]


def test_user_role_is_forbidden_even_with_forged_management_scope(monkeypatch):
    forged = USER_SCOPES | frozenset({"users:manage"})
    client, service = _client(monkeypatch, _principal("User", forged))

    with client:
        response = client.get("/api/v1/admin/users")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_scope"
    assert service.calls == []


def test_admin_role_without_management_scope_is_forbidden(monkeypatch):
    client, service = _client(monkeypatch, _principal("Admin", USER_SCOPES))

    with client:
        response = client.get("/api/v1/admin/users")

    assert response.status_code == 403
    assert service.calls == []


def test_patch_cannot_change_username_and_delete_does_not_exist(monkeypatch):
    client, _ = _client(monkeypatch)
    with client:
        patch = client.patch(
            f"/api/v1/admin/users/{USER_ID}",
            json={"username": "changed"},
        )
        deleted = client.delete(f"/api/v1/admin/users/{USER_ID}")

    assert patch.status_code == 422
    assert deleted.status_code == 405


def test_identity_error_is_mapped_without_upstream_details(monkeypatch):
    client, service = _client(monkeypatch)
    service.error = identity_error("identity_provider_unavailable")

    with client:
        response = client.get(
            "/api/v1/admin/users",
            headers={"X-Request-ID": "identity-request-1"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "identity_provider_unavailable",
            "message": "The identity provider is temporarily unavailable",
            "request_id": "identity-request-1",
        }
    }


def test_create_validation_rejects_invalid_role(monkeypatch):
    client, service = _client(monkeypatch)
    with client:
        response = client.post(
            "/api/v1/admin/users",
            json={
                "username": "target",
                "display_name": "Target",
                "email": "target@example.com",
                "role": "Viewer",
            },
        )

    assert response.status_code == 422
    assert service.calls == []
