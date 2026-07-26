from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import exchange_calendars as xcals
from sqlalchemy import select

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.assessments.contracts import AssessmentItem, SubmitAssessments
from tradingng_platform.assessments.service import AssessmentService
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.models import (
    Artifact,
    AssessmentRun,
    Decision,
    RunConfigSnapshot,
    RunEvent,
    Validation,
)
from tradingng_platform.validation.calendars import MarketCalendarResolver
from tradingng_platform.validation.price_contracts import OhlcBasis, ProviderPriceSeries
from tradingng_platform.validation.prices import PriceSeries
from tradingng_platform.validation.repository import ValidationRepository
from tradingng_platform.validation.service import ValidationService
from tradingng_platform.validation.worker import ValidationWorker


def _principal():
    return Principal(
        "issuer",
        "validation-analyst",
        "user",
        frozenset(
            {
                "assessments:submit",
                "assessments:read",
                "validations:read",
                "validations:write",
            }
        ),
        roles=frozenset({"Analyst"}),
    )


class _Prices:
    async def history(self, ticker, start, end):
        del start, end
        sessions = [date(2026, 7, 1) + timedelta(days=index) for index in range(21)]
        base = Decimal("200") if ticker == "SPY" else Decimal("100")
        closes = [base + index for index in range(21)]
        return PriceSeries(
            ticker=ticker,
            currency="USD",
            sessions=sessions,
            open=closes,
            high=[value + Decimal("1") for value in closes],
            low=[value - Decimal("1") for value in closes],
            close=closes,
            adjusted_close=closes,
            source="fixture",
            collected_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        )


class _PricesV2:
    provider_id = "fixture-v2"

    async def history(self, ticker, start, end):
        calendar = xcals.get_calendar("XNYS")
        sessions = [value.date() for value in calendar.sessions_in_range(start, end)]
        base = Decimal("200") if ticker == "SPY" else Decimal("100")
        closes = [base + index for index in range(len(sessions))]
        return ProviderPriceSeries(
            ticker=ticker,
            provider_symbol=ticker,
            provider_id=self.provider_id,
            provider_adapter_version="fixture.v2",
            request_fingerprint=("e" if ticker == "SPY" else "f") * 64,
            ohlc_basis=OhlcBasis.SPLIT_NORMALIZED,
            capabilities=frozenset({"cash_dividends", "splits"}),
            currency="USD",
            timezone="America/New_York",
            sessions=sessions,
            open=closes,
            high=[value + Decimal("1") for value in closes],
            low=[value - Decimal("1") for value in closes],
            close=closes,
            adjusted_close=closes,
            cash_distributions=[Decimal("0")] * len(sessions),
            split_coefficient=[Decimal("1")] * len(sessions),
            collected_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )


async def test_validation_worker_completes_three_horizons_without_rewriting_run(
    session_factory,
    instrument_classifier,
    tmp_path,
):
    principal = _principal()
    run = (
        await AssessmentService(session_factory, instrument_classifier).submit(
            principal,
            SubmitAssessments(
                items=[AssessmentItem(ticker="NVDA", analysis_date=date(2026, 7, 1))],
                idempotency_key="validation-integration-20260725",  # gitleaks:allow
            ),
            "validation-submit",
        )
    )[0]
    async with session_factory() as session, session.begin():
        snapshot = RunConfigSnapshot(
            content_json={"resolved": {"benchmark_ticker": "SPY"}},
            sha256="9" * 64,
            gateway_snapshot_id="validation-snapshot",
        )
        session.add(snapshot)
        await session.flush()
        persisted = await session.get(AssessmentRun, run.id)
        persisted.status = RunStatus.SUCCEEDED.value
        persisted.config_snapshot_id = snapshot.id
        persisted.finished_at = datetime(2026, 7, 2, tzinfo=timezone.utc)
        session.add(
            Decision(
                run_id=run.id,
                rating="Buy",
                executive_summary="Fixture",
                investment_thesis="Fixture",
                price_target=Decimal("110"),
                time_horizon="20 sessions",
                structured_json={},
            )
        )

    service = ValidationService(ValidationRepository(session_factory))
    first = await service.schedule(principal, run.id)
    second = await service.schedule(principal, run.id)
    assert [item.id for item in first] == [item.id for item in second]

    worker = ValidationWorker(
        session_factory,
        _Prices(),
        LocalArtifactStore(tmp_path / "artifacts"),
        v2_provider=_PricesV2(),
    )
    now = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    # The target-price basis is prepared independently before the three horizons.
    assert await worker.run_once(now)
    assert await worker.run_once(now)
    assert await worker.run_once(now)
    assert await worker.run_once(now)
    assert not await worker.run_once(now)

    async with session_factory() as session:
        validations = list(
            await session.scalars(
                select(Validation).where(Validation.run_id == run.id).order_by(Validation.horizon)
            )
        )
        persisted = await session.get(AssessmentRun, run.id)
        artifacts = list(
            await session.scalars(
                select(Artifact).where(Artifact.run_id == run.id).order_by(Artifact.kind)
            )
        )
        events = list(
            await session.scalars(select(RunEvent.event_type).where(RunEvent.run_id == run.id))
        )
    assert [item.status for item in validations] == ["completed", "completed", "completed"]
    assert validations[1].raw_return == validations[1].total_return
    assert validations[1].price_return == validations[1].total_return
    assert validations[1].provider_id == "fixture-v2"
    assert persisted.status == RunStatus.SUCCEEDED.value
    assert len(artifacts) == 3
    assert {artifact.retention_class for artifact in artifacts} == {"permanent"}
    assert events.count("validation.completed") == 3

    views = await service.list_for_run(principal, run.id)
    twenty_day = next(item for item in views if item.horizon == 20)
    assert twenty_day.data_artifact_id == validations[2].data_artifact_id
    assert twenty_day.trigger_results.rating == "Buy"
    assert twenty_day.trigger_results.direction == "bullish"
    assert twenty_day.trigger_results.direction_correct is True
    assert twenty_day.trigger_results.entry_session == date(2026, 7, 1)
    expected_exit = (
        MarketCalendarResolver().schedule("stock", "TEST", date(2026, 7, 1), 20).exit_session
    )
    assert twenty_day.trigger_results.exit_session == expected_exit
    assert twenty_day.error_code is None
    assert twenty_day.calculation_version == "validation.v2"
    assert twenty_day.normalization_version == "prices.v1"
