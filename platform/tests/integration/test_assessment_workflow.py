from datetime import date

import pytest
from sqlalchemy import select

from tradingng_platform.assessments.contracts import (
    AssessmentItem,
    RunListFilters,
    SubmitAssessments,
)
from tradingng_platform.assessments.service import (
    AssessmentAccessDenied,
    AssessmentIdempotencyConflict,
    AssessmentService,
)
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.models import AssessmentRun, AuditEvent, RunConfigSnapshot


def _analyst(subject):
    return Principal(
        "issuer",
        subject,
        "user",
        frozenset({"assessments:submit", "assessments:read", "assessments:cancel"}),
        roles=frozenset({"Analyst"}),
    )


async def test_cancel_retry_cursor_events_and_owner_invariants(
    session_factory,
    instrument_classifier,
):
    service = AssessmentService(session_factory, instrument_classifier)
    owner = _analyst("owner")
    runs = await service.submit(
        owner,
        SubmitAssessments(
            items=[
                AssessmentItem(ticker=ticker, analysis_date=date(2026, 7, 25))
                for ticker in ("NVDA", "TSLA", "GLD")
            ],
            idempotency_key="workflow-20260725",
        ),
        "request-submit",
    )

    first_page = await service.list(owner, RunListFilters(limit=2))
    second_page = await service.list(
        owner,
        RunListFilters(limit=2, cursor=first_page.next_cursor),
    )
    assert len(first_page.items) == 2
    assert first_page.next_cursor is not None
    assert len(second_page.items) == 1
    assert {item.id for item in first_page.items + second_page.items} == {run.id for run in runs}

    cancelled = await service.cancel(owner, runs[0].id, "request-cancel")
    assert cancelled.status == RunStatus.CANCELLED
    with pytest.raises(AssessmentAccessDenied):
        await service.cancel(_analyst("other"), runs[1].id, "request-other")

    retried = await service.retry(owner, runs[0].id, "request-retry")
    assert retried.id != runs[0].id
    assert retried.status == RunStatus.QUEUED
    assert retried.attempt == 2
    events = await service.events(owner, runs[0].id)
    assert [event.event_type for event in events] == [
        "assessment.queued",
        "assessment.cancelled",
    ]
    comparison = await service.compare(owner, [runs[0].id, retried.id])
    assert comparison.ratings == {runs[0].id: None, retried.id: None}
    assert "status" in comparison.changed_sections

    async with session_factory() as session:
        retry_row = await session.get(AssessmentRun, retried.id)
        original = await session.get(AssessmentRun, runs[0].id)
        actions = tuple(
            await session.scalars(select(AuditEvent.action).order_by(AuditEvent.created_at))
        )
    assert retry_row.retry_of_run_id == original.id
    assert original.status == RunStatus.CANCELLED.value
    assert actions == (
        "assessment.submit",
        "assessment.cancel",
        "assessment.retry",
    )


async def test_reusing_idempotency_key_with_different_payload_is_rejected(
    session_factory,
    instrument_classifier,
):
    service = AssessmentService(session_factory, instrument_classifier)
    owner = _analyst("idempotency-owner")
    first = SubmitAssessments(
        items=[AssessmentItem(ticker="NVDA", analysis_date=date(2026, 7, 25))],
        idempotency_key="same-key-20260725",
    )
    second = SubmitAssessments(
        items=[AssessmentItem(ticker="TSLA", analysis_date=date(2026, 7, 25))],
        idempotency_key="same-key-20260725",
    )
    await service.submit(owner, first, "request-first")

    with pytest.raises(AssessmentIdempotencyConflict):
        await service.submit(owner, second, "request-second")


async def test_run_detail_exposes_immutable_execution_metadata(
    session_factory,
    instrument_classifier,
):
    service = AssessmentService(session_factory, instrument_classifier)
    owner = _analyst("detail-owner")
    run_view = (
        await service.submit(
            owner,
            SubmitAssessments(
                items=[AssessmentItem(ticker="SPCX", analysis_date=date(2026, 7, 25))],
                idempotency_key="detail-20260725",
            ),
            "request-detail",
        )
    )[0]
    async with session_factory() as session, session.begin():
        snapshot = RunConfigSnapshot(
            content_json={
                "request": {"depth": "deep", "language": "Chinese"},
                "resolved": {"debate_rounds": 3, "risk_rounds": 3},
                "gateway": {
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "xhigh",
                    "snapshot_id": "gateway-snapshot",
                },
                "source": {
                    "root_commit": "root-commit",
                    "tradingagents_commit": "ta-commit",
                },
                "prompt_schema_version": "v1",
                "data_vendors": {"market": "yfinance"},
                "tool_vendors": {},
                "memory": {
                    "mode": "historical",
                    "snapshot_sha256": "b" * 64,
                    "entries": [
                        {
                            "source_run_id": "00000000-0000-0000-0000-000000000701",
                            "validation_id": "00000000-0000-0000-0000-000000000702",
                            "analysis_date": "2026-07-01",
                            "exit_session": "2026-07-06",
                            "horizon": 5,
                            "rating": "Buy",
                            "raw_return": "0.05",
                            "alpha": "0.02",
                            "direction_correct": True,
                            "price_target_hit": False,
                            "content_sha256": "c" * 64,
                            "decision": "not exposed in the detail view",
                            "reflection": "not exposed in the detail view",
                        }
                    ],
                },
            },
            sha256="a" * 64,
            gateway_snapshot_id="gateway-snapshot",
        )
        session.add(snapshot)
        await session.flush()
        run = await session.get(AssessmentRun, run_view.id)
        run.config_snapshot_id = snapshot.id

    detail = await service.get(owner, run_view.id)

    assert detail.config_snapshot_sha256 == "a" * 64
    assert detail.gateway_model == "gpt-5.6-sol"
    assert detail.gateway_reasoning_effort == "xhigh"
    assert detail.root_commit == "root-commit"
    assert detail.tradingagents_commit == "ta-commit"
    assert detail.prompt_schema_version == "v1"
    assert detail.request_config == {"depth": "deep", "language": "Chinese"}
    assert detail.resolved_config == {"debate_rounds": 3, "risk_rounds": 3}
    assert detail.memory.mode.value == "historical"
    assert detail.memory.snapshot_sha256 == "b" * 64
    assert detail.memory.sources[0].horizon == 5
    assert not hasattr(detail.memory.sources[0], "decision")
