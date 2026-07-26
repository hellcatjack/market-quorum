from __future__ import annotations

from decimal import Decimal

from tradingng_platform.validation.price_contracts import (
    CanonicalPriceSeries,
    OhlcBasis,
    ProviderPriceSeries,
)

_ONE_HUNDRED = Decimal("100")


def _later_split_factors(series: ProviderPriceSeries) -> list[Decimal]:
    factors = [Decimal("1")] * len(series.sessions)
    cumulative = Decimal("1")
    for index in range(len(series.sessions) - 1, -1, -1):
        factors[index] = cumulative
        cumulative *= series.split_coefficient[index]
    return factors


def _quality_status(
    series: ProviderPriceSeries,
    canonical_total_return: Decimal,
) -> tuple[str, Decimal | None]:
    if series.adjusted_close is None:
        return "not_available", None
    provider_total_return = series.adjusted_close[-1] / series.adjusted_close[0] - Decimal("1")
    difference = abs(provider_total_return - canonical_total_return)
    if difference <= Decimal("0.000001"):
        return "matched", provider_total_return
    if difference <= Decimal("0.001"):
        return "minor_difference", provider_total_return
    return "material_difference", provider_total_return


def normalize_prices(series: ProviderPriceSeries) -> CanonicalPriceSeries:
    if series.ohlc_basis == OhlcBasis.UNKNOWN:
        raise ValueError("provider OHLC basis is unknown")
    if series.ohlc_basis == OhlcBasis.AS_TRADED:
        factors = _later_split_factors(series)
    else:
        factors = [Decimal("1")] * len(series.sessions)

    def adjusted(values: list[Decimal]) -> list[Decimal]:
        return [value / factor for value, factor in zip(values, factors, strict=True)]

    open_ = adjusted(series.open)
    high = adjusted(series.high)
    low = adjusted(series.low)
    close = adjusted(series.close)
    distributions = adjusted(series.cash_distributions)
    price_index = [_ONE_HUNDRED]
    total_return_index = [_ONE_HUNDRED]
    for index in range(1, len(close)):
        previous = close[index - 1]
        price_index.append(price_index[-1] * close[index] / previous)
        total_return_index.append(
            total_return_index[-1] * (close[index] + distributions[index]) / previous
        )
    canonical_total_return = total_return_index[-1] / _ONE_HUNDRED - Decimal("1")
    quality_status, provider_total_return = _quality_status(series, canonical_total_return)
    return CanonicalPriceSeries(
        ticker=series.ticker,
        provider_symbol=series.provider_symbol,
        provider_id=series.provider_id,
        provider_adapter_version=series.provider_adapter_version,
        request_fingerprint=series.request_fingerprint,
        currency=series.currency,
        timezone=series.timezone,
        sessions=series.sessions,
        open=open_,
        high=high,
        low=low,
        close=close,
        cash_distributions=distributions,
        price_index=price_index,
        total_return_index=total_return_index,
        data_quality_status=quality_status,
        provider_total_return=provider_total_return,
        collected_at=series.collected_at,
    )
