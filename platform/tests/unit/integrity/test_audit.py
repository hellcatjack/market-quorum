import json
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.integrity.audit import RetrospectiveAuditService, audit_evidence
from tradingng_platform.integrity.contracts import IntegrityStatus
from tradingng_platform.integrity.financials import Availability
from tradingng_platform.models import (
    Artifact,
    AssessmentBatch,
    AssessmentRequest,
    AssessmentRun,
    Base,
    Instrument,
    RunIntegrityAssessment,
    User,
)


class StubResolver:
    def __init__(self, values):
        self.values = values

    def resolve(self, ticker, fiscal_end, frequency):
        return self.values.get(fiscal_end)


def _write_evidence(path, *, tool_name, output, arguments=None):
    path.write_text(
        json.dumps(
            {
                "tool_name": tool_name,
                "source": "alpha_vantage",
                "arguments": arguments or {"ticker": "NVDA"},
                "output": output,
                "output_sha256": "a" * 64,
                "collected_at": "2026-07-27T12:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_retrospective_audit_marks_confirmed_future_statement_at_risk(tmp_path):
    evidence = _write_evidence(
        tmp_path / "evidence.jsonl",
        tool_name="get_income_statement",
        output=json.dumps(
            {
                "annualReports": [],
                "quarterlyReports": [{"fiscalDateEnding": "2025-06-30"}],
            }
        ),
    )
    resolver = StubResolver(
        {date(2025, 6, 30): Availability(date(2025, 7, 24), "sec", "high")}
    )

    result = audit_evidence(
        evidence,
        ticker="NVDA",
        analysis_date=date(2025, 7, 1),
        resolver=resolver,
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )

    assert result.status is IntegrityStatus.AT_RISK
    assert "future_publication_exposed" in result.reason_codes


def test_retrospective_audit_unwraps_archived_statement_tool_message(tmp_path):
    evidence = _write_evidence(
        tmp_path / "evidence.jsonl",
        tool_name="get_income_statement",
        output={
            "content": json.dumps(
                {
                    "annualReports": [],
                    "quarterlyReports": [{"fiscalDateEnding": "2025-06-30"}],
                }
            ),
            "name": "get_income_statement",
            "type": "tool",
        },
    )
    resolver = StubResolver(
        {date(2025, 6, 30): Availability(date(2025, 7, 24), "sec", "high")}
    )

    result = audit_evidence(
        evidence,
        ticker="NVDA",
        analysis_date=date(2025, 7, 1),
        resolver=resolver,
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )

    assert result.status is IntegrityStatus.AT_RISK
    assert "future_publication_exposed" in result.reason_codes
    assert "no_statement_records" not in result.reason_codes


def test_retrospective_audit_accepts_statement_published_by_analysis_date(tmp_path):
    evidence = _write_evidence(
        tmp_path / "evidence.jsonl",
        tool_name="get_balance_sheet",
        output=json.dumps(
            {
                "annualReports": [],
                "quarterlyReports": [{"fiscalDateEnding": "2025-06-30"}],
            }
        ),
    )
    resolver = StubResolver(
        {date(2025, 6, 30): Availability(date(2025, 7, 24), "sec", "high")}
    )

    result = audit_evidence(
        evidence,
        ticker="NVDA",
        analysis_date=date(2025, 7, 24),
        resolver=resolver,
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )

    assert result.status is IntegrityStatus.SAFE
    assert "publication_verified" in result.reason_codes


def test_retrospective_audit_marks_unverified_statement_unknown(tmp_path):
    evidence = _write_evidence(
        tmp_path / "evidence.jsonl",
        tool_name="get_cashflow",
        output=json.dumps(
            {
                "annualReports": [],
                "quarterlyReports": [{"fiscalDateEnding": "2025-06-30"}],
            }
        ),
    )

    result = audit_evidence(
        evidence,
        ticker="NVDA",
        analysis_date=date(2025, 7, 24),
        resolver=StubResolver({}),
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )

    assert result.status is IntegrityStatus.UNKNOWN
    assert "publication_unverified" in result.reason_codes


def test_retrospective_audit_marks_missing_evidence_unknown(tmp_path):
    result = audit_evidence(
        tmp_path / "missing.jsonl",
        ticker="NVDA",
        analysis_date=date(2025, 7, 1),
        resolver=StubResolver({}),
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )

    assert result.status is IntegrityStatus.UNKNOWN
    assert result.reason_codes == ("evidence_missing",)


def test_retrospective_audit_detects_current_snapshot_exposure(tmp_path):
    evidence = _write_evidence(
        tmp_path / "evidence.jsonl",
        tool_name="get_fundamentals",
        output={"PERatio": "50"},
    )

    result = audit_evidence(
        evidence,
        ticker="NVDA",
        analysis_date=date(2025, 7, 1),
        resolver=StubResolver({}),
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )

    assert result.status is IntegrityStatus.AT_RISK
    assert "current_snapshot_exposed" in result.reason_codes


def test_retrospective_audit_accepts_archived_fred_vintage_message(tmp_path):
    evidence = _write_evidence(
        tmp_path / "evidence.jsonl",
        tool_name="get_macro_indicators",
        output={
            "content": (
                "POINT_IN_TIME_VINTAGE: FRED observations are limited to values "
                "available on 2025-07-01."
            ),
            "name": "get_macro_indicators",
            "type": "tool",
        },
    )

    result = audit_evidence(
        evidence,
        ticker="NVDA",
        analysis_date=date(2025, 7, 1),
        resolver=StubResolver({}),
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )

    assert result.status is IntegrityStatus.SAFE
    assert "sealed_evidence_verified" in result.reason_codes
    assert "fred_vintage_applied" in result.reason_codes


async def test_retrospective_service_archives_once_and_is_idempotent(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = LocalArtifactStore(tmp_path / "artifacts")
    source = _write_evidence(
        tmp_path / "source.jsonl",
        tool_name="get_income_statement",
        output=json.dumps(
            {
                "annualReports": [],
                "quarterlyReports": [{"fiscalDateEnding": "2025-06-30"}],
            }
        ),
    )
    try:
        async with sessions() as session, session.begin():
            user = User(
                issuer="issuer",
                subject="audit-owner",
                display_name="Owner",
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
                idempotency_key="audit-batch",
                defaults_json={},
            )
            session.add(batch)
            await session.flush()
            request = AssessmentRequest(
                batch_id=batch.id,
                instrument_id=instrument.id,
                analysis_date=date(2025, 7, 1),
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
            await session.flush()
            run_id = run.id

        service = RetrospectiveAuditService(
            sessions,
            store,
            StubResolver(
                {date(2025, 6, 30): Availability(date(2025, 7, 24), "sec", "high")}
            ),
            clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
        )

        first = await service.audit_one(run_id)
        second = await service.audit_one(run_id)

        async with sessions() as session:
            count = await session.scalar(
                select(func.count()).select_from(RunIntegrityAssessment)
            )
            retro = await session.get(Artifact, first.artifact_id)
        assert first.id == second.id
        assert first.status == "at_risk"
        assert count == 1
        assert retro.kind == "point_in_time_integrity_retro"
    finally:
        await engine.dispose()
