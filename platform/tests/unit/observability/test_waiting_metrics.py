import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tradingng_platform.models import (
    AssessmentBatch,
    AssessmentRequest,
    AssessmentRun,
    Base,
    Instrument,
    User,
)
from tradingng_platform.observability.metrics import (
    WAITING_OLDEST,
    refresh_database_metrics,
    render_metrics,
)


async def test_waiting_count_and_oldest_age_are_exported():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session, session.begin():
            user = User(
                id=uuid.UUID(int=1),
                issuer="issuer",
                subject="owner",
                display_name="Owner",
            )
            instrument = Instrument(
                id=uuid.UUID(int=2),
                canonical_ticker="XYZ",
                asset_type="stock",
                exchange="NASDAQ",
                metadata_json={},
            )
            session.add_all([user, instrument])
            batch = AssessmentBatch(
                submitted_by=user.id,
                idempotency_key="waiting-metric",
                defaults_json={},
            )
            session.add(batch)
            await session.flush()
            request = AssessmentRequest(
                batch_id=batch.id,
                instrument_id=instrument.id,
                analysis_date=date(2026, 8, 1),
                requested_config_json={},
            )
            session.add(request)
            await session.flush()
            session.add(
                AssessmentRun(
                    request_id=request.id,
                    status="waiting_for_data",
                    version=1,
                    created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
                )
            )

        await refresh_database_metrics(sessions)

        rendered = render_metrics().decode()
        assert 'tradingng_runs{status="waiting_for_data"} 1.0' in rendered
        assert WAITING_OLDEST._value.get() >= 299
    finally:
        await engine.dispose()
