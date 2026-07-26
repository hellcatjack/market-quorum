from datetime import date
from decimal import Decimal

import httpx
import pandas as pd

from tradingng_platform.config import Settings
from tradingng_platform.validation.price_contracts import OhlcBasis
from tradingng_platform.validation.providers import (
    AlphaVantagePriceProvider,
    YFinancePriceProviderV2,
    build_price_provider,
)


async def test_alpha_vantage_maps_daily_adjusted_without_leaking_key():
    payload = {
        "Meta Data": {"1. Information": "Daily", "5. Time Zone": "US/Eastern"},
        "Time Series (Daily)": {
            "2026-01-06": {
                "1. open": "51",
                "2. high": "56",
                "3. low": "49",
                "4. close": "55",
                "5. adjusted close": "55",
                "6. volume": "1000",
                "7. dividend amount": "0",
                "8. split coefficient": "2",
            },
            "2026-01-05": {
                "1. open": "99",
                "2. high": "101",
                "3. low": "98",
                "4. close": "100",
                "5. adjusted close": "50",
                "6. volume": "900",
                "7. dividend amount": "1",
                "8. split coefficient": "1",
            },
        },
    }
    observed_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_url
        observed_url = str(request.url)
        return httpx.Response(200, json=payload)

    secret = "premium-secret-key"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        series = await AlphaVantagePriceProvider(secret, client=client).history(
            "IBM", date(2026, 1, 5), date(2026, 1, 6)
        )

    assert series.ohlc_basis == OhlcBasis.AS_TRADED
    assert series.sessions == [date(2026, 1, 5), date(2026, 1, 6)]
    assert series.cash_distributions == [Decimal("1"), Decimal("0")]
    assert series.split_coefficient == [Decimal("1"), Decimal("2")]
    assert secret in observed_url
    assert secret not in series.request_fingerprint
    assert secret not in repr(series)


async def test_yfinance_v2_maps_actions_and_declares_split_normalized(monkeypatch):
    columns = pd.MultiIndex.from_product(
        [
            [
                "Open",
                "High",
                "Low",
                "Close",
                "Adj Close",
                "Dividends",
                "Stock Splits",
                "Capital Gains",
            ],
            ["NVDA"],
        ]
    )
    frame = pd.DataFrame(
        [[50, 51, 49, 50, 50, 0, 10, 0], [54, 56, 53, 55, 55.5, 0.5, 0, 0.25]],
        index=pd.to_datetime(["2026-01-05", "2026-01-06"]),
        columns=columns,
    )
    observed = {}

    def download(*args, **kwargs):
        observed.update(kwargs)
        return frame

    monkeypatch.setattr("tradingng_platform.validation.providers.yf.download", download)

    series = await YFinancePriceProviderV2().history(
        "NVDA", date(2026, 1, 5), date(2026, 1, 6)
    )

    assert observed["auto_adjust"] is False
    assert observed["actions"] is True
    assert observed["threads"] is False
    assert series.ohlc_basis == OhlcBasis.SPLIT_NORMALIZED
    assert series.cash_distributions == [Decimal("0"), Decimal("0.75")]
    assert series.split_coefficient == [Decimal("10"), Decimal("1")]


def test_provider_router_skips_alpha_vantage_when_key_is_absent():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
        validation_price_providers=("alphavantage", "yfinance"),
        alpha_vantage_api_key=None,
    )

    router = build_price_provider(settings)

    assert router.provider_ids == ("yfinance",)


def test_provider_router_applies_configured_alpha_vantage_rate_limit():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
        validation_price_providers=("alphavantage", "yfinance"),
        alpha_vantage_api_key="premium-secret-key",
        alpha_vantage_requests_per_minute=17,
    )

    router = build_price_provider(settings)

    assert router.provider_ids == ("alphavantage", "yfinance")
    assert router.providers[0].requests_per_minute == 17
