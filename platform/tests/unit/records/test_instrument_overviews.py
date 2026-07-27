import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from tradingng_platform.models import AssessmentRequest, AssessmentRun, Decision, Instrument
from tradingng_platform.records.contracts import InstrumentValidationView
from tradingng_platform.records.service import (
    _build_overview_items,
    _preferred_validation,
    _validation_stats,
)


def _validation(
    *,
    horizon: int,
    status: str,
    direction_correct: bool | None = None,
) -> InstrumentValidationView:
    return InstrumentValidationView(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        horizon=horizon,
        status=status,
        scheduled_for=datetime(2026, 7, 26, tzinfo=timezone.utc),
        matures_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
        exit_session=None,
        total_return=Decimal("-0.2065"),
        total_alpha=Decimal("-0.1459"),
        direction_correct=direction_correct,
        price_target_hit=None,
        error_code=None,
    )


def test_preferred_validation_uses_completed_20_then_5_then_1():
    one = _validation(horizon=1, status="completed")
    five = _validation(horizon=5, status="completed")
    twenty_scheduled = _validation(horizon=20, status="scheduled")

    assert _preferred_validation([one, five, twenty_scheduled]) == five


def test_preferred_validation_uses_longest_pending_when_none_completed():
    one = _validation(horizon=1, status="scheduled")
    twenty = _validation(horizon=20, status="scheduled")

    assert _preferred_validation([one, twenty]) == twenty
    assert _preferred_validation([]) is None


def test_validation_stats_exclude_non_completed_and_missing_direction():
    stats = _validation_stats(
        [
            _validation(horizon=20, status="completed", direction_correct=True),
            _validation(horizon=20, status="completed", direction_correct=False),
            _validation(horizon=20, status="failed", direction_correct=True),
            _validation(horizon=5, status="completed", direction_correct=None),
        ]
    )
    by_horizon = {item.horizon: item for item in stats}

    assert by_horizon[20].completed == 2
    assert by_horizon[20].direction_observed == 2
    assert by_horizon[20].direction_correct == 1
    assert by_horizon[20].accuracy == Decimal("0.5")
    assert by_horizon[5].completed == 1
    assert by_horizon[5].direction_observed == 0
    assert by_horizon[5].accuracy is None
    assert by_horizon[1].completed == 0


def test_overview_items_keep_latest_successful_decision_when_latest_run_failed():
    instrument_id = uuid.uuid4()
    instrument = Instrument(
        id=instrument_id,
        canonical_ticker="NVDA",
        asset_type="stock",
        exchange="NASDAQ",
        name="英伟达",
        metadata_json={},
    )
    successful_request = AssessmentRequest(
        id=uuid.uuid4(),
        batch_id=uuid.uuid4(),
        instrument_id=instrument_id,
        analysis_date=date(2026, 6, 1),
        requested_config_json={},
    )
    failed_request = AssessmentRequest(
        id=uuid.uuid4(),
        batch_id=uuid.uuid4(),
        instrument_id=instrument_id,
        analysis_date=date(2026, 7, 20),
        requested_config_json={},
    )
    created_at = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    successful = AssessmentRun(
        id=uuid.uuid4(),
        request_id=successful_request.id,
        attempt=1,
        status="succeeded",
        created_at=created_at - timedelta(days=1),
    )
    failed = AssessmentRun(
        id=uuid.uuid4(),
        request_id=failed_request.id,
        attempt=1,
        status="failed",
        created_at=created_at,
    )
    failed_retry = AssessmentRun(
        id=uuid.uuid4(),
        request_id=failed_request.id,
        attempt=2,
        status="failed",
        retry_of_run_id=failed.id,
        created_at=created_at + timedelta(hours=1),
    )
    decision = Decision(
        id=uuid.uuid4(),
        run_id=successful.id,
        rating="Underweight",
        executive_summary="Valuation risk.",
        investment_thesis="Expect underperformance.",
        price_target=Decimal("110"),
        time_horizon="20 trading days",
        structured_json={},
    )
    completed = _validation(horizon=20, status="completed", direction_correct=True)
    completed.run_id = successful.id

    items = _build_overview_items(
        [
            (failed_retry, failed_request, instrument, None, None),
            (failed, failed_request, instrument, None, None),
            (successful, successful_request, instrument, decision, None),
        ],
        {successful.id: [completed]},
    )

    assert len(items) == 1
    overview = items[0]
    assert overview.latest_run.id == failed_retry.id
    assert overview.latest_successful_run.id == successful.id
    assert overview.latest_decision.rating == "Underweight"
    assert overview.preferred_validation == completed
    assert overview.run_counts.total == 2
    assert overview.run_counts.succeeded == 1
    assert overview.run_counts.anomalous == 1
