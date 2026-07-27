import uuid
from datetime import datetime, timezone
from decimal import Decimal

from tradingng_platform.records.contracts import InstrumentValidationView
from tradingng_platform.records.service import _preferred_validation, _validation_stats


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
