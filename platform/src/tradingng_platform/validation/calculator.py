from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from pydantic import BaseModel

from tradingng_platform.validation.prices import PriceSeries

_QUANTUM = Decimal("0.0000000001")


class InsufficientSessions(ValueError):
    pass


class ValidationCalculation(BaseModel):
    raw_return: Decimal
    benchmark_return: Decimal
    alpha: Decimal
    max_adverse_excursion: Decimal
    max_favorable_excursion: Decimal
    entry_session: date
    exit_session: date
    trigger_results: dict


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)


def calculate_outcome(
    instrument: PriceSeries,
    benchmark: PriceSeries,
    *,
    analysis_date: date,
    horizon: int,
    rating: str,
    price_target: Decimal | None,
) -> ValidationCalculation:
    if horizon < 0:
        raise ValueError("horizon cannot be negative")
    entry_index = next(
        (index for index, session in enumerate(instrument.sessions) if session >= analysis_date),
        None,
    )
    if entry_index is None or entry_index + horizon >= len(instrument.sessions):
        raise InsufficientSessions("instrument has fewer than the required trading sessions")
    exit_index = entry_index + horizon
    entry_session = instrument.sessions[entry_index]
    exit_session = instrument.sessions[exit_index]
    benchmark_closes = dict(zip(benchmark.sessions, benchmark.adjusted_close, strict=True))
    if entry_session not in benchmark_closes or exit_session not in benchmark_closes:
        raise InsufficientSessions("benchmark is missing aligned entry or exit sessions")

    entry_close = instrument.adjusted_close[entry_index]
    exit_close = instrument.adjusted_close[exit_index]
    benchmark_entry = benchmark_closes[entry_session]
    benchmark_exit = benchmark_closes[exit_session]
    if min(entry_close, exit_close, benchmark_entry, benchmark_exit) <= 0:
        raise ValueError("entry and exit prices must be positive")

    adjusted_highs = []
    adjusted_lows = []
    for index in range(entry_index, exit_index + 1):
        raw_close = instrument.close[index]
        if raw_close <= 0:
            raise ValueError("raw close prices must be positive")
        adjustment = instrument.adjusted_close[index] / raw_close
        adjusted_highs.append(instrument.high[index] * adjustment)
        adjusted_lows.append(instrument.low[index] * adjustment)

    raw_return = exit_close / entry_close - Decimal("1")
    benchmark_return = benchmark_exit / benchmark_entry - Decimal("1")
    alpha = raw_return - benchmark_return
    mae = min(value / entry_close - Decimal("1") for value in adjusted_lows)
    mfe = max(value / entry_close - Decimal("1") for value in adjusted_highs)
    bullish = rating in {"Buy", "Overweight"}
    bearish = rating in {"Sell", "Underweight"}
    direction_correct = (
        raw_return > 0
        if bullish
        else raw_return < 0
        if bearish
        else abs(raw_return) <= Decimal("0.03")
    )
    price_target_hit = None
    if price_target is not None:
        price_target_hit = (
            max(adjusted_highs) >= price_target if bullish else min(adjusted_lows) <= price_target
        )
    return ValidationCalculation(
        raw_return=_quantize(raw_return),
        benchmark_return=_quantize(benchmark_return),
        alpha=_quantize(alpha),
        max_adverse_excursion=_quantize(mae),
        max_favorable_excursion=_quantize(mfe),
        entry_session=entry_session,
        exit_session=exit_session,
        trigger_results={
            "rating": rating,
            "direction": "bullish" if bullish else "bearish" if bearish else "neutral",
            "direction_correct": direction_correct,
            "price_target_hit": price_target_hit,
            "entry_price": str(entry_close),
            "exit_price": str(exit_close),
            "entry_session": entry_session.isoformat(),
            "exit_session": exit_session.isoformat(),
        },
    )
