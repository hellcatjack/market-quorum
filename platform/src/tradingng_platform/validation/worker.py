from __future__ import annotations

import json
import os
import socket
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
    DecisionPriceBasis,
    Instrument,
    RunConfigSnapshot,
    Validation,
)
from tradingng_platform.validation.bases import prepare_target_basis
from tradingng_platform.validation.calculator import InsufficientSessions, calculate_outcome
from tradingng_platform.validation.calculator_v2 import (
    TargetPriceBasis,
    calculate_outcome_v2,
)
from tradingng_platform.validation.calendars import ValidationSchedule
from tradingng_platform.validation.normalizer import normalize_prices
from tradingng_platform.validation.prices import PriceProvider
from tradingng_platform.validation.providers import (
    ProviderInvalidData,
    ProviderProtocol,
    ProviderUnavailable,
)

_RETRY_MINUTES = (5, 10, 20, 30)
_LEASE_DURATION = timedelta(minutes=5)


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
    calculation_version: str
    calendar_code: str | None
    entry_session: date | None
    exit_session: date | None
    matures_at: datetime | None
    target_basis: TargetPriceBasis | None


@dataclass(frozen=True)
class ClaimedPriceBasis:
    id: uuid.UUID
    run_id: uuid.UUID
    ticker: str
    analysis_date: date
    target_price: Decimal


