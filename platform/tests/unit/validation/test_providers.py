import json
from datetime import date
from decimal import Decimal

import httpx
import pandas as pd

from tradingng_platform.config import Settings
from tradingng_platform.validation.price_contracts import OhlcBasis
from tradingng_platform.validation.providers import (
    AlphaVantagePriceProvider,
    LegacyPriceProviderAdapter,
    YFinancePriceProviderV2,
    build_price_provider,
)
from tradingng_platform.vendors.alpha_vantage import AlphaVantageRetryPolicy


class _FakeRateGate:
    def __init__(self):
        self.acquired = 0
        self.deferred = []

    def acquire(self):
        self.acquired += 1

    def defer(self, seconds):
        self.deferred.append(seconds)


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


async def test_alpha_vantage_retries_rate_limit_on_same_provider():
    payload = {
        "Meta Data": {"1. Information": "Daily", "5. Time Zone": "US/Eastern"},
        "Time Series (Daily)": {
            "2026-01-05": {
                "1. open": "99",
                "2. high": "101",
                "3. low": "98",
                "4. close": "100",
                "5. adjusted close": "100",
                "6. volume": "900",
                "7. dividend amount": "0",
                "8. split coefficient": "1",
            }
        },
    }
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"Note": "API call frequency exceeded"})
        return httpx.Response(200, json=payload)

    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    gate = _FakeRateGate()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AlphaVantagePriceProvider(
            "premium-secret-key",
            client=client,
            rate_gate=gate,
            retry_policy=AlphaVantageRetryPolicy(
                attempts=3,
                base_seconds=2,
                max_seconds=10,
            ),
            sleep=sleep,
        )
        series = await provider.history("IBM", date(2026, 1, 5), date(2026, 1, 5))

    assert series.provider_id == "alphavantage"
    assert calls == 2
    assert gate.acquired == 2
    assert gate.deferred == [2]
    assert sleeps == [2]


async def test_alpha_vantage_retries_http_429_with_retry_after():
    payload = {
        "Time Series (Daily)": {
            "2026-01-05": {
                "1. open": "99",
                "2. high": "101",
                "3. low": "98",
                "4. close": "100",
                "5. adjusted close": "100",
                "7. dividend amount": "0",
                "8. split coefficient": "1",
            }
        }
    }
    responses = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json=payload),
    ]
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: responses.pop(0))
    ) as client:
        provider = AlphaVantagePriceProvider(
            "premium-secret-key",
            client=client,
            rate_gate=_FakeRateGate(),
            retry_policy=AlphaVantageRetryPolicy(
                attempts=2,
                base_seconds=2,
                max_seconds=10,
            ),
            sleep=sleep,
        )
        await provider.history("IBM", date(2026, 1, 5), date(2026, 1, 5))

    assert sleeps == [7]


async def test_alpha_vantage_validation_uses_broker_without_direct_http():
    payload = {
        "Time Series (Daily)": {
            "2026-01-05": {
                "1. open": "99",
                "2. high": "101",
                "3. low": "98",
                "4. close": "100",
                "5. adjusted close": "100",
                "7. dividend amount": "0",
                "8. split coefficient": "1",
            }
        }
    }

    class FakeBroker:
        def __init__(self):
            self.calls = []

        async def query(self, function_name, params):
            self.calls.append((function_name, dict(params)))
            return json.dumps(payload)

    broker = FakeBroker()
    provider = AlphaVantagePriceProvider(api_key=None, broker_client=broker)

    series = await provider.history("IBM", date(2026, 1, 5), date(2026, 1, 5))

    assert series.close == [Decimal("100")]
    assert broker.calls == [
        (
            "TIME_SERIES_DAILY_ADJUSTED",
            {"symbol": "IBM", "outputsize": "full"},
        )
    ]


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

    series = await YFinancePriceProviderV2().history("NVDA", date(2026, 1, 5), date(2026, 1, 6))

    assert observed["auto_adjust"] is False
    assert observed["actions"] is True
    assert observed["threads"] is False
    assert series.ohlc_basis == OhlcBasis.SPLIT_NORMALIZED
    assert series.cash_distributions == [Decimal("0"), Decimal("0.75")]
    assert series.split_coefficient == [Decimal("10"), Decimal("1")]


def test_provider_router_uses_stocklean_without_alpha_key():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
        validation_price_providers=("stocklean",),
        alpha_vantage_api_key=None,
        stocklean_internal_token="internal-test-token",
    )

    router = build_price_provider(settings)

    assert router.provider_ids == ("stocklean",)


def test_provider_router_does_not_switch_to_alpha_when_key_is_present():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
        validation_price_providers=("stocklean",),
        alpha_vantage_api_key="premium-secret-key",
        stocklean_internal_token="internal-test-token",
    )

    router = build_price_provider(settings)

    assert router.provider_ids == ("stocklean",)


def test_provider_router_ignores_legacy_broker_for_stocklean():
    class FakeBroker:
        async def query(self, function_name, params):
            raise AssertionError("not called while building")

    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
        alpha_vantage_api_key="premium-secret-key",
        stocklean_internal_token="internal-test-token",
    )

    router = build_price_provider(settings, broker_client=FakeBroker())

    assert router.provider_ids == ("stocklean",)


async def test_legacy_adapter_uses_effective_alpha_provider():
    payload = {
        "Time Series (Daily)": {
            "2026-01-05": {
                "1. open": "99",
                "2. high": "101",
                "3. low": "98",
                "4. close": "100",
                "5. adjusted close": "100",
                "7. dividend amount": "0",
                "8. split coefficient": "1",
            }
        }
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        provider = AlphaVantagePriceProvider("premium-secret-key", client=client)
        legacy = LegacyPriceProviderAdapter(provider)
        series = await legacy.history("IBM", date(2026, 1, 5), date(2026, 1, 5))

    assert series.source == "alphavantage"
    assert series.close == [Decimal("100")]
