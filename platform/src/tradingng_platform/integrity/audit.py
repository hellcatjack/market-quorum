from __future__ import annotations

import hashlib
import json
import re
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.assessments.repository import AssessmentRepository
from tradingng_platform.integrity.contracts import (
    CURRENT_POLICY_VERSION,
    IntegrityDocument,
    IntegrityStatus,
)
from tradingng_platform.integrity.financials import FilingAvailabilityResolver
from tradingng_platform.integrity.policy import PointInTimeRecorder
from tradingng_platform.integrity.repository import IntegrityRepository
from tradingng_platform.models import (
    Artifact,
    AssessmentRequest,
    AssessmentRun,
    Instrument,
    RunIntegrityAssessment,
)

_FINANCIAL_TOOLS = frozenset(
    {"get_balance_sheet", "get_cashflow", "get_income_statement"}
)
_CURRENT_SNAPSHOT_TOOLS = frozenset(
    {
        "get_fundamentals",
        "get_insider_transactions",
        "get_prediction_markets",
        "fetch_stocktwits_messages",
        "fetch_reddit_posts",
    }
)
_DATE_BOUNDED_TOOLS = frozenset(
    {
        "get_stock_data",
        "get_verified_market_snapshot",
        "get_indicators",
        "get_news",
        "get_global_news",
    }
)
_ISO_DATE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")


class RetrospectiveAuditService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        artifact_store: LocalArtifactStore,
        resolver: FilingAvailabilityResolver,
        *,
        clock=lambda: datetime.now(timezone.utc),
    ):
        self.sessions = sessions
        self.artifact_store = artifact_store
        self.resolver = resolver
        self.clock = clock

    async def audit_one(self, run_id: uuid.UUID) -> RunIntegrityAssessment:
        async with self.sessions() as session, session.begin():
            row = (
                await session.execute(
                    select(AssessmentRun, AssessmentRequest, Instrument)
                    .join(AssessmentRequest, AssessmentRun.request_id == AssessmentRequest.id)
                    .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
                    .where(AssessmentRun.id == run_id)
                    .with_for_update(of=AssessmentRun)
                )
            ).one_or_none()
            if row is None:
                raise ValueError("assessment run was not found")
            run, request, instrument = row
            if run.status != "succeeded":
                raise ValueError("only succeeded assessment runs can be audited")
            evidence_artifact = await session.scalar(
                select(Artifact)
                .where(Artifact.run_id == run_id, Artifact.kind == "evidence")
                .order_by(Artifact.created_at.desc(), Artifact.id.desc())
                .limit(1)
            )
            document = self._audit_artifact(
                evidence_artifact,
                ticker=instrument.canonical_ticker,
                analysis_date=request.analysis_date,
            )
            repository = IntegrityRepository(session)
            existing = await repository.find_document(run_id, document)
            if existing is not None:
                return existing
            integrity_artifact = await self._archive_document(session, run_id, document)
            persisted = await repository.persist_document(
                run_id,
                document,
                artifact_id=integrity_artifact.id,
                audit_mode="retrospective",
            )
            await AssessmentRepository(session).append_event(
                run_id,
                "assessment.integrity_reaudited",
                {
                    "integrity_id": str(persisted.id),
                    "policy_version": persisted.policy_version,
                    "status": persisted.status,
                },
            )
            return persisted

    async def audit_pending(self, *, limit: int = 50) -> list[RunIntegrityAssessment]:
        if not 1 <= limit <= 500:
            raise ValueError("integrity audit limit must be between 1 and 500")
        async with self.sessions() as session:
            run_ids = list(
                await session.scalars(
                    select(AssessmentRun.id)
                    .where(
                        AssessmentRun.status == "succeeded",
                        ~exists().where(
                            RunIntegrityAssessment.run_id == AssessmentRun.id,
                            RunIntegrityAssessment.policy_version == CURRENT_POLICY_VERSION,
                        ),
                    )
                    .order_by(AssessmentRun.created_at, AssessmentRun.id)
                    .limit(limit)
                )
            )
        return [await self.audit_one(run_id) for run_id in run_ids]

    def _audit_artifact(
        self,
        artifact: Artifact | None,
        *,
        ticker: str,
        analysis_date: date,
    ) -> IntegrityDocument:
        if artifact is None:
            return _unknown_document(
                analysis_date,
                "evidence_missing",
                now=self.clock(),
            )
        if not self.artifact_store.verify(artifact.storage_key, artifact.sha256):
            return _unknown_document(
                analysis_date,
                "evidence_hash_mismatch",
                now=self.clock(),
            )
        return audit_evidence(
            self.artifact_store.resolve(artifact.storage_key),
            ticker=ticker,
            analysis_date=analysis_date,
            resolver=self.resolver,
            now=self.clock(),
        )

    async def _archive_document(
        self,
        session: AsyncSession,
        run_id: uuid.UUID,
        document: IntegrityDocument,
    ) -> Artifact:
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as source:
            source.write(document.model_dump_json())
            source.flush()
            stored = self.artifact_store.put(
                run_id,
                "point_in_time_integrity_retro",
                "application/json",
                Path(source.name),
            )
        artifact = Artifact(
            run_id=run_id,
            kind=stored.kind,
            media_type=stored.media_type,
            size=stored.size,
            sha256=stored.sha256,
            storage_key=stored.storage_key,
            redacted=True,
            retention_class="permanent",
            metadata_json={
                "policy_version": document.policy_version,
                "input_fingerprint": document.input_fingerprint,
            },
        )
        session.add(artifact)
        await session.flush()
        return artifact


