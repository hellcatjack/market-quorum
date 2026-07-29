from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from tradingng_platform.auth.oidc import ADMIN_SCOPES
from tradingng_platform.auth.principal import Principal
from tradingng_platform.identity.contracts import (
    CreateUserCommand,
    IdentitySync,
    KeycloakPage,
    KeycloakSession,
    KeycloakUser,
    LocalIdentity,
    UpdateUserCommand,
)
from tradingng_platform.identity.errors import IdentityError
from tradingng_platform.identity.service import IdentityAdminService

ISSUER = "https://issuer.example/realms/tradingng"


def admin(subject: str = "admin-sub") -> Principal:
    return Principal(
        issuer=ISSUER,
        subject=subject,
        actor_type="user",
        scopes=ADMIN_SCOPES,
        display_name="Platform Admin",
        email="admin@example.com",
        roles=frozenset({"Admin"}),
    )


def local(
    subject: str,
    role: str,
    *,
    status: str = "active",
    identity_id: UUID | None = None,
) -> LocalIdentity:
    return LocalIdentity(
        id=identity_id or uuid4(),
        issuer=ISSUER,
        subject=subject,
        display_name=subject,
        email=f"{subject}@example.com",
        status=status,
        role=role,
        synced_at=datetime.now(timezone.utc),
    )


def keycloak_user(
    subject: str,
    role: str,
    *,
    enabled: bool = True,
    username: str | None = None,
) -> KeycloakUser:
    return KeycloakUser(
        subject=subject,
        username=username or subject,
        display_name=subject,
        email=f"{subject}@example.com",
        enabled=enabled,
        role=role,
    )


class FakeKeycloak:
    def __init__(self, users: list[KeycloakUser]):
        self.users = {user.subject: user for user in users}
        self.calls: list[tuple] = []
        self.next_subject = "created-sub"

    async def list_users(self, *, search, first, maximum):
        self.calls.append(("list", search, first, maximum))
        users = list(self.users.values())
        if search:
            needle = search.casefold()
            users = [
                user
                for user in users
                if needle in user.username.casefold()
                or needle in (user.email or "").casefold()
                or needle in user.display_name.casefold()
            ]
        return KeycloakPage(tuple(users[first : first + maximum]), len(users))

    async def get_user(self, subject):
        self.calls.append(("get", subject))
        return self.users[subject]

    async def create_user(self, *, username, display_name, email, enabled):
        self.calls.append(("create", username, enabled))
        self.users[self.next_subject] = KeycloakUser(
            subject=self.next_subject,
            username=username,
            display_name=display_name,
            email=email,
            enabled=enabled,
            role="User",
        )
        return self.next_subject

    async def update_user(self, subject, *, display_name, email, enabled):
        self.calls.append(("update", subject, display_name, email, enabled))
        current = self.users[subject]
        self.users[subject] = KeycloakUser(
            subject=subject,
            username=current.username,
            display_name=display_name,
            email=email,
            enabled=enabled,
            role=current.role,
        )

    async def replace_role(self, subject, role):
        self.calls.append(("role", subject, role))
        current = self.users[subject]
        self.users[subject] = KeycloakUser(
            subject=current.subject,
            username=current.username,
            display_name=current.display_name,
            email=current.email,
            enabled=current.enabled,
            role=role,
        )

    async def set_temporary_password(self, subject, password):
        self.calls.append(("password", subject, password))

    async def logout(self, subject):
        self.calls.append(("logout", subject))

    async def sessions(self, subject):
        return (
            KeycloakSession(
                session_id="session-1",
                started_at=datetime(2026, 7, 29, 10, tzinfo=timezone.utc),
                last_access_at=datetime(2026, 7, 29, 11, tzinfo=timezone.utc),
            ),
        )


class FakeRepository:
    def __init__(self, identities: list[LocalIdentity], *, fail_sync=False):
        self.identities = {item.id: item for item in identities}
        self.audit: list[dict] = []
        self.fail_sync = fail_sync
        self.guard_entries = 0

    @asynccontextmanager
    async def transaction(self, *, guard=False):
        if guard:
            self.guard_entries += 1
        yield self

    async def get_by_id(self, identity_id):
        return self.identities.get(identity_id)

    async def enabled_admin_count(self):
        return sum(
            item.role == "Admin" and item.status == "active"
            for item in self.identities.values()
        )

    async def sync_authoritative(self, user, issuer):
        if self.fail_sync:
            raise RuntimeError("database unavailable")
        existing = next(
            (item for item in self.identities.values() if item.subject == user.subject),
            None,
        )
        previous_role = existing.role if existing else None
        previous_status = existing.status if existing else None
        identity = LocalIdentity(
            id=existing.id if existing else uuid4(),
            issuer=issuer,
            subject=user.subject,
            display_name=user.display_name,
            email=user.email,
            status="active" if user.enabled else "disabled",
            role=user.role,
            synced_at=datetime.now(timezone.utc),
        )
        self.identities[identity.id] = identity
        unchanged = existing is not None and (
            existing.display_name,
            existing.email,
            existing.status,
            existing.role,
        ) == (
            identity.display_name,
            identity.email,
            identity.status,
            identity.role,
        )
        changed = () if unchanged else ("profile",)
        return IdentitySync(identity, changed, previous_role, previous_status)

    async def append_audit(self, principal, action, target, request_id, metadata):
        self.audit.append(
            {
                "actor": principal.subject,
                "action": action,
                "target": str(target.id),
                "request_id": request_id,
                "metadata": metadata,
            }
        )


