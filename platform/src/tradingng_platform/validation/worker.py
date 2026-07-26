from __future__ import annotations

import json
import tempfile
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.assessments.repository import AssessmentRepository
from tradingng_platform.models import (
    Artifact,
    AssessmentRequest,
    AssessmentRun,
    Decision,
    Instrument,
    RunConfigSnapshot,
    Validation,
)
from tradingng_platform.validation.calculator import InsufficientSessions, calculate_outcome
from tradingng_platform.validation.prices import PriceProvider

_RETRY_MINUTES = (5, 10, 20, 30)


@dataclass(frozen=True)
class ClaimedValidation:
    id: uuid.UUID
    run_id: uuid.UUID
    ticker: str
    benchmark_ticker: str
    analysis_date: date
    horizon: int
    rating: str
    price_target: Decimal | None


class ValidationWorker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: PriceProvider,
        artifact_store: LocalArtifactStore,
        max_running: int = 2,
    ):
        self.sessions = sessions
        self.provider = provider
        self.artifact_store = artifact_store
        self.max_running = max_running

    async def run_once(self, now: datetime | None = None) -> bool:
        observed_now = now or datetime.now(timezone.utc)
        claim = await self._claim(observed_now)
        if claim is None:
            return False
        try:
            start = claim.analysis_date - timedelta(days=7)
            instrument = await self.provider.history(claim.ticker, start, observed_now.date())
            benchmark = await self.provider.history(
                claim.benchmark_ticker,
                start,
                observed_now.date(),
            )
            calculation = calculate_outcome(
                instrument,
                benchmark,
                analysis_date=claim.analysis_date,
                horizon=claim.horizon,
                rating=claim.rating,
                price_target=claim.price_target,
            )
            await self._complete(claim, instrument, benchmark, calculation, observed_now)
        except InsufficientSessions:
            await self._retry(claim.id, observed_now, next_day=True, code="future_sessions")
        except (httpx.HTTPError, OSError, TimeoutError):
            await self._retry(claim.id, observed_now, next_day=False, code="provider_unavailable")
        except ValueError:
            await self._terminal_error(claim.id, "unavailable", "invalid_market_data")
        except Exception:
            await self._terminal_error(claim.id, "failed", "calculation_error")
        return True

    async def _claim(self, now: datetime) -> ClaimedValidation | None:
        async with self.sessions() as session, session.begin():
            running = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Validation)
                    .where(Validation.status == "running")
                )
                or 0
            )
            if running >= self.max_running:
                return None
            row = (
                await session.execute(
                    select(
                        Validation,
                        AssessmentRequest,
                        Instrument,
                        RunConfigSnapshot,
                        Decision,
                    )
                    .join(AssessmentRun, Validation.run_id == AssessmentRun.id)
                    .join(AssessmentRequest, AssessmentRun.request_id == AssessmentRequest.id)
                    .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
                    .outerjoin(
                        RunConfigSnapshot,
                        AssessmentRun.config_snapshot_id == RunConfigSnapshot.id,
                    )
                    .join(Decision, Decision.run_id == AssessmentRun.id)
                    .where(
                        or_(
                            (Validation.status == "scheduled") & (Validation.scheduled_for <= now),
                            (Validation.status == "retry_wait")
                            & (Validation.next_attempt_at <= now),
                        )
                    )
                    .order_by(Validation.scheduled_for, Validation.id)
                    .with_for_update(of=Validation, skip_locked=True)
                    .limit(1)
                )
            ).one_or_none()
            if row is None:
                return None
            item, request, instrument, snapshot, decision = row
            item.status = "running"
            item.attempts += 1
            item.next_attempt_at = None
            content = snapshot.content_json if snapshot is not None else {}
            resolved = content.get("resolved") or {}
            benchmark = resolved.get("benchmark_ticker") or content.get("benchmark_ticker") or "SPY"
            return ClaimedValidation(
                id=item.id,
                run_id=item.run_id,
                ticker=instrument.canonical_ticker,
                benchmark_ticker=str(benchmark),
                analysis_date=request.analysis_date,
                horizon=item.horizon,
                rating=decision.rating,
                price_target=decision.price_target,
            )

    async def _complete(self, claim, instrument, benchmark, calculation, now) -> None:
        payload = {
            "instrument": instrument.model_dump(mode="json"),
            "benchmark": benchmark.model_dump(mode="json"),
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as source:
            json.dump(payload, source, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            source.flush()
            stored = self.artifact_store.put(
                claim.run_id,
                f"validation_{claim.horizon}_prices",
                "application/json",
                Path(source.name),
            )
        async with self.sessions() as session, session.begin():
            item = await session.get(Validation, claim.id, with_for_update=True)
            if item is None or item.status != "running":
                raise RuntimeError("claimed validation is no longer running")
            artifact = Artifact(
                run_id=claim.run_id,
                kind=stored.kind,
                media_type=stored.media_type,
                size=stored.size,
                sha256=stored.sha256,
                storage_key=stored.storage_key,
                redacted=True,
                retention_class="permanent",
                metadata_json={"validation_id": str(claim.id)},
            )
            session.add(artifact)
            await session.flush()
            item.status = "completed"
            item.observed_at = now
            item.raw_return = calculation.raw_return
            item.benchmark_return = calculation.benchmark_return
            item.alpha = calculation.alpha
            item.max_adverse_excursion = calculation.max_adverse_excursion
            item.max_favorable_excursion = calculation.max_favorable_excursion
            item.trigger_results_json = calculation.trigger_results
            item.data_artifact_id = artifact.id
            item.error_code = None
            await AssessmentRepository(session).append_event(
                claim.run_id,
                "validation.completed",
                {"validation_id": str(claim.id), "horizon": claim.horizon},
            )

    async def _retry(self, validation_id, now, *, next_day: bool, code: str) -> None:
        async with self.sessions() as session, session.begin():
            item = await session.get(Validation, validation_id, with_for_update=True)
            if item is None:
                return
            item.status = "retry_wait"
            if next_day:
                item.next_attempt_at = datetime.combine(
                    now.date() + timedelta(days=1), time.min, timezone.utc
                )
            else:
                delay = _RETRY_MINUTES[min(max(item.attempts - 1, 0), len(_RETRY_MINUTES) - 1)]
                item.next_attempt_at = now + timedelta(minutes=delay)
            item.error_code = code

    async def _terminal_error(self, validation_id, status: str, code: str) -> None:
        async with self.sessions() as session, session.begin():
            item = await session.get(Validation, validation_id, with_for_update=True)
            if item is not None:
                item.status = status
                item.error_code = code