def audit_evidence(
    evidence_path: Path,
    *,
    ticker: str,
    analysis_date: date,
    resolver: FilingAvailabilityResolver,
    now: datetime | None = None,
) -> IntegrityDocument:
    recorder = PointInTimeRecorder(analysis_date, now=now)
    if not evidence_path.is_file():
        recorder.record("evidence", IntegrityStatus.UNKNOWN, "evidence_missing")
        return recorder.finalize()
    try:
        content = evidence_path.read_bytes()
    except OSError:
        recorder.record("evidence", IntegrityStatus.UNKNOWN, "evidence_unreadable")
        return recorder.finalize()
    recorder.record(
        "evidence",
        IntegrityStatus.SAFE,
        "sealed_evidence_verified",
        {"sha256": hashlib.sha256(content).hexdigest()},
    )
    observed = 0
    for raw_line in content.decode("utf-8", errors="replace").splitlines():
        if not raw_line.strip():
            continue
        try:
            item = json.loads(raw_line)
        except json.JSONDecodeError:
            recorder.record("evidence", IntegrityStatus.UNKNOWN, "evidence_line_invalid")
            continue
        if not isinstance(item, dict):
            recorder.record("evidence", IntegrityStatus.UNKNOWN, "evidence_line_invalid")
            continue
        tool_name = item.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name:
            recorder.record("evidence", IntegrityStatus.UNKNOWN, "tool_name_missing")
            continue
        observed += 1
        output = item.get("output")
        arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        if tool_name in _FINANCIAL_TOOLS:
            _audit_financial_output(
                recorder,
                tool_name,
                output,
                ticker=ticker,
                analysis_date=analysis_date,
                resolver=resolver,
            )
        elif tool_name in _CURRENT_SNAPSHOT_TOOLS:
            rendered = _render(output)
            blocked = (
                "POINT_IN_TIME_DATA_UNAVAILABLE:" in rendered
                or "<point-in-time unavailable:" in rendered
            )
            recorder.record(
                tool_name,
                IntegrityStatus.SAFE if blocked else IntegrityStatus.AT_RISK,
                "current_snapshot_blocked" if blocked else "current_snapshot_exposed",
            )
        elif tool_name == "get_macro_indicators":
            vintage = _render(output).startswith("POINT_IN_TIME_VINTAGE:")
            recorder.record(
                tool_name,
                IntegrityStatus.SAFE if vintage else IntegrityStatus.AT_RISK,
                "fred_vintage_applied" if vintage else "macro_vintage_missing",
            )
        elif tool_name in _DATE_BOUNDED_TOOLS:
            _audit_date_bounded_output(
                recorder,
                tool_name,
                output,
                arguments,
                analysis_date,
            )
        else:
            recorder.record(tool_name, IntegrityStatus.UNKNOWN, "unregistered_tool")
    if observed == 0:
        recorder.record("evidence", IntegrityStatus.UNKNOWN, "evidence_empty")
    return recorder.finalize()


