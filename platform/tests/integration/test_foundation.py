from datetime import date

from sqlalchemy import func, select

from tradingng_platform.assessments.contracts import AssessmentItem, SubmitAssessments
from tradingng_platform.assessments.service import AssessmentService
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.models import (
    AssessmentBatch,
    AssessmentRequest,
    AssessmentRun,
    AuditEvent,
    RunEvent,
)


async def _scalar_count(session, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_submission_is_transactional_and_idempotent(
    session_factory,
    instrument_classifier,
):
    service = AssessmentService(session_factory, instrument_classifier)
    principal = Principal(
        issuer="https://issuer.example/realms/tradingng",
        subject="alice-sub",
        actor_type="user",
        scopes=frozenset({"assessments:read", "assessments:submit"}),
        display_name="Alice",
        email="alice@example.com",
        roles=frozenset({"Analyst"}),
    )
    command = SubmitAssessments(
        items=[
            AssessmentItem(ticker="NVDA", analysis_date=date(2026, 7, 25)),
            AssessmentItem(ticker="TSLA", analysis_date=date(2026, 7, 25)),
        ],
        idempotency_key="foundation-20260725",  # gitleaks:allow
    )

    first = await service.submit(principal, command, request_id="request-one")
    second = await service.submit(principal, command, request_id="request-two")

    assert len(first) == 2
    assert [run.id for run in first] == [run.id for run in second]
    assert {run.status for run in first} == {RunStatus.QUEUED}
    async with session_factory() as session:
        assert await _scalar_count(session, AssessmentBatch) == 1
        assert await _scalar_count(session, AssessmentRequest) == 2
        assert await _scalar_count(session, AssessmentRun) == 2
        assert await _scalar_count(session, RunEvent) == 2
        assert await _scalar_count(session, AuditEvent) == 1
