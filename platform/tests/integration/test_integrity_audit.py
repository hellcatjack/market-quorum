import json
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.integrity.audit import RetrospectiveAuditService
from tradingng_platform.models import (
    Artifact,
    AssessmentBatch,
    AssessmentRequest,
    AssessmentRun,
    Decision,
    Instrument,
    RunIntegrityAssessment,
    User,
    Validation,
)


class _NoFinancialStatements:
    def resolve(self, ticker, fiscal_end, frequency):
        raise AssertionError("this test should not resolve a financial statement")


async def test_bounded_audit_classifies_runs_without_mutating_results(
    session_factory,
    tmp_path,
):
    store = LocalArtifactStore(tmp_path / "artifacts")
    async with session_factory() as session, session.begin():
        user = User(
            issuer="issuer",
            subject="integrity-auditor",
            display_name="Integrity Auditor",
            email=None,
        )
        instrument = Instrument(
            canonical_ticker="NVDA",
            asset_type="stock",
            exchange="NASDAQ",
            name="NVIDIA",
            metadata_json={},
        )
        session.add_all([user, instrument])
        await session.flush()
        batch = AssessmentBatch(
            submitted_by=user.id,
            idempotency_key="integrity-audit-integration",
            defaults_json={},
        )
        session.add(batch)
        await session.flush()

        runs = []
        for day in (1, 2, 3):
            request = AssessmentRequest(
                batch_id=batch.id,
                instrument_id=instrument.id,
                analysis_date=date(2025, 7, day),
                requested_config_json={},
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
            runs.append(run)
            session.add(
                Decision(
                    run_id=run.id,
                    rating="Hold",
                    executive_summary=f"Decision {day}",
                    investment_thesis="Sealed thesis",
                    price_target=Decimal("100.00"),
                    time_horizon="20 trading days",
                    structured_json={"day": day},
                )
            )
            session.add(
                Validation(
                    run_id=run.id,
                    horizon=20,
                    status="completed",
                    scheduled_for=datetime(2025, 8, day, tzinfo=timezone.utc),
                    raw_return=Decimal("0.01"),
                    benchmark_return=Decimal("0.005"),
                    alpha=Decimal("0.005"),
                    trigger_results_json={},
                )
            )

        for run, output in (
            (runs[0], "POINT_IN_TIME_DATA_UNAVAILABLE: historical snapshot blocked"),
            (runs[1], {"PERatio": "50"}),
        ):
            source = tmp_path / f"{run.id}.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "tool_name": "get_fundamentals",
                        "arguments": {"ticker": "NVDA"},
                        "output": output,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            stored = store.put(run.id, "evidence", "application/x-ndjson", source)
            session.add(
                Artifact(
                    run_id=run.id,
                    kind=stored.kind,
                    media_type=stored.media_type,
                    size=stored.size,
                    sha256=stored.sha256,
                    storage_key=stored.storage_key,
                    redacted=True,
                )
            )

    async with session_factory() as session:
        decisions_before = list(
            await session.execute(
                select(
                    Decision.id,
                    Decision.run_id,
                    Decision.rating,
                    Decision.structured_json,
                ).order_by(Decision.run_id)
            )
        )
        validations_before = list(
            await session.execute(
                select(
                    Validation.id,
                    Validation.run_id,
                    Validation.status,
                    Validation.raw_return,
                ).order_by(Validation.run_id)
            )
        )

    service = RetrospectiveAuditService(
        session_factory,
        store,
        _NoFinancialStatements(),
        clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
    )
    audited = await service.audit_pending(limit=3)

    async with session_factory() as session:
        decisions_after = list(
            await session.execute(
                select(
                    Decision.id,
                    Decision.run_id,
                    Decision.rating,
                    Decision.structured_json,
                ).order_by(Decision.run_id)
            )
        )
        validations_after = list(
            await session.execute(
                select(
                    Validation.id,
                    Validation.run_id,
                    Validation.status,
                    Validation.raw_return,
                ).order_by(Validation.run_id)
            )
        )
        verdicts = list(await session.scalars(select(RunIntegrityAssessment)))

    assert Counter(row.status for row in audited) == {
        "safe": 1,
        "at_risk": 1,
        "unknown": 1,
    }
    assert len(verdicts) == 3
    assert decisions_after == decisions_before
    assert validations_after == validations_before
