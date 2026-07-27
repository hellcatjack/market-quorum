import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tradingng_platform.auth.principal import Principal
from tradingng_platform.models import (
    AssessmentRequest,
    AssessmentRun,
    AuditEvent,
    Base,
    Decision,
    DecisionPriceBasis,
    Instrument,
    RunEvent,
    Validation,
)
from tradingng_platform.validation.repository import ValidationRepository


def _principal() -> Principal:
    return Principal(
        issuer="fixture",
        subject="validator",
        actor_type="user",
        scopes=frozenset({"validations:read", "validations:write"}),
    )


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_run(
    sessions,
    *,
    status="succeeded",
    price_target=None,
    rating="Buy",
):
    run_id = uuid.uuid4()
    request_id = uuid.uuid4()
    instrument_id = uuid.uuid4()
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
                analysis_date=date(2026, 7, 25),
                requested_config_json={},
            )
        )
        session.add(
            AssessmentRun(
                id=run_id,
                request_id=request_id,
                attempt=1,
                status=status,
                version=1,
            )
        )
        session.add(
            Decision(
                run_id=run_id,
                rating=rating,
                executive_summary="fixture",
                investment_thesis="fixture",
                price_target=price_target,
                structured_json={},
            )
        )
    return run_id


async def test_v2_schedule_creates_one_non_blocking_target_basis(monkeypatch):
    engine, sessions = await _database()
    try:

        def plain_insert(dialect, model, values, conflict_columns):
            del dialect, conflict_columns
            return insert(model).values(**values)

        monkeypatch.setattr(
            "tradingng_platform.validation.repository.insert_ignore",
            plain_insert,
        )
        run_id = await _seed_run(sessions, price_target=Decimal("200"))
        repository = ValidationRepository(sessions)

        first = await repository.schedule(run_id, (1, 5, 20), _principal(), "schedule-1")

        async with sessions() as session:
            bases = list(await session.scalars(select(DecisionPriceBasis)))
        assert {item.calculation_version for item in first} == {"validation.v2"}
        assert all(item.entry_session and item.exit_session and item.matures_at for item in first)
        assert len(bases) == 1
        assert bases[0].status == "pending"
        assert bases[0].target_price == Decimal("200.000000")
    finally:
        await engine.dispose()


async def test_retry_preserves_attempts_and_rejects_active_lease():
    engine, sessions = await _database()
    now = datetime(2026, 7, 26, 20, tzinfo=timezone.utc)
    try:
        run_id = await _seed_run(sessions)
        failed_id = uuid.uuid4()
        active_id = uuid.uuid4()
        async with sessions() as session, session.begin():
            session.add_all(
                [
                    Validation(
                        id=failed_id,
                        run_id=run_id,
                        horizon=1,
                        status="failed",
                        scheduled_for=now - timedelta(days=1),
                        trigger_results_json={},
                        attempts=3,
                        error_code="calculation_error",
                    ),
                    Validation(
                        id=active_id,
                        run_id=run_id,
                        horizon=5,
                        status="running",
                        scheduled_for=now - timedelta(days=1),
                        trigger_results_json={},
                        attempts=1,
                        claimed_at=now,
                        lease_expires_at=now + timedelta(minutes=5),
                        worker_instance="active-worker",
                    ),
                ]
            )
        repository = ValidationRepository(sessions)

        retried = await repository.retry(failed_id, _principal(), "retry-1", now)

        assert retried.status == "scheduled"
        async with sessions() as session:
            failed = await session.get(Validation, failed_id)
            events = list(await session.scalars(select(RunEvent.event_type)))
            audits = list(await session.scalars(select(AuditEvent.action)))
        assert failed.attempts == 3
        assert failed.error_code is None
        assert events == ["validation.retry_requested"]
        assert audits == ["validation.retry"]
        with pytest.raises(ValueError, match="eligible"):
            await repository.retry(active_id, _principal(), "retry-2", now)
    finally:
        await engine.dispose()


async def test_recompute_directions_updates_completed_v2_results_with_audit_trail():
    engine, sessions = await _database()
    try:
        run_id = await _seed_run(sessions, rating="Underweight")
        validation_id = uuid.uuid4()
        async with sessions() as session, session.begin():
            session.add(
                Validation(
                    id=validation_id,
                    run_id=run_id,
                    horizon=20,
                    status="completed",
                    scheduled_for=datetime(2026, 7, 26, tzinfo=timezone.utc),
                    observed_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
                    calculation_version="validation.v2",
                    total_return=Decimal("0.05"),
                    total_alpha=Decimal("-0.05"),
                    trigger_results_json={
                        "rating": "Underweight",
                        "direction": "bearish",
                        "direction_correct": False,
                    },
                    attempts=1,
                )
            )

        changed = await ValidationRepository(sessions).recompute_directions(
            (run_id,),
            _principal(),
            "recompute-1",
        )

        assert changed == 1
        async with sessions() as session:
            validation = await session.get(Validation, validation_id)
            events = list(await session.scalars(select(RunEvent)))
            audits = list(await session.scalars(select(AuditEvent)))
        assert validation.trigger_results_json["direction_correct"] is True
        assert validation.trigger_results_json["direction_basis"] == "benchmark_total_alpha"
        assert validation.trigger_results_json["direction_rule_version"] == "rating-direction.v2"
        assert events[-1].event_type == "validation.direction_recomputed"
        assert audits[-1].action == "validation.direction_recompute"
    finally:
        await engine.dispose()
