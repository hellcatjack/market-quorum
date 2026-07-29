import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from tradingng_platform.assessments.repository import AssessmentRepository
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.runs import RunStatus


def test_run_view_includes_cached_instrument_identity():
    run = SimpleNamespace(
        id=uuid.uuid4(),
        status=RunStatus.SUCCEEDED.value,
        attempt=1,
        created_at=datetime(2026, 7, 25, 16, 0, tzinfo=timezone.utc),
    )
    request = SimpleNamespace(id=uuid.uuid4(), analysis_date=date(2026, 7, 25))
    instrument = SimpleNamespace(
        canonical_ticker="NVDA",
        asset_type="stock",
        name="英伟达",
        exchange="NASDAQ",
    )

    view = AssessmentRepository._run_view(run, request, instrument)

    assert view.instrument_name == "英伟达"
    assert view.exchange == "NASDAQ"


class _ExistingIdentitySession:
    def __init__(self, user):
        self.user = user
        self.write_count = 0

    async def scalar(self, statement):
        return self.user

    async def execute(self, statement):
        self.write_count += 1
        raise AssertionError("an existing managed identity must not rewrite roles or status")


@pytest.mark.asyncio
async def test_upsert_user_does_not_reactivate_existing_disabled_user():
    user = SimpleNamespace(
        id=uuid.uuid4(),
        issuer="issuer",
        subject="alice",
        display_name="Old Name",
        email="old@example.com",
        status="disabled",
    )
    session = _ExistingIdentitySession(user)
    principal = Principal(
        issuer="issuer",
        subject="alice",
        actor_type="user",
        scopes=frozenset({"assessments:submit"}),
        display_name="Alice",
        email="alice@example.com",
        roles=frozenset({"User"}),
    )

    resolved = await AssessmentRepository(session).upsert_user(principal)

    assert resolved.status == "disabled"
    assert resolved.display_name == "Alice"
    assert session.write_count == 0


@pytest.mark.asyncio
async def test_upsert_user_does_not_replace_managed_role_from_stale_token():
    user = SimpleNamespace(
        id=uuid.uuid4(),
        issuer="issuer",
        subject="alice",
        display_name="Alice",
        email="alice@example.com",
        status="active",
    )
    session = _ExistingIdentitySession(user)
    stale_admin = Principal(
        issuer="issuer",
        subject="alice",
        actor_type="user",
        scopes=frozenset({"assessments:submit"}),
        display_name="Alice",
        email="alice@example.com",
        roles=frozenset({"Admin"}),
    )

    await AssessmentRepository(session).upsert_user(stale_admin)

    assert session.write_count == 0