def service(users, identities, *, fail_sync=False):
    keycloak = FakeKeycloak(users)
    repository = FakeRepository(identities, fail_sync=fail_sync)
    return IdentityAdminService(keycloak, repository, ISSUER), keycloak, repository


def test_commands_normalize_and_validate_account_fields():
    command = CreateUserCommand(
        username="  Alice.Research  ",
        display_name="  Alice Research  ",
        email="Alice@Example.COM",
        role="User",
    )
    assert command.username == "alice.research"
    assert command.display_name == "Alice Research"
    assert str(command.email) == "Alice@example.com"

    with pytest.raises(ValidationError):
        CreateUserCommand(
            username="bad/user",
            display_name="Alice",
            email="alice@example.com",
            role="User",
        )
    with pytest.raises(ValidationError):
        UpdateUserCommand()


async def test_create_is_disabled_until_role_and_password_exist_and_secret_is_not_audited():
    admin_local = local("admin-sub", "Admin")
    identity_service, keycloak, repository = service(
        [keycloak_user("admin-sub", "Admin")],
        [admin_local],
    )

    created = await identity_service.create_user(
        admin(),
        CreateUserCommand(
            username="alice",
            display_name="Alice",
            email="alice@example.com",
            role="User",
        ),
        "request-1",
    )

    password = created.temporary_password.get_secret_value()
    assert len(password) >= 24
    assert keycloak.calls[2:8] == [
        ("create", "alice", False),
        ("role", "created-sub", "User"),
        ("password", "created-sub", password),
        ("update", "created-sub", "Alice", "alice@example.com", True),
        ("logout", "created-sub"),
        ("get", "created-sub"),
    ]
    assert repository.audit[0]["action"] == "user.create"
    assert password not in repr(repository.audit)
    assert password not in repr(created)


@pytest.mark.parametrize(
    ("command", "expected_code"),
    [
        (UpdateUserCommand(enabled=False), "self_admin_change_forbidden"),
        (UpdateUserCommand(role="User"), "self_admin_change_forbidden"),
    ],
)
async def test_current_admin_cannot_remove_own_access(command, expected_code):
    admin_local = local("admin-sub", "Admin")
    identity_service, _, _ = service(
        [keycloak_user("admin-sub", "Admin")],
        [admin_local],
    )

    with pytest.raises(IdentityError) as captured:
        await identity_service.update_user(admin(), admin_local.id, command, "request-2")

    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    "command",
    [UpdateUserCommand(enabled=False), UpdateUserCommand(role="User")],
)
async def test_last_enabled_admin_is_protected(command):
    current = local("admin-sub", "Admin")
    target = local("target-sub", "Admin")
    current = LocalIdentity(**{**current.__dict__, "status": "disabled"})
    identity_service, _, repository = service(
        [
            keycloak_user("admin-sub", "Admin", enabled=False),
            keycloak_user("target-sub", "Admin"),
        ],
        [current, target],
    )

    with pytest.raises(IdentityError) as captured:
        await identity_service.update_user(admin(), target.id, command, "request-3")

    assert captured.value.code == "last_admin_protected"
    assert repository.guard_entries == 1


async def test_role_and_status_changes_revoke_sessions_and_write_separate_audits():
    current = local("admin-sub", "Admin")
    target = local("target-sub", "User")
    identity_service, keycloak, repository = service(
        [keycloak_user("admin-sub", "Admin"), keycloak_user("target-sub", "User")],
        [current, target],
    )

    detail = await identity_service.update_user(
        admin(),
        target.id,
        UpdateUserCommand(role="Admin", enabled=False),
        "request-4",
    )

    assert detail.user.role == "Admin"
    assert detail.user.enabled is False
    assert ("logout", "target-sub") in keycloak.calls
    assert [event["action"] for event in repository.audit] == [
        "user.role_change",
        "user.disable",
    ]


async def test_reset_password_returns_secret_once_and_revokes_sessions():
    current = local("admin-sub", "Admin")
    target = local("target-sub", "User")
    identity_service, keycloak, repository = service(
        [keycloak_user("admin-sub", "Admin"), keycloak_user("target-sub", "User")],
        [current, target],
    )

    result = await identity_service.reset_password(admin(), target.id, "request-5")

    password = result.temporary_password.get_secret_value()
    assert ("password", "target-sub", password) in keycloak.calls
    assert ("logout", "target-sub") in keycloak.calls
    assert repository.audit[0]["action"] == "user.password_reset"
    assert password not in repr(repository.audit)


async def test_keycloak_success_and_mysql_failure_is_reported_as_sync_pending():
    current = local("admin-sub", "Admin")
    target = local("target-sub", "User")
    identity_service, keycloak, _ = service(
        [keycloak_user("admin-sub", "Admin"), keycloak_user("target-sub", "User")],
        [current, target],
        fail_sync=True,
    )

    with pytest.raises(IdentityError) as captured:
        await identity_service.update_user(
            admin(),
            target.id,
            UpdateUserCommand(display_name="Updated"),
            "request-6",
        )

    assert ("update", "target-sub", "Updated", "target-sub@example.com", True) in keycloak.calls
    assert captured.value.code == "identity_sync_pending"


async def test_list_filters_authoritative_users_and_preserves_platform_ids():
    current = local("admin-sub", "Admin")
    target = local("target-sub", "User", identity_id=UUID(int=42))
    identity_service, _, repository = service(
        [keycloak_user("admin-sub", "Admin"), keycloak_user("target-sub", "User")],
        [current, target],
    )

    page = await identity_service.list_users(
        admin(), search=None, role="User", status="active", page=1, page_size=20
    )

    assert page.total == 1
    assert page.items[0].id == UUID(int=42)
    assert page.items[0].username == "target-sub"
    assert repository.audit == []
