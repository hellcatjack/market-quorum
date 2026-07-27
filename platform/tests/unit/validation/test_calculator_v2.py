from datetime import date, datetime, timezone
from decimal import Decimal

from tradingng_platform.validation.calculator_v2 import (
    TargetPriceBasis,
    calculate_outcome_v2,
)
from tradingng_platform.validation.calendars import ValidationSchedule
from tradingng_platform.validation.normalizer import normalize_prices
from tradingng_platform.validation.price_contracts import OhlcBasis, ProviderPriceSeries


def _canonical(
    ticker: str,
    closes: list[str],
    *,
    highs: list[str] | None = None,
    lows: list[str] | None = None,
    distributions: list[str] | None = None,
    sessions: list[date] | None = None,
):
    values = [Decimal(value) for value in closes]
    resolved_sessions = sessions or [date(2026, 1, 5), date(2026, 1, 6)]
    return normalize_prices(
        ProviderPriceSeries(
            ticker=ticker,
            provider_symbol=ticker,
            provider_id="fixture",
            provider_adapter_version="fixture.v1",
            request_fingerprint="c" * 64,
            ohlc_basis=OhlcBasis.SPLIT_NORMALIZED,
            capabilities=frozenset({"cash_dividends", "splits"}),
            currency="USD",
            timezone="America/New_York",
            sessions=resolved_sessions,
            open=values,
            high=[Decimal(value) for value in (highs or closes)],
            low=[Decimal(value) for value in (lows or closes)],
            close=values,
            adjusted_close=None,
            cash_distributions=[Decimal(value) for value in (distributions or ["0"] * len(values))],
            split_coefficient=[Decimal("1")] * len(values),
            collected_at=datetime(2026, 1, 7, tzinfo=timezone.utc),
        )
    )


def _schedule() -> ValidationSchedule:
    return ValidationSchedule(
        calendar_code="XNYS",
        entry_session=date(2026, 1, 5),
        exit_session=date(2026, 1, 6),
        matures_at=datetime(2026, 1, 6, 23, tzinfo=timezone.utc),
    )


def test_v2_separates_price_return_from_total_return():
    instrument = _canonical("TEST", ["100", "99"], distributions=["0", "1"])
    benchmark = _canonical("SPY", ["200", "200"])

    result = calculate_outcome_v2(
        instrument,
        benchmark,
        schedule=_schedule(),
        rating="Buy",
        price_target=None,
        target_basis=None,
    )

    assert result.price_return == Decimal("-0.0100000000")
    assert result.total_return == Decimal("0E-10")
    assert result.price_alpha == Decimal("-0.0100000000")
    assert result.total_alpha == Decimal("0E-10")
    assert result.trigger_results["direction_correct"] is False
    assert result.trigger_results["price_target_status"] == "not_set"


def test_target_rebases_to_split_scale_and_dividend_does_not_hit_it():
    sessions = [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]
    instrument = _canonical(
        "TEST",
        ["50", "50", "55"],
        highs=["51", "55", "59"],
        lows=["49", "49", "54"],
        distributions=["0", "1", "0"],
        sessions=sessions,
    )
    benchmark = _canonical("SPY", ["200", "200", "202"], sessions=sessions)
    basis = TargetPriceBasis(
        reference_session=date(2026, 1, 2),
        reference_close=Decimal("100"),
        target_multiple=Decimal("1.2"),
    )

    result = calculate_outcome_v2(
        instrument,
        benchmark,
        schedule=_schedule(),
        rating="Buy",
        price_target=Decimal("120"),
        target_basis=basis,
    )

    assert result.trigger_results["rebased_price_target"] == "60.0"
    assert result.trigger_results["price_target_hit"] is False
    assert result.trigger_results["price_target_status"] == "evaluated"
    assert result.max_favorable_excursion == Decimal("0.1800000000")


def test_target_without_frozen_basis_fails_closed_without_blocking_returns():
    result = calculate_outcome_v2(
        _canonical("TEST", ["100", "110"]),
        _canonical("SPY", ["200", "202"]),
        schedule=_schedule(),
        rating="Buy",
        price_target=Decimal("120"),
        target_basis=None,
    )

    assert result.total_return == Decimal("0.1000000000")
    assert result.trigger_results["price_target_hit"] is None
    assert result.trigger_results["price_target_status"] == "basis_unavailable"


def test_underweight_is_correct_when_instrument_rises_but_underperforms_benchmark():
    result = calculate_outcome_v2(
        _canonical("TEST", ["100", "105"]),
        _canonical("SPY", ["200", "220"]),
        schedule=_schedule(),
        rating="Underweight",
        price_target=None,
        target_basis=None,
    )

    assert result.total_return == Decimal("0.0500000000")
    assert result.total_alpha == Decimal("-0.0500000000")
    assert result.trigger_results["direction_correct"] is True
    assert result.trigger_results["direction_basis"] == "benchmark_total_alpha"
    assert result.trigger_results["direction_rule_version"] == "rating-direction.v2"


def test_underweight_is_incorrect_when_instrument_falls_but_outperforms_benchmark():
    result = calculate_outcome_v2(
        _canonical("TEST", ["100", "95"]),
        _canonical("SPY", ["200", "180"]),
        schedule=_schedule(),
        rating="Underweight",
        price_target=None,
        target_basis=None,
    )

    assert result.total_return == Decimal("-0.0500000000")
    assert result.total_alpha == Decimal("0.0500000000")
    assert result.trigger_results["direction_correct"] is False
    assert result.trigger_results["direction_basis"] == "benchmark_total_alpha"


def test_overweight_is_correct_when_instrument_falls_less_than_benchmark():
    result = calculate_outcome_v2(
        _canonical("TEST", ["100", "95"]),
        _canonical("SPY", ["200", "180"]),
        schedule=_schedule(),
        rating="Overweight",
        price_target=None,
        target_basis=None,
    )

    assert result.trigger_results["direction_correct"] is True
    assert result.trigger_results["direction_basis"] == "benchmark_total_alpha"


def test_sell_remains_an_absolute_return_call():
    result = calculate_outcome_v2(
        _canonical("TEST", ["100", "95"]),
        _canonical("SPY", ["200", "180"]),
        schedule=_schedule(),
        rating="Sell",
        price_target=None,
        target_basis=None,
    )

    assert result.trigger_results["direction_correct"] is True
    assert result.trigger_results["direction_basis"] == "instrument_total_return"
