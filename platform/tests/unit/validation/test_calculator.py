from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from tradingng_platform.validation.calculator import InsufficientSessions, calculate_outcome
from tradingng_platform.validation.prices import PriceSeries


def _series(ticker, closes, highs=None, lows=None, sessions=None):
    values = [Decimal(str(value)) for value in closes]
    return PriceSeries(
        ticker=ticker,
        currency="USD",
        sessions=sessions or [date(2026, 7, 20 + index) for index in range(len(values))],
        open=values,
        high=[Decimal(str(value)) for value in (highs or closes)],
        low=[Decimal(str(value)) for value in (lows or closes)],
        close=values,
        adjusted_close=values,
        source="fixture",
        collected_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )


def test_exact_realized_return_benchmark_mae_and_mfe():
    instrument = _series(
        "NVDA",
        [100, 102, 99, 105, 110, 108],
        highs=[100, 103, 101, 106, 110, 109],
        lows=[100, 101, 99, 103, 108, 107],
    )
    benchmark = _series("SPY", [200, 202, 204, 206, 208, 210])

    outcome = calculate_outcome(
        instrument,
        benchmark,
        analysis_date=date(2026, 7, 20),
        horizon=5,
        rating="Buy",
        price_target=Decimal("109"),
    )

    assert outcome.raw_return == Decimal("0.0800000000")
    assert outcome.benchmark_return == Decimal("0.0500000000")
    assert outcome.alpha == Decimal("0.0300000000")
    assert outcome.max_adverse_excursion == Decimal("-0.0100000000")
    assert outcome.max_favorable_excursion == Decimal("0.1000000000")
    assert outcome.trigger_results["direction_correct"] is True
    assert outcome.trigger_results["price_target_hit"] is True


def test_weekend_gap_uses_trading_session_index_and_adjusts_split_high_low():
    sessions = [date(2026, 7, 24), date(2026, 7, 27)]
    instrument = PriceSeries(
        ticker="SPLT",
        currency="USD",
        sessions=sessions,
        open=[Decimal("100"), Decimal("52")],
        high=[Decimal("110"), Decimal("55")],
        low=[Decimal("90"), Decimal("50")],
        close=[Decimal("100"), Decimal("50")],
        adjusted_close=[Decimal("50"), Decimal("52")],
        source="fixture",
        collected_at=datetime.now(timezone.utc),
    )
    benchmark = _series("SPY", [200, 202], sessions=sessions)

    outcome = calculate_outcome(
        instrument,
        benchmark,
        analysis_date=date(2026, 7, 25),
        horizon=0,
        rating="Hold",
        price_target=None,
    )
    assert outcome.entry_session == date(2026, 7, 27)
    assert outcome.max_favorable_excursion == Decimal("0.1000000000")


def test_insufficient_and_invalid_prices_fail_closed():
    instrument = _series("NVDA", [100, 101])
    benchmark = _series("SPY", [200, 201])
    with pytest.raises(InsufficientSessions):
        calculate_outcome(
            instrument,
            benchmark,
            analysis_date=date(2026, 7, 20),
            horizon=5,
            rating="Hold",
            price_target=None,
        )
    with pytest.raises(ValueError, match="positive"):
        _series("BAD", [0, 1])
