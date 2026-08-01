from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from tradingng_platform.artifacts.store import LocalArtifactStore
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
from tradingng_platform.models import (
    Artifact,
    AssessmentBatch,
    AssessmentDataRequirement,
    AssessmentRequest,
    AssessmentRun,
    AuditEvent,
    Comment,
    Decision,
    DecisionPriceBasis,
    EvidenceItem,
    Instrument,
    Review,
    RunConfigSnapshot,
    RunEvent,
    RunIntegrityAssessment,
    RunStep,
    User,
    Validation,
    Webhook,
    WebhookDelivery,
)


def _analyst(subject):
    return Principal(
        "issuer",
        subject,
        "user",
        frozenset({"assessments:submit", "assessments:read", "assessments:cancel"}),
        roles=frozenset({"Analyst"}),
    )


def _admin():
    return Principal(
        "issuer",
        "admin",
        "user",
        frozenset({"assessments:admin"}),
        roles=frozenset({"Admin"}),
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
                    "routes": {
                        "fast": {
                            "model": "gpt-5.6-terra",
                            "reasoning_effort": "medium",
                        },
                        "slow": {
                            "model": "gpt-5.6-sol",
                            "reasoning_effort": "high",
                        },
                    },
                    "routing_snapshot_id": "routing-snapshot",
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
    assert detail.gateway_fast_model == "gpt-5.6-terra"
    assert detail.gateway_fast_reasoning_effort == "medium"
    assert detail.gateway_slow_model == "gpt-5.6-sol"
    assert detail.gateway_slow_reasoning_effort == "high"
    assert detail.model_routing_snapshot_id == "routing-snapshot"
    assert detail.root_commit == "root-commit"
    assert detail.tradingagents_commit == "ta-commit"
    assert detail.prompt_schema_version == "v1"
    assert detail.request_config == {"depth": "deep", "language": "Chinese"}
    assert detail.resolved_config == {"debate_rounds": 3, "risk_rounds": 3}
    assert detail.memory.mode.value == "historical"
    assert detail.memory.snapshot_sha256 == "b" * 64
    assert detail.memory.sources[0].horizon == 5
    assert not hasattr(detail.memory.sources[0], "decision")


async def test_delete_removes_complete_run_graph_and_orphans(
    session_factory,
    instrument_classifier,
    tmp_path,
):
    artifact_store = LocalArtifactStore(tmp_path / "artifacts")
    job_dir = tmp_path / "jobs"
    service = AssessmentService(
        session_factory,
        instrument_classifier,
        artifact_store,
        job_dir,
    )
    owner = _analyst("delete-owner")
    run_view = (
        await service.submit(
            owner,
            SubmitAssessments(
                items=[AssessmentItem(ticker="NVDA", analysis_date=date(2026, 7, 25))],
                idempotency_key="delete-complete-graph-20260725",
            ),
            "request-submit-delete",
        )
    )[0]
    await service.cancel(owner, run_view.id, "request-cancel-delete")

    async with session_factory() as session, session.begin():
        run = await session.get(AssessmentRun, run_view.id)
        request = await session.get(AssessmentRequest, run.request_id)
        owner_row = await session.scalar(select(User).where(User.subject == "delete-owner"))
        event = await session.scalar(
            select(RunEvent).where(RunEvent.run_id == run_view.id).limit(1)
        )
        snapshot = RunConfigSnapshot(
            content_json={"request": {"depth": "deep"}},
            sha256="d" * 64,
            gateway_snapshot_id="delete-snapshot",
        )
        session.add(snapshot)
        await session.flush()
        run.config_snapshot_id = snapshot.id

        artifact = Artifact(
            run_id=run_view.id,
            kind="complete_report",
            media_type="text/markdown",
            size=6,
            sha256="e" * 64,
            storage_key=f"{run_view.id}/complete_report/{'e' * 64}",
            redacted=True,
            retention_class="permanent",
            metadata_json={},
        )
        session.add(artifact)
        await session.flush()
        session.add_all(
            [
                AssessmentDataRequirement(
                    run_id=run_view.id,
                    provider_request_id="17",
                    external_request_key=f"assessment:{run_view.id}",
                    required_products_json=["market", "fundamental"],
                    status="failed",
                    progress_json={"stage": "failed"},
                ),
                RunStep(
                    run_id=run_view.id,
                    name="finalizing",
                    status="completed",
                    attempt=1,
                ),
                Decision(
                    run_id=run_view.id,
                    rating="Buy",
                    executive_summary="summary",
                    investment_thesis="thesis",
                    price_target=Decimal("200"),
                    time_horizon="12 months",
                    structured_json={},
                ),
                EvidenceItem(
                    run_id=run_view.id,
                    source="test",
                    tool_name="test_tool",
                    arguments_json={},
                    collected_at=datetime.now(timezone.utc),
                    artifact_id=artifact.id,
                    content_hash="f" * 64,
                ),
                Review(
                    run_id=run_view.id,
                    reviewer_id=owner_row.id,
                    verdict="approved",
                    comment="review",
                ),
                Comment(
                    run_id=run_view.id,
                    author_id=owner_row.id,
                    body="comment",
                ),
                Validation(
                    run_id=run_view.id,
                    horizon=20,
                    status="completed",
                    scheduled_for=datetime.now(timezone.utc),
                    data_artifact_id=artifact.id,
                ),
                DecisionPriceBasis(
                    run_id=run_view.id,
                    status="completed",
                    target_price=Decimal("200"),
                    reference_session=date(2026, 7, 25),
                    reference_close=Decimal("180"),
                ),
                RunIntegrityAssessment(
                    run_id=run_view.id,
                    artifact_id=artifact.id,
                    policy_version="integrity.v1",
                    status="safe",
                    audit_mode="point_in_time",
                    temporal_scope="historical",
                    analysis_date=date(2026, 7, 25),
                    checked_at=datetime.now(timezone.utc),
                    reason_codes_json=[],
                    tool_findings_json=[],
                    input_fingerprint="1" * 64,
                ),
            ]
        )
        webhook = Webhook(
            owner_id=owner_row.id,
            endpoint="https://example.test/hook",
            event_types_json=["assessment.cancelled"],
            encrypted_secret="encrypted",
            status="active",
        )
        session.add(webhook)
        await session.flush()
        session.add(
            WebhookDelivery(
                webhook_id=webhook.id,
                event_id=event.id,
                status="delivered",
            )
        )
        request_id = request.id
        batch_id = request.batch_id
        snapshot_id = snapshot.id
        instrument_id = request.instrument_id

    artifact_path = artifact_store.root / str(run_view.id) / "complete_report" / "report.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("report", encoding="utf-8")
    job_path = job_dir / str(run_view.id) / "worker.log"
    job_path.parent.mkdir(parents=True)
    job_path.write_text("log", encoding="utf-8")

    deleted = await service.delete(_admin(), run_view.id, "request-delete")

    assert deleted.run_id == run_view.id
    assert not artifact_path.parent.parent.exists()
    assert not job_path.parent.exists()
    async with session_factory() as session:
        owned_models = (
            WebhookDelivery,
            AssessmentDataRequirement,
            RunIntegrityAssessment,
            Validation,
            DecisionPriceBasis,
            EvidenceItem,
            Review,
            Comment,
            Decision,
            RunStep,
            Artifact,
            RunEvent,
            AssessmentRun,
        )
        for model in owned_models:
            assert await session.scalar(select(func.count()).select_from(model)) == 0
        assert await session.get(AssessmentRequest, request_id) is None
        assert await session.get(AssessmentBatch, batch_id) is None
        assert await session.get(RunConfigSnapshot, snapshot_id) is None
        assert await session.get(Instrument, instrument_id) is not None
        audit = await session.scalar(
            select(AuditEvent)
            .where(AuditEvent.action == "assessment.delete")
            .order_by(AuditEvent.created_at.desc())
        )
    assert audit.object_id == str(run_view.id)
    assert audit.metadata_json["ticker"] == "NVDA"
