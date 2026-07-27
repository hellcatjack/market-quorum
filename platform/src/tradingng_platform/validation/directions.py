from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

DIRECTION_RULE_VERSION = "rating-direction.v2"


@dataclass(frozen=True)
class DirectionEvaluation:
    direction: Literal["bullish", "bearish", "neutral"]
    direction_correct: bool
    direction_basis: Literal["instrument_total_return", "benchmark_total_alpha"]


def evaluate_rating_direction(
    rating: str,
    *,
    total_return: Decimal,
    total_alpha: Decimal,
) -> DirectionEvaluation:
    if rating == "Buy":
        return DirectionEvaluation("bullish", total_return > 0, "instrument_total_return")
    if rating == "Sell":
        return DirectionEvaluation("bearish", total_return < 0, "instrument_total_return")
    if rating == "Overweight":
        return DirectionEvaluation("bullish", total_alpha > 0, "benchmark_total_alpha")
    if rating == "Underweight":
        return DirectionEvaluation("bearish", total_alpha < 0, "benchmark_total_alpha")
    return DirectionEvaluation(
        "neutral",
        abs(total_return) <= Decimal("0.03"),
        "instrument_total_return",
    )
