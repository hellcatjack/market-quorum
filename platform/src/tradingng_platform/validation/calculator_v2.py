from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from pydantic import BaseModel

from tradingng_platform.validation.calculator import InsufficientSessions
from tradingng_platform.validation.calendars import ValidationSchedule
from tradingng_platform.validation.directions import (
    DIRECTION_RULE_VERSION,
    evaluate_rating_direction,
)
from tradingng_platform.validation.price_contracts import CanonicalPriceSeries

_QUANTUM = Decimal("0.0000000001")
_QUALITY_RANK = {
    "not_available": 0,
    "matched": 1,
    "minor_difference": 2,
    "material_difference": 3,
}


class TargetPriceBasis(BaseModel):
    reference_session: date
    reference_close: Decimal
    target_multiple: Decimal


class ValidationCalculationV2(BaseModel):
    price_return: Decimal
    benchmark_price_return: Decimal
    price_alpha: Decimal
    total_return: Decimal
    benchmark_total_return: Decimal
    total_alpha: Decimal
    max_adverse_excursion: Decimal
    max_favorable_excursion: Decimal
    trigger_results: dict


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)


def _index(series: CanonicalPriceSeries, session) -> int:
    try:
        return series.sessions.index(session)
    except ValueError as exc:
        raise InsufficientSessions(f"{series.ticker} is missing session {session}") from exc


def _return(values: list[Decimal], entry_index: int, exit_index: int) -> Decimal:
    entry = values[entry_index]
    if entry <= 0:
        raise ValueError("entry index value must be positive")
    return values[exit_index] / entry - Decimal("1")


def _quality_status(
    instrument: CanonicalPriceSeries,
    benchmark: CanonicalPriceSeries,
) -> str:
    return max(
        (instrument.data_quality_status, benchmark.data_quality_status),
        key=lambda value: _QUALITY_RANK[value],
    )


def calculate_outcome_v2(
    instrument: CanonicalPriceSeries,
    benchmark: CanonicalPriceSeries,
    *,
    schedule: ValidationSchedule,
    rating: str,
    price_target: Decimal | None,
    target_basis: TargetPriceBasis | None,
) -> ValidationCalculationV2:
    entry_index = _index(instrument, schedule.entry_session)
    exit_index = _index(instrument, schedule.exit_session)
    benchmark_entry_index = _index(benchmark, schedule.entry_session)
    benchmark_exit_index = _index(benchmark, schedule.exit_session)
    if exit_index < entry_index or benchmark_exit_index < benchmark_entry_index:
        raise ValueError("validation exit session precedes entry session")

    price_return = _return(instrument.close, entry_index, exit_index)
    benchmark_price_return = _return(benchmark.close, benchmark_entry_index, benchmark_exit_index)
    total_return = _return(instrument.total_return_index, entry_index, exit_index)
    benchmark_total_return = _return(
        benchmark.total_return_index,
        benchmark_entry_index,
        benchmark_exit_index,
    )
    entry_close = instrument.close[entry_index]
    highs = instrument.high[entry_index : exit_index + 1]
    lows = instrument.low[entry_index : exit_index + 1]
    mae = min(value / entry_close - Decimal("1") for value in lows)
    mfe = max(value / entry_close - Decimal("1") for value in highs)

    total_alpha = total_return - benchmark_total_return
    bullish = rating in {"Buy", "Overweight"}
    bearish = rating in {"Sell", "Underweight"}
    direction = evaluate_rating_direction(
        rating,
        total_return=total_return,
        total_alpha=total_alpha,
    )
    price_target_hit = None
    rebased_price_target = None
    if price_target is None:
        price_target_status = "not_set"
    elif target_basis is None:
        price_target_status = "basis_unavailable"
    else:
        reference_index = _index(instrument, target_basis.reference_session)
        rebased_price_target = target_basis.target_multiple * instrument.close[reference_index]
        if bullish:
            price_target_hit = max(highs) >= rebased_price_target
        elif bearish:
            price_target_hit = min(lows) <= rebased_price_target
        price_target_status = "evaluated"

    quality_status = _quality_status(instrument, benchmark)
    return ValidationCalculationV2(
        price_return=_quantize(price_return),
        benchmark_price_return=_quantize(benchmark_price_return),
        price_alpha=_quantize(price_return - benchmark_price_return),
        total_return=_quantize(total_return),
        benchmark_total_return=_quantize(benchmark_total_return),
        total_alpha=_quantize(total_alpha),
        max_adverse_excursion=_quantize(mae),
        max_favorable_excursion=_quantize(mfe),
        trigger_results={
            "rating": rating,
            "direction": direction.direction,
            "direction_correct": direction.direction_correct,
            "direction_basis": direction.direction_basis,
            "direction_rule_version": DIRECTION_RULE_VERSION,
            "price_target_hit": price_target_hit,
            "price_target_status": price_target_status,
            "rebased_price_target": (
                str(rebased_price_target) if rebased_price_target is not None else None
            ),
            "data_quality_status": quality_status,
            "entry_price": str(entry_close),
            "exit_price": str(instrument.close[exit_index]),
            "entry_session": schedule.entry_session.isoformat(),
            "exit_session": schedule.exit_session.isoformat(),
        },
    )
