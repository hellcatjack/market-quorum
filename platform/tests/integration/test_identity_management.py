from datetime import datetime, timezone

from sqlalchemy import select

from tradingng_platform.auth.oidc import ADMIN_SCOPES
from tradingng_platform.auth.principal import Principal
from tradingng_platform.identity.contracts import KeycloakUser
from tradingng_platform.identity.repository import IdentityRepository
from tradingng_platform.models import AuditEvent, Role, User, UserRole


async def test_authoritative_sync_preserves_platform_uuid_and_replaces_legacy_role(
    session_factory,
):
    existing = User(
        issuer="https://issuer.example/realms/tradingng",
        subject="alice-sub",
        display_name="Old Alice",
        email="old@example.com",
        status="active",
        synced_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    analyst = Role(name="Analyst")
    async with session_factory() as session, session.begin():
        session.add_all([existing, analyst])
        await session.flush()
        session.add(UserRole(user_id=existing.id, role_id=analyst.id))
    original_id = existing.id

    repository = IdentityRepository(session_factory)
    async with repository.transaction(guard=True) as transaction:
        sync = await transaction.sync_authoritative(
            KeycloakUser(
                subject="alice-sub",
                username="alice",
                display_name="Alice",
                email="alice@example.com",
                enabled=False,
                role="User",
            ),
            "https://issuer.example/realms/tradingng",
        )

    assert sync.identity.id == original_id
    assert sync.identity.status == "disabled"
    async with session_factory() as session:
        roles = set(
            await session.scalars(
                select(Role.name)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == original_id)
            )
        )
    assert roles == {"User"}


async def test_identity_audit_rejects_secret_shaped_metadata(session_factory):
    repository = IdentityRepository(session_factory)
    principal = Principal(
        issuer="https://issuer.example/realms/tradingng",
        subject="admin-sub",
        actor_type="user",
        scopes=ADMIN_SCOPES,
        roles=frozenset({"Admin"}),
    )
    async with repository.transaction() as transaction:
        sync = await transaction.sync_authoritative(
            KeycloakUser(
                subject="target-sub",
                username="target",
                display_name="Target",
                email="target@example.com",
                enabled=True,
                role="User",
            ),
            principal.issuer,
        )
        try:
            await transaction.append_audit(
                principal,
                "user.password_reset",
                sync.identity,
                "request-secret",
                {"temporary_password": "must-not-persist"},
            )
        except ValueError as error:
            assert "metadata" in str(error)
        else:
            raise AssertionError("secret-shaped audit metadata was accepted")

    async with session_factory() as session:
        assert list(await session.scalars(select(AuditEvent))) == []