def _unknown_document(
    analysis_date: date,
    reason_code: str,
    *,
    now: datetime,
) -> IntegrityDocument:
    recorder = PointInTimeRecorder(analysis_date, now=now)
    recorder.record("evidence", IntegrityStatus.UNKNOWN, reason_code)
    return recorder.finalize()


def _audit_financial_output(
    recorder: PointInTimeRecorder,
    tool_name: str,
    output,
    *,
    ticker: str,
    analysis_date: date,
    resolver: FilingAvailabilityResolver,
) -> None:
    payload = _mapping(output)
    if payload is None:
        recorder.record(tool_name, IntegrityStatus.UNKNOWN, "statement_output_unparseable")
        return
    observed = 0
    for key, frequency in (("annualReports", "annual"), ("quarterlyReports", "quarterly")):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            observed += 1
            if not isinstance(row, dict):
                recorder.record(tool_name, IntegrityStatus.UNKNOWN, "statement_row_invalid")
                continue
            fiscal_end = _parse_date(row.get("fiscalDateEnding"))
            if fiscal_end is None:
                recorder.record(tool_name, IntegrityStatus.UNKNOWN, "fiscal_date_invalid")
                continue
            availability = resolver.resolve(ticker, fiscal_end, frequency)
            details = {
                "frequency": frequency,
                "fiscal_date_ending": fiscal_end.isoformat(),
            }
            if availability is None:
                recorder.record(
                    tool_name,
                    IntegrityStatus.UNKNOWN,
                    "publication_unverified",
                    details,
                )
                continue
            details.update(
                {
                    "available_at": availability.available_at.isoformat(),
                    "availability_source": availability.source,
                    "assurance": availability.assurance,
                }
            )
            exposed_future = availability.available_at > analysis_date
            recorder.record(
                tool_name,
                IntegrityStatus.AT_RISK if exposed_future else IntegrityStatus.SAFE,
                "future_publication_exposed" if exposed_future else "publication_verified",
                details,
            )
    if observed == 0:
        recorder.record(tool_name, IntegrityStatus.SAFE, "no_statement_records")


def _audit_date_bounded_output(
    recorder: PointInTimeRecorder,
    tool_name: str,
    output,
    arguments: dict,
    analysis_date: date,
) -> None:
    dates = _dates_in({"arguments": arguments, "output": output})
    if not dates:
        recorder.record(tool_name, IntegrityStatus.UNKNOWN, "date_boundary_unverified")
        return
    future_dates = [value for value in dates if value > analysis_date]
    recorder.record(
        tool_name,
        IntegrityStatus.AT_RISK if future_dates else IntegrityStatus.SAFE,
        "future_dated_output" if future_dates else "date_boundary_verified",
        {
            "latest_date": max(dates).isoformat(),
            "analysis_date": analysis_date.isoformat(),
        },
    )


def _mapping(value) -> dict | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _render(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def _parse_date(value) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _dates_in(value) -> set[date]:
    rendered = _render(value)
    dates = set()
    for match in _ISO_DATE.finditer(rendered):
        parsed = _parse_date(match.group(1))
        if parsed is not None:
            dates.add(parsed)
    return dates
