import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tradingng_platform.assessments.repository import AssessmentRepository
from tradingng_platform.auth.principal import Principal
from tradingng_platform.integrity.contracts import IntegrityStatus
from tradingng_platform.integrity.policy import PointInTimeRecorder
from tradingng_platform.integrity.repository import IntegrityRepository
from tradingng_platform.integrity.service import (
    CleanReassessmentNotAllowed,
    IntegrityNotFound,
    IntegrityService,
)
from tradingng_platform.models import (
    AssessmentBatch,
    AssessmentRequest,
    AssessmentRun,
    Base,
    Instrument,
    User,
)


def _admin() -> Principal:
    return Principal(
        "issuer",
        "integrity-admin",
        "user",
        frozenset({"assessments:read", "assessments:admin", "assessments:submit"}),
        roles=frozenset({"Admin"}),
    )


async def _seed_run(sessions, *, status: IntegrityStatus | None):
    async with sessions() as session, session.begin():
        user = await session.get(User, uuid.UUID(int=1))
        if user is None:
            user = User(
                id=uuid.UUID(int=1),
                issuer="issuer",
                subject="owner",
                display_name="Owner",
                email=None,
            )
            instrument = Instrument(
                id=uuid.UUID(int=2),
                canonical_ticker="NVDA",
                asset_type="stock",
                exchange="NASDAQ",
                name="NVIDIA",
                metadata_json={},
            )
            session.add_all([user, instrument])
            await session.flush()
        instrument = await session.get(Instrument, uuid.UUID(int=2))
        batch = AssessmentBatch(
            submitted_by=user.id,
            idempotency_key=f"source-{uuid.uuid4()}",
            defaults_json={
                "analysts": ["market", "news", "fundamentals"],
                "depth": "deep",
                "memory_mode": "historical",
                "language": "Chinese",
            },
        )
        session.add(batch)
        await session.flush()
        request = AssessmentRequest(
            batch_id=batch.id,
            instrument_id=instrument.id,
            analysis_date=date(2025, 7, 1),
            requested_config_json={"analysts": ["market", "news", "fundamentals"]},
        )
        session.add(request)
        await session.flush()
        run = AssessmentRun(
            request_id=request.id,
            status="succeeded",
            attempt=1,
            version=1,
        )
        session.add(run)
        await session.flush()
        if status is not None:
            recorder = PointInTimeRecorder(
                request.analysis_date,
                now=datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            recorder.record("evidence", status, f"test_{status.value}")
            await IntegrityRepository(session).persist_document(
                run.id,
                recorder.finalize(),
                artifact_id=None,
                audit_mode="retrospective",
            )
        return run.id


@pytest.fixture
async def service_context():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield IntegrityService(sessions), sessions
    finally:
        await engine.dispose()


async def test_clean_reassessment_is_independent_linked_and_idempotent(service_context):
    service, sessions = service_context
    risky_run_id = await _seed_run(sessions, status=IntegrityStatus.AT_RISK)

    first = await service.clean_reassess(_admin(), risky_run_id, "request-clean-1")
    second = await service.clean_reassess(_admin(), risky_run_id, "request-clean-2")

    async with sessions() as session:
        context = await AssessmentRepository(session).get_run_context(first.id)
    assert first.id == second.id
    assert context.batch.defaults_json["memory_mode"] == "independent"
    assert context.request.requested_config_json["memory_mode"] == "independent"
    assert context.run.clean_reassessment_of_run_id == risky_run_id
    assert context.run.retry_of_run_id is None
    assert context.run.attempt == 1


async def test_safe_run_cannot_be_clean_reassessed(service_context):
    service, sessions = service_context
    safe_run_id = await _seed_run(sessions, status=IntegrityStatus.SAFE)

    with pytest.raises(CleanReassessmentNotAllowed):
        await service.clean_reassess(_admin(), safe_run_id, "request-clean-safe")


async def test_integrity_view_and_summary_include_unassessed_runs(service_context):
    service, sessions = service_context
    risky_run_id = await _seed_run(sessions, status=IntegrityStatus.AT_RISK)
    unassessed_run_id = await _seed_run(sessions, status=None)

    risky = await service.get(_admin(), risky_run_id)
    unassessed = await service.get(_admin(), unassessed_run_id)
    summary = await service.summary(_admin())

    assert risky.status == "at_risk"
    assert unassessed.status == "unassessed"
    assert summary.total == 2
    assert summary.at_risk == 1
    assert summary.unassessed == 1
    assert summary.excluded_at_risk_count == 1

    with pytest.raises(IntegrityNotFound):
        await service.get(_admin(), uuid.uuid4())
