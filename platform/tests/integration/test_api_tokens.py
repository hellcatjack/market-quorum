import pytest
from sqlalchemy import select

from tradingng_platform.auth.principal import Principal
from tradingng_platform.auth.tokens import ApiTokenService
from tradingng_platform.models import ApiCredential, AuditEvent


async def test_api_token_is_hashed_scoped_and_preserves_issuer_role(session_factory):
    service = ApiTokenService(session_factory, "integration-test-pepper")
    admin = Principal(
        "https://issuer.example",
        "admin-user",
        "user",
        frozenset({"assessments:admin", "assessments:read"}),
        display_name="Admin User",
        roles=frozenset({"Admin"}),
    )

    created = await service.create(
        admin,
        {"assessments:read"},
        request_id="request-create-token",
    )
    verified = await service.verify(created.token)

    assert created.token.startswith(f"tng_{created.credential.public_id}_")
    assert created.token not in created.credential.token_hash
    assert verified.subject == f"api:{created.credential.public_id}"
    assert verified.scopes == frozenset({"assessments:read"})
    assert verified.roles == frozenset({"Admin"})
    async with session_factory() as session:
        credential = await session.scalar(
            select(ApiCredential).where(ApiCredential.id == created.credential.id)
        )
        assert credential.last_used_at is not None

    listed = await service.list(admin)
    assert listed[0].public_id == created.credential.public_id
    assert "token" not in listed[0].model_dump()
    assert "token_hash" not in listed[0].model_dump()
    await service.revoke(admin, created.credential.id, "request-revoke-token")
    with pytest.raises(ValueError, match="revoked"):
        await service.verify(created.token)
    async with session_factory() as session:
        actions = tuple(
            await session.scalars(select(AuditEvent.action).order_by(AuditEvent.created_at))
        )
    assert actions == ("api_credential.create", "api_credential.revoke")

    with pytest.raises(PermissionError, match="exceed"):
        await service.create(admin, {"system:unknown"})
