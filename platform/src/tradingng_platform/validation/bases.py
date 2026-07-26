from __future__ import annotations

from datetime import date
from decimal import Decimal

from tradingng_platform.validation.calculator_v2 import TargetPriceBasis
from tradingng_platform.validation.price_contracts import CanonicalPriceSeries


def prepare_target_basis(
    price_target: Decimal,
    analysis_date: date,
    prices: CanonicalPriceSeries,
) -> TargetPriceBasis:
    candidates = [
        (session, close)
        for session, close in zip(prices.sessions, prices.close, strict=True)
        if session <= analysis_date
    ]
    if not candidates:
        raise ValueError("target basis has no session on or before the analysis date")
    reference_session, reference_close = candidates[-1]
    if reference_close <= 0 or price_target <= 0:
        raise ValueError("target basis prices must be positive")
    return TargetPriceBasis(
        reference_session=reference_session,
        reference_close=reference_close,
        target_multiple=price_target / reference_close,
    )