class ValidationWorker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: PriceProvider,
        artifact_store: LocalArtifactStore,
        max_running: int = 2,
        worker_instance: str | None = None,
        v2_provider: ProviderProtocol | None = None,
    ):
        self.sessions = sessions
        self.provider = provider
        self.artifact_store = artifact_store
        self.max_running = max_running
        self.worker_instance = worker_instance or f"{socket.gethostname()}:{os.getpid()}"
        self.v2_provider = v2_provider

    async def run_once(self, now: datetime | None = None) -> bool:
        observed_now = now or datetime.now(timezone.utc)
        basis_claim = await self._claim_basis(observed_now)
        if basis_claim is not None:
            await self._run_basis(basis_claim, observed_now)
            return True
        claim = await self._claim(observed_now)
        if claim is None:
            return False
        try:
            if claim.calculation_version == "validation.v2":
                await self._run_v2(claim, observed_now)
            else:
                await self._run_v1(claim, observed_now)
        except InsufficientSessions:
            await self._retry(claim.id, observed_now, next_day=True, code="future_sessions")
        except ProviderUnavailable:
            await self._retry(claim.id, observed_now, next_day=False, code="provider_unavailable")
        except ProviderInvalidData:
            await self._terminal_error(claim.id, "unavailable", "invalid_market_data")
        except (httpx.HTTPError, OSError, TimeoutError):
            await self._retry(claim.id, observed_now, next_day=False, code="provider_unavailable")
        except ValueError:
            await self._terminal_error(claim.id, "unavailable", "invalid_market_data")
        except Exception:
            await self._terminal_error(claim.id, "failed", "calculation_error")
        return True

    async def _run_basis(self, claim: ClaimedPriceBasis, now: datetime) -> None:
        try:
            if self.v2_provider is None:
                raise RuntimeError("validation.v2 price provider is not configured")
            raw = await self.v2_provider.history(
                claim.ticker,
                claim.analysis_date - timedelta(days=14),
                claim.analysis_date,
            )
            prices = normalize_prices(raw)
            prepared = prepare_target_basis(
                claim.target_price,
                claim.analysis_date,
                prices,
            )
            await self._complete_basis(claim.id, prices, prepared)
        except ProviderUnavailable:
            await self._retry_basis(claim.id, now, "provider_unavailable")
        except (httpx.HTTPError, OSError, TimeoutError):
            await self._retry_basis(claim.id, now, "provider_unavailable")
        except (ProviderInvalidData, ValueError):
            await self._terminal_basis(claim.id, "invalid_market_data")
        except Exception:
            await self._terminal_basis(claim.id, "basis_calculation_error")

    async def _claim_basis(self, now: datetime) -> ClaimedPriceBasis | None:
        async with self.sessions() as session, session.begin():
            expired = list(
                await session.scalars(
                    select(DecisionPriceBasis)
                    .where(
                        DecisionPriceBasis.status == "running",
                        or_(
                            DecisionPriceBasis.lease_expires_at.is_(None),
                            DecisionPriceBasis.lease_expires_at <= now,
                        ),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            for item in expired:
                item.status = "pending"
                item.claimed_at = None
                item.lease_expires_at = None
                item.worker_instance = None
                item.next_attempt_at = None
                item.error_code = "lease_expired"
                await AssessmentRepository(session).append_event(
                    item.run_id,
                    "validation.price_basis_recovered",
                    {"price_basis_id": str(item.id)},
                )
            row = (
                await session.execute(
                    select(
                        DecisionPriceBasis,
                        AssessmentRequest,
                        Instrument,
                    )
                    .join(
                        AssessmentRun,
                        DecisionPriceBasis.run_id == AssessmentRun.id,
                    )
                    .join(
                        AssessmentRequest,
                        AssessmentRun.request_id == AssessmentRequest.id,
                    )
                    .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
                    .where(
                        or_(
                            DecisionPriceBasis.status == "pending",
                            (DecisionPriceBasis.status == "retry_wait")
                            & (DecisionPriceBasis.next_attempt_at <= now),
                        )
                    )
                    .order_by(DecisionPriceBasis.created_at, DecisionPriceBasis.id)
                    .with_for_update(of=DecisionPriceBasis, skip_locked=True)
                    .limit(1)
                )
            ).one_or_none()
            if row is None:
                return None
            item, request, instrument = row
            item.status = "running"
            item.attempts += 1
            item.next_attempt_at = None
            item.claimed_at = now
            item.lease_expires_at = now + _LEASE_DURATION
            item.worker_instance = self.worker_instance
            return ClaimedPriceBasis(
                id=item.id,
                run_id=item.run_id,
                ticker=instrument.canonical_ticker,
                analysis_date=request.analysis_date,
                target_price=item.target_price,
            )

    async def _complete_basis(self, basis_id, prices, prepared) -> None:
        async with self.sessions() as session, session.begin():
            item = await session.get(DecisionPriceBasis, basis_id, with_for_update=True)
            if item is None or item.status != "running":
                raise RuntimeError("claimed target-price basis is no longer running")
            item.status = "completed"
            item.reference_session = prepared.reference_session
            item.reference_close = prepared.reference_close
            item.target_multiple = prepared.target_multiple
            item.currency = prices.currency
            item.provider_id = prices.provider_id
            item.provider_adapter_version = prices.provider_adapter_version
            item.normalization_version = prices.normalization_version
            item.collected_at = prices.collected_at
            item.next_attempt_at = None
            item.error_code = None
            item.claimed_at = None
            item.lease_expires_at = None
            item.worker_instance = None
            await AssessmentRepository(session).append_event(
                item.run_id,
                "validation.price_basis_completed",
                {
                    "price_basis_id": str(item.id),
                    "reference_session": prepared.reference_session.isoformat(),
                },
            )

    async def _retry_basis(self, basis_id, now: datetime, code: str) -> None:
        async with self.sessions() as session, session.begin():
            item = await session.get(DecisionPriceBasis, basis_id, with_for_update=True)
            if item is None:
                return
            delay = _RETRY_MINUTES[min(max(item.attempts - 1, 0), len(_RETRY_MINUTES) - 1)]
            item.status = "retry_wait"
            item.next_attempt_at = now + timedelta(minutes=delay)
            item.error_code = code
            item.claimed_at = None
            item.lease_expires_at = None
            item.worker_instance = None

    async def _terminal_basis(self, basis_id, code: str) -> None:
        async with self.sessions() as session, session.begin():
            item = await session.get(DecisionPriceBasis, basis_id, with_for_update=True)
            if item is not None:
                item.status = "unavailable"
                item.error_code = code
                item.claimed_at = None
                item.lease_expires_at = None
                item.worker_instance = None

    async def _run_v1(self, claim: ClaimedValidation, now: datetime) -> None:
        start = claim.analysis_date - timedelta(days=7)
        instrument = await self.provider.history(claim.ticker, start, now.date())
        benchmark = await self.provider.history(
            claim.benchmark_ticker,
            start,
            now.date(),
        )
        calculation = calculate_outcome(
            instrument,
            benchmark,
            analysis_date=claim.analysis_date,
            horizon=claim.horizon,
            rating=claim.rating,
            price_target=claim.price_target,
        )
        await self._complete(claim, instrument, benchmark, calculation, now)

    async def _run_v2(self, claim: ClaimedValidation, now: datetime) -> None:
        if self.v2_provider is None:
            raise RuntimeError("validation.v2 price provider is not configured")
        if (
            claim.calendar_code is None
            or claim.entry_session is None
            or claim.exit_session is None
            or claim.matures_at is None
        ):
            raise ValueError("validation.v2 schedule is incomplete")
        schedule = ValidationSchedule(
            calendar_code=claim.calendar_code,
            entry_session=claim.entry_session,
            exit_session=claim.exit_session,
            matures_at=claim.matures_at,
        )
        start = claim.analysis_date - timedelta(days=14)
        instrument_raw = await self.v2_provider.history(claim.ticker, start, schedule.exit_session)
        benchmark_raw = await self.v2_provider.history(
            claim.benchmark_ticker, start, schedule.exit_session
        )
        instrument = normalize_prices(instrument_raw)
        benchmark = normalize_prices(benchmark_raw)
        calculation = calculate_outcome_v2(
            instrument,
            benchmark,
            schedule=schedule,
            rating=claim.rating,
            price_target=claim.price_target,
            target_basis=claim.target_basis,
        )
        await self._complete_v2(
            claim,
            instrument_raw,
            benchmark_raw,
            instrument,
            benchmark,
            calculation,
            now,
        )

    async def _claim(self, now: datetime) -> ClaimedValidation | None:
        async with self.sessions() as session, session.begin():
            expired = list(
                await session.scalars(
                    select(Validation)
                    .where(
                        Validation.status == "running",
                        or_(
                            Validation.lease_expires_at.is_(None),
                            Validation.lease_expires_at <= now,
                        ),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            for item in expired:
                item.status = "scheduled"
                item.claimed_at = None
                item.lease_expires_at = None
                item.worker_instance = None
                item.next_attempt_at = None
                item.error_code = "lease_expired"
                await AssessmentRepository(session).append_event(
                    item.run_id,
                    "validation.recovered",
                    {"validation_id": str(item.id), "horizon": item.horizon},
                )
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
                        DecisionPriceBasis,
                    )
                    .join(AssessmentRun, Validation.run_id == AssessmentRun.id)
                    .join(AssessmentRequest, AssessmentRun.request_id == AssessmentRequest.id)
                    .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
                    .outerjoin(
                        RunConfigSnapshot,
                        AssessmentRun.config_snapshot_id == RunConfigSnapshot.id,
                    )
                    .join(Decision, Decision.run_id == AssessmentRun.id)
                    .outerjoin(
                        DecisionPriceBasis,
                        DecisionPriceBasis.run_id == AssessmentRun.id,
                    )
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
            item, request, instrument, snapshot, decision, basis = row
            item.status = "running"
            item.attempts += 1
            item.next_attempt_at = None
            item.claimed_at = now
            item.lease_expires_at = now + _LEASE_DURATION
            item.worker_instance = self.worker_instance
            content = snapshot.content_json if snapshot is not None else {}
            resolved = content.get("resolved") or {}
            benchmark = resolved.get("benchmark_ticker") or content.get("benchmark_ticker") or "SPY"
            target_basis = None
            if (
                basis is not None
                and basis.status == "completed"
                and basis.reference_session is not None
                and basis.reference_close is not None
                and basis.target_multiple is not None
            ):
                target_basis = TargetPriceBasis(
                    reference_session=basis.reference_session,
                    reference_close=basis.reference_close,
                    target_multiple=basis.target_multiple,
                )
            return ClaimedValidation(
                id=item.id,
                run_id=item.run_id,
                ticker=instrument.canonical_ticker,
                benchmark_ticker=str(benchmark),
                analysis_date=request.analysis_date,
                horizon=item.horizon,
                rating=decision.rating,
                price_target=decision.price_target,
                calculation_version=item.calculation_version,
                calendar_code=item.calendar_code,
                entry_session=item.entry_session,
                exit_session=item.exit_session,
                matures_at=item.matures_at,
                target_basis=target_basis,
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
            item.claimed_at = None
            item.lease_expires_at = None
            item.worker_instance = None
            await AssessmentRepository(session).append_event(
                claim.run_id,
                "validation.completed",
                {"validation_id": str(claim.id), "horizon": claim.horizon},
            )

    async def _complete_v2(
        self,
        claim,
        instrument_raw,
        benchmark_raw,
        instrument,
        benchmark,
        calculation,
        now,
    ) -> None:
        def series_payload(series):
            payload = series.model_dump(mode="json")
            # The compatibility alias keeps existing charts readable while the v2
            # fields expose both price-only and total-return indices explicitly.
            payload["adjusted_close"] = payload["close"]
            payload["source"] = series.provider_id
            return payload

        payload = {
            "schema_version": "validation-prices.v2",
            "instrument": series_payload(instrument),
            "benchmark": series_payload(benchmark),
            "provider_series": {
                "instrument": instrument_raw.model_dump(mode="json"),
                "benchmark": benchmark_raw.model_dump(mode="json"),
            },
            "provenance": {
                "provider_id": instrument.provider_id,
                "instrument_provider_id": instrument.provider_id,
                "benchmark_provider_id": benchmark.provider_id,
                "instrument_provider_symbol": instrument.provider_symbol,
                "benchmark_provider_symbol": benchmark.provider_symbol,
                "instrument_request_fingerprint": instrument.request_fingerprint,
                "benchmark_request_fingerprint": benchmark.request_fingerprint,
                "instrument_adapter_version": instrument.provider_adapter_version,
                "benchmark_adapter_version": benchmark.provider_adapter_version,
                "normalization_version": instrument.normalization_version,
                "instrument_data_quality_status": instrument.data_quality_status,
                "benchmark_data_quality_status": benchmark.data_quality_status,
                "collected_at": max(instrument.collected_at, benchmark.collected_at).isoformat(),
            },
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
                metadata_json={
                    "validation_id": str(claim.id),
                    "schema_version": "validation-prices.v2",
                    "provider_id": instrument.provider_id,
                    "provider_adapter_version": instrument.provider_adapter_version,
                    "normalization_version": instrument.normalization_version,
                },
            )
            session.add(artifact)
            await session.flush()
            item.status = "completed"
            item.observed_at = now
            item.price_return = calculation.price_return
            item.benchmark_price_return = calculation.benchmark_price_return
            item.price_alpha = calculation.price_alpha
            item.total_return = calculation.total_return
            item.benchmark_total_return = calculation.benchmark_total_return
            item.total_alpha = calculation.total_alpha
            # Legacy aliases remain total-return based for existing API clients.
            item.raw_return = calculation.total_return
            item.benchmark_return = calculation.benchmark_total_return
            item.alpha = calculation.total_alpha
            item.max_adverse_excursion = calculation.max_adverse_excursion
            item.max_favorable_excursion = calculation.max_favorable_excursion
            item.trigger_results_json = calculation.trigger_results
            item.data_artifact_id = artifact.id
            item.normalization_version = instrument.normalization_version
            item.provider_adapter_version = instrument.provider_adapter_version
            item.provider_id = instrument.provider_id
            item.error_code = None
            item.claimed_at = None
            item.lease_expires_at = None
            item.worker_instance = None
            await AssessmentRepository(session).append_event(
                claim.run_id,
                "validation.completed",
                {
                    "validation_id": str(claim.id),
                    "horizon": claim.horizon,
                    "calculation_version": claim.calculation_version,
                },
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
            item.claimed_at = None
            item.lease_expires_at = None
            item.worker_instance = None

    async def _terminal_error(self, validation_id, status: str, code: str) -> None:
        async with self.sessions() as session, session.begin():
            item = await session.get(Validation, validation_id, with_for_update=True)
            if item is not None:
                item.status = status
                item.error_code = code
                item.claimed_at = None
                item.lease_expires_at = None
                item.worker_instance = None
