from datetime import date

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradingng_platform.assessments.contracts import MemoryMode
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.memory.context import (
    MemoryCandidate,
    MemorySnapshot,
    build_memory_snapshot,
)
from tradingng_platform.models import (
    AssessmentRequest,
    AssessmentRun,
    Decision,
    Instrument,
    Validation,
)


class HistoricalMemoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def build(
        self,
        ticker: str,
        analysis_date: date,
        mode: MemoryMode,
        *,
        limit: int = 5,
    ) -> MemorySnapshot:
        if mode is MemoryMode.INDEPENDENT:
            return build_memory_snapshot(mode, ticker, analysis_date, (), limit=limit)

        rows = (
            await self.session.execute(
                select(
                    AssessmentRun,
                    AssessmentRequest,
                    Instrument,
                    Decision,
                    Validation,
                )
                .join(AssessmentRequest, AssessmentRun.request_id == AssessmentRequest.id)
                .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
                .join(Decision, Decision.run_id == AssessmentRun.id)
                .join(Validation, Validation.run_id == AssessmentRun.id)
                .where(
                    Instrument.canonical_ticker == ticker,
                    AssessmentRun.status == RunStatus.SUCCEEDED.value,
                    AssessmentRequest.analysis_date < analysis_date,
                    Validation.status == "completed",
                    Validation.observed_at.is_not(None),
                    Validation.raw_return.is_not(None),
                    Validation.alpha.is_not(None),
                    Validation.max_adverse_excursion.is_not(None),
                    Validation.max_favorable_excursion.is_not(None),
                )
                .order_by(
                    AssessmentRequest.analysis_date.desc(),
                    AssessmentRun.created_at.desc(),
                    Validation.horizon.desc(),
                )
            )
        ).all()
        candidates = []
        for run, request, instrument, decision, validation in rows:
            trigger_results = dict(validation.trigger_results_json or {})
            try:
                exit_session = date.fromisoformat(str(trigger_results["exit_session"]))
                candidate = MemoryCandidate(
                    source_run_id=run.id,
                    validation_id=validation.id,
                    ticker=instrument.canonical_ticker,
                    analysis_date=request.analysis_date,
                    exit_session=exit_session,
                    horizon=validation.horizon,
                    rating=decision.rating,
                    executive_summary=decision.executive_summary,
                    investment_thesis=decision.investment_thesis,
                    price_target=decision.price_target,
                    time_horizon=decision.time_horizon,
                    raw_return=validation.raw_return,
                    alpha=validation.alpha,
                    max_adverse_excursion=validation.max_adverse_excursion,
                    max_favorable_excursion=validation.max_favorable_excursion,
                    direction_correct=_optional_bool(trigger_results.get("direction_correct")),
                    price_target_hit=_optional_bool(trigger_results.get("price_target_hit")),
                )
            except (KeyError, TypeError, ValueError, ValidationError):
                continue
            candidates.append(candidate)
        return build_memory_snapshot(
            mode,
            ticker,
            analysis_date,
            candidates,
            limit=limit,
        )


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
