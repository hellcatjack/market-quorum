import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.models import (
    Artifact,
    AssessmentRequest,
    AssessmentRun,
    Base,
    Decision,
    Instrument,
    RunEvent,
    Validation,
)
from tradingng_platform.validation.price_contracts import OhlcBasis, ProviderPriceSeries
from tradingng_platform.validation.worker import ValidationWorker


class _UnusedPrices:
    async def history(self, ticker, start, end):
        raise AssertionError("lease claim must not fetch prices")


class _V2Prices:
    async def history(self, ticker, start, end):
        del start, end
        benchmark = ticker == "SPY"
        closes = [200, 200] if benchmark else [100, 99]
        return ProviderPriceSeries(
            ticker=ticker,
            provider_symbol=ticker,
            provider_id="fixture-v2",
            provider_adapter_version="fixture.v2",
            request_fingerprint=("e" if benchmark else "f") * 64,
            ohlc_basis=OhlcBasis.SPLIT_NORMALIZED,
            capabilities=frozenset({"cash_dividends"}),
            currency="USD",
            timezone="America/New_York",
            sessions=[date(2026, 1, 5), date(2026, 1, 6)],
            open=closes,
            high=closes,
            low=closes,
            close=closes,
            adjusted_close=[200, 200] if benchmark else [100, 100],
            cash_distributions=[0, 0] if benchmark else [0, 1],
            split_coefficient=[1, 1],
            collected_at=datetime(2026, 1, 7, tzinfo=timezone.utc),
        )


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_expired_running_validation_is_recovered_and_reclaimed(tmp_path):
    engine, sessions = await _database()
    now = datetime(2026, 7, 26, 20, tzinfo=timezone.utc)
    run_id = uuid.uuid4()
    request_id = uuid.uuid4()
    instrument_id = uuid.uuid4()
    try:
        async with sessions() as session, session.begin():
            session.add(
                Instrument(
                    id=instrument_id,
                    canonical_ticker="NVDA",
                    asset_type="stock",
                    exchange="NMS",
                    metadata_json={},
                )
            )
            session.add(
                AssessmentRequest(
                    id=request_id,
                    batch_id=uuid.uuid4(),
                    instrument_id=instrument_id,
                    analysis_date=date(2026, 6, 1),
                    requested_config_json={},
                )
            )
            session.add(
                AssessmentRun(
                    id=run_id,
                    request_id=request_id,
                    attempt=1,
                    status="succeeded",
                    version=1,
                )
            )
            session.add(
                Decision(
                    run_id=run_id,
                    rating="Buy",
                    executive_summary="fixture",
                    investment_thesis="fixture",
                    structured_json={},
                )
            )
            session.add(
                Validation(
                    run_id=run_id,
                    horizon=1,
                    status="running",
                    scheduled_for=now - timedelta(days=1),
                    trigger_results_json={},
                    attempts=1,
                    calculation_version="validation.v2",
                    entry_session=date(2026, 6, 1),
                    exit_session=date(2026, 6, 2),
                    claimed_at=now - timedelta(minutes=10),
                    lease_expires_at=now - timedelta(seconds=1),
                    worker_instance="dead-worker",
                )
            )

        worker = ValidationWorker(
            sessions,
            _UnusedPrices(),
            LocalArtifactStore(tmp_path / "artifacts"),
            worker_instance="test-worker",
        )

        claim = await worker._claim(now)

        assert claim is not None
        async with sessions() as session:
            validation = await session.scalar(select(Validation))
            events = list(await session.scalars(select(RunEvent.event_type)))
        assert validation.status == "running"
        assert validation.worker_instance == "test-worker"
        assert validation.claimed_at == now
        assert validation.lease_expires_at == now + timedelta(minutes=5)
        assert events == ["validation.recovered"]
    finally:
        await engine.dispose()


async def test_v2_worker_persists_dual_returns_and_minimal_provenance_artifact(tmp_path):
    engine, sessions = await _database()
    now = datetime(2026, 1, 7, tzinfo=timezone.utc)
    run_id = uuid.uuid4()
    request_id = uuid.uuid4()
    instrument_id = uuid.uuid4()
    try:
        async with sessions() as session, session.begin():
            session.add(
                Instrument(
                    id=instrument_id,
                    canonical_ticker="TEST",
                    asset_type="stock",
                    exchange="NMS",
                    metadata_json={},
                )
            )
            session.add(
                AssessmentRequest(
                    id=request_id,
                    batch_id=uuid.uuid4(),
                    instrument_id=instrument_id,
                    analysis_date=date(2026, 1, 5),
                    requested_config_json={},
                )
            )
            session.add(
                AssessmentRun(
                    id=run_id,
                    request_id=request_id,
                    attempt=1,
                    status="succeeded",
                    version=1,
                )
            )
            session.add(
                Decision(
                    run_id=run_id,
                    rating="Buy",
                    executive_summary="fixture",
                    investment_thesis="fixture",
                    structured_json={},
                )
            )
            session.add(
                Validation(
                    run_id=run_id,
                    horizon=1,
                    status="scheduled",
                    scheduled_for=now - timedelta(minutes=1),
                    trigger_results_json={},
                    attempts=0,
                    calculation_version="validation.v2",
                    calendar_code="XNYS",
                    entry_session=date(2026, 1, 5),
                    exit_session=date(2026, 1, 6),
                    matures_at=now - timedelta(minutes=1),
                )
            )
        store = LocalArtifactStore(tmp_path / "artifacts")
        worker = ValidationWorker(
            sessions,
            _UnusedPrices(),
            store,
            v2_provider=_V2Prices(),
            worker_instance="test-worker",
        )

        assert await worker.run_once(now)

        async with sessions() as session:
            validation = await session.scalar(select(Validation))
            artifact = await session.get(Artifact, validation.data_artifact_id)
        assert validation.status == "completed"
        assert validation.price_return == Decimal("-0.0100000000")
        assert validation.total_return == Decimal("0E-10")
        assert validation.raw_return == validation.total_return
        assert validation.provider_id == "fixture-v2"
        assert validation.normalization_version == "prices.v1"
        payload = json.loads((store.root / artifact.storage_key).read_text(encoding="utf-8"))
        assert payload["schema_version"] == "validation-prices.v2"
        assert payload["instrument"]["sessions"] == ["2026-01-05", "2026-01-06"]
        assert payload["provenance"]["provider_id"] == "fixture-v2"
    finally:
        await engine.dispose()
