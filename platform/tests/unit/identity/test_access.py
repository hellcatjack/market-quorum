from datetime import datetime, timezone
from uuid import uuid4

import pytest

from tradingng_platform.api.errors import ApiError
from tradingng_platform.auth.oidc import ADMIN_SCOPES
from tradingng_platform.auth.principal import Principal
from tradingng_platform.identity.access import IdentityAccessService
from tradingng_platform.identity.contracts import LocalIdentity


class FakeIdentityRepository:
    def __init__(self, identity: LocalIdentity | None):
        self.identity = identity
        self.provisioned = False
        self.read_count = 0

    async def get_human(self, issuer: str, subject: str, *, for_update: bool = False):
        self.read_count += 1
        return self.identity

    async def provision_from_principal(self, principal: Principal, role: str):
        self.provisioned = True
        self.identity = local_identity(role)
        return self.identity


def token_principal(role: str) -> Principal:
    return Principal(
        issuer="https://issuer.example/realms/tradingng",
        subject="alice-sub",
        actor_type="user",
        scopes=ADMIN_SCOPES,
        display_name="Alice",
        email="alice@example.com",
        roles=frozenset({role}),
    )


def local_identity(role: str, status: str = "active") -> LocalIdentity:
    return LocalIdentity(
        id=uuid4(),
        issuer="https://issuer.example/realms/tradingng",
        subject="alice-sub",
        display_name="Alice",
        email="alice@example.com",
        status=status,
        role=role,
        synced_at=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize(
    ("token_role", "local_role", "expected_role", "has_system"),
    [
        ("User", "User", "User", False),
        ("Admin", "Admin", "Admin", True),
        ("Admin", "User", "User", False),
        ("User", "Admin", "User", False),
    ],
)
async def test_effective_role_never_exceeds_token_or_local_role(
    token_role,
    local_role,
    expected_role,
    has_system,
):
    repository = FakeIdentityRepository(local_identity(local_role))

    effective = await IdentityAccessService(repository).enforce(token_principal(token_role))

    assert effective.roles == frozenset({expected_role})
    assert ("system:read" in effective.scopes) is has_system


async def test_disabled_local_user_is_rejected():
    repository = FakeIdentityRepository(local_identity("User", "disabled"))

    with pytest.raises(ApiError) as captured:
        await IdentityAccessService(repository).enforce(token_principal("User"))

    assert captured.value.code == "account_disabled"


async def test_unknown_user_with_formal_role_is_provisioned():
    repository = FakeIdentityRepository(None)

    effective = await IdentityAccessService(repository).enforce(token_principal("User"))

    assert repository.provisioned is True
    assert effective.roles == frozenset({"User"})


async def test_unknown_user_with_legacy_role_is_rejected():
    repository = FakeIdentityRepository(None)

    with pytest.raises(ApiError) as captured:
        await IdentityAccessService(repository).enforce(token_principal("Analyst"))

    assert captured.value.code == "identity_not_provisioned"
    assert repository.provisioned is False


async def test_service_principal_bypasses_human_mirror():
    repository = FakeIdentityRepository(None)
    principal = Principal(
        issuer="https://issuer.example/realms/tradingng",
        subject="service-sub",
        actor_type="service",
        scopes=frozenset({"system:read"}),
    )

    effective = await IdentityAccessService(repository).enforce(principal)

    assert effective is principal
    assert repository.read_count == 0
