from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx
import pandas as pd
import yfinance as yf

from tradingng_platform.config import Settings
from tradingng_platform.validation.price_contracts import OhlcBasis, ProviderPriceSeries
from tradingng_platform.validation.prices import PriceSeries
from tradingng_platform.vendors.alpha_vantage import (
    AlphaVantageRetryPolicy,
    CrossProcessRateGate,
    alpha_key_fingerprint,
    classify_alpha_payload,
)

_ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
logger = logging.getLogger(__name__)


class ProviderUnavailable(RuntimeError):
    pass


class ProviderRateLimited(ProviderUnavailable):
    pass


class ProviderUnsupported(RuntimeError):
    pass


class ProviderInvalidData(ValueError):
    pass


class ProviderProtocol(Protocol):
    provider_id: str

    async def history(self, ticker: str, start: date, end: date) -> ProviderPriceSeries: ...


class SlidingWindowRateLimiter:
    def __init__(self, requests_per_minute: int):
        if requests_per_minute < 1:
            raise ValueError("requests per minute must be positive")
        self.requests_per_minute = requests_per_minute
        self._observed: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                observed_now = time.monotonic()
                cutoff = observed_now - 60
                while self._observed and self._observed[0] <= cutoff:
                    self._observed.popleft()
                if len(self._observed) < self.requests_per_minute:
                    self._observed.append(observed_now)
                    return
                delay = max(self._observed[0] + 60 - observed_now, 0.001)
            await asyncio.sleep(delay)


def _fingerprint(provider: str, ticker: str, start: date, end: date, options: dict) -> str:
    payload = json.dumps(
        {
            "provider": provider,
            "ticker": ticker.upper(),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "options": options,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _column(frame: pd.DataFrame, name: str) -> pd.Series:
    values = frame[name]
    if isinstance(values, pd.DataFrame):
        if values.shape[1] != 1:
            raise ProviderInvalidData("price provider returned ambiguous ticker columns")
        return values.iloc[:, 0]
    return values


class YFinancePriceProviderV2:
    provider_id = "yfinance"
    adapter_version = "yfinance.v2"

    async def history(self, ticker: str, start: date, end: date) -> ProviderPriceSeries:
        try:
            frame = await asyncio.to_thread(
                yf.download,
                ticker,
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                auto_adjust=False,
                progress=False,
                actions=True,
                threads=False,
            )
        except (OSError, TimeoutError) as exc:
            raise ProviderUnavailable("yfinance request failed") from exc
        if frame.empty:
            raise ProviderInvalidData("yfinance returned no observations")
        try:
            prices = pd.concat(
                {
                    name: _column(frame, name)
                    for name in ("Open", "High", "Low", "Close", "Adj Close")
                },
                axis=1,
            ).dropna()
        except KeyError as exc:
            raise ProviderInvalidData("yfinance response is missing price columns") from exc
        if prices.empty:
            raise ProviderInvalidData("yfinance returned no complete observations")

        def optional_action(name: str) -> pd.Series:
            try:
                return _column(frame, name).reindex(prices.index).fillna(0)
            except KeyError:
                return pd.Series(0, index=prices.index, dtype=float)

        dividends = optional_action("Dividends")
        capital_gains = optional_action("Capital Gains")
        splits = optional_action("Stock Splits").replace(0, 1)
        return ProviderPriceSeries(
            ticker=ticker.upper(),
            provider_symbol=ticker.upper(),
            provider_id=self.provider_id,
            provider_adapter_version=self.adapter_version,
            request_fingerprint=_fingerprint(
                self.provider_id,
                ticker,
                start,
                end,
                {"auto_adjust": False, "actions": True, "threads": False},
            ),
            ohlc_basis=OhlcBasis.SPLIT_NORMALIZED,
            capabilities=frozenset({"splits", "cash_dividends", "capital_gains"}),
            currency=None,
            timezone=None,
            sessions=[timestamp.date() for timestamp in pd.to_datetime(prices.index)],
            open=[Decimal(str(value)) for value in prices["Open"]],
            high=[Decimal(str(value)) for value in prices["High"]],
            low=[Decimal(str(value)) for value in prices["Low"]],
            close=[Decimal(str(value)) for value in prices["Close"]],
            adjusted_close=[Decimal(str(value)) for value in prices["Adj Close"]],
            cash_distributions=[
                Decimal(str(dividend)) + Decimal(str(capital_gain))
                for dividend, capital_gain in zip(dividends, capital_gains, strict=True)
            ],
            split_coefficient=[Decimal(str(value)) for value in splits],
            collected_at=datetime.now(timezone.utc),
        )


class AlphaVantagePriceProvider:
    provider_id = "alphavantage"
    adapter_version = "alphavantage.v1"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
        requests_per_minute: int = 75,
        rate_gate: CrossProcessRateGate | None = None,
        retry_policy: AlphaVantageRetryPolicy | None = None,
        sleep=asyncio.sleep,
    ):
        if not api_key:
            raise ValueError("Alpha Vantage API key is required")
        self._api_key = api_key
        self._client = client
        self._timeout = timeout
        self.requests_per_minute = requests_per_minute
        self._rate_limiter = SlidingWindowRateLimiter(requests_per_minute)
        self._rate_gate = rate_gate
        self._retry_policy = retry_policy or AlphaVantageRetryPolicy()
        self._sleep = sleep

    async def history(self, ticker: str, start: date, end: date) -> ProviderPriceSeries:
        params = {
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": ticker.upper(),
            "outputsize": "full",
            "apikey": self._api_key,
        }
        payload = await self._request_payload(params)
        if not isinstance(payload, dict):
            raise ProviderInvalidData("Alpha Vantage response is not an object")
        if "Error Message" in payload:
            raise ProviderInvalidData("Alpha Vantage rejected the symbol")
        observations = payload.get("Time Series (Daily)")
        if not isinstance(observations, dict):
            raise ProviderInvalidData("Alpha Vantage response is missing daily prices")
        selected = []
        for raw_session, values in observations.items():
            try:
                session = date.fromisoformat(raw_session)
            except (TypeError, ValueError) as exc:
                raise ProviderInvalidData("Alpha Vantage returned an invalid session") from exc
            if start <= session <= end:
                if not isinstance(values, dict):
                    raise ProviderInvalidData("Alpha Vantage returned invalid daily values")
                selected.append((session, values))
        selected.sort(key=lambda item: item[0])
        if not selected:
            raise ProviderInvalidData("Alpha Vantage returned no observations in range")

        def values(field: str) -> list[Decimal]:
            try:
                return [Decimal(str(row[field])) for _, row in selected]
            except (KeyError, InvalidOperation) as exc:
                raise ProviderInvalidData(f"Alpha Vantage response is missing {field}") from exc

        metadata = payload.get("Meta Data")
        timezone_name = metadata.get("5. Time Zone") if isinstance(metadata, dict) else None
        return ProviderPriceSeries(
            ticker=ticker.upper(),
            provider_symbol=ticker.upper(),
            provider_id=self.provider_id,
            provider_adapter_version=self.adapter_version,
            request_fingerprint=_fingerprint(
                self.provider_id,
                ticker,
                start,
                end,
                {"function": "TIME_SERIES_DAILY_ADJUSTED", "outputsize": "full"},
            ),
            ohlc_basis=OhlcBasis.AS_TRADED,
            capabilities=frozenset({"splits", "cash_dividends"}),
            currency=None,
            timezone=str(timezone_name) if timezone_name else None,
            sessions=[session for session, _ in selected],
            open=values("1. open"),
            high=values("2. high"),
            low=values("3. low"),
            close=values("4. close"),
            adjusted_close=values("5. adjusted close"),
            cash_distributions=values("7. dividend amount"),
            split_coefficient=values("8. split coefficient"),
            collected_at=datetime.now(timezone.utc),
        )

    async def _request_payload(self, params: dict) -> dict:
        for attempt in range(1, self._retry_policy.attempts + 1):
            await self._acquire()
            retry_after = None
            try:
                if self._client is None:
                    async with httpx.AsyncClient(timeout=self._timeout) as client:
                        response = await client.get(_ALPHA_VANTAGE_URL, params=params)
                else:
                    response = await self._client.get(_ALPHA_VANTAGE_URL, params=params)
                if response.status_code == 429:
                    retry_after = _numeric_retry_after(response.headers.get("Retry-After"))
                    classification = "rate_limit"
                else:
                    response.raise_for_status()
                    payload = response.json()
                    classification = classify_alpha_payload(payload)
                    if classification is None:
                        if not isinstance(payload, dict):
                            raise ProviderInvalidData("Alpha Vantage response is not an object")
                        return payload
                    if classification == "authentication":
                        raise ProviderUnavailable(
                            "Alpha Vantage rejected the configured credentials"
                        )
                    if classification != "rate_limit":
                        raise ProviderInvalidData("Alpha Vantage returned an invalid response")
            except ProviderUnavailable:
                raise
            except ProviderInvalidData:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderUnavailable("Alpha Vantage request failed") from exc

            if attempt == self._retry_policy.attempts:
                raise ProviderRateLimited(
                    "Alpha Vantage request limit persisted after delayed retries"
                )
            delay = self._retry_policy.delay(attempt, retry_after=retry_after)
            if self._rate_gate is not None:
                await asyncio.to_thread(self._rate_gate.defer, delay)
            logger.warning(
                "alpha_vantage_validation_retry attempt=%d delay_seconds=%.1f",
                attempt,
                delay,
            )
            await self._sleep(delay)
        raise RuntimeError("unreachable Alpha Vantage retry state")

    async def _acquire(self) -> None:
        if self._rate_gate is not None:
            await asyncio.to_thread(self._rate_gate.acquire)
            return
        await self._rate_limiter.acquire()


class PriceProviderRouter:
    def __init__(self, providers: tuple[ProviderProtocol, ...]):
        if not providers:
            raise ValueError("at least one validation price provider is required")
        self.providers = providers

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(provider.provider_id for provider in self.providers)

    async def history(self, ticker: str, start: date, end: date) -> ProviderPriceSeries:
        last_error: Exception | None = None
        for provider in self.providers:
            try:
                return await provider.history(ticker, start, end)
            except (ProviderUnavailable, ProviderUnsupported) as exc:
                last_error = exc
        raise ProviderUnavailable(
            "all configured validation price providers failed"
        ) from last_error


class LegacyPriceProviderAdapter:
    def __init__(self, provider: ProviderProtocol):
        self.provider = provider

    async def history(self, ticker: str, start: date, end: date) -> PriceSeries:
        raw = await self.provider.history(ticker, start, end)
        return PriceSeries(
            ticker=raw.ticker,
            currency=raw.currency,
            sessions=raw.sessions,
            open=raw.open,
            high=raw.high,
            low=raw.low,
            close=raw.close,
            adjusted_close=raw.adjusted_close,
            source=raw.provider_id,
            collected_at=raw.collected_at,
        )


def build_price_provider(settings: Settings) -> PriceProviderRouter:
    providers: list[ProviderProtocol] = []
    for provider_id in settings.effective_validation_price_providers:
        if provider_id == "alphavantage":
            if settings.alpha_vantage_api_key is None:
                continue
            api_key = settings.alpha_vantage_api_key.get_secret_value()
            providers.append(
                AlphaVantagePriceProvider(
                    api_key,
                    timeout=settings.validation_provider_timeout_seconds,
                    requests_per_minute=settings.alpha_vantage_requests_per_minute,
                    rate_gate=CrossProcessRateGate(
                        settings.data_dir
                        / "vendor-limits"
                        / f"alpha-vantage-{alpha_key_fingerprint(api_key)}.json",
                        settings.alpha_vantage_requests_per_minute,
                    ),
                    retry_policy=AlphaVantageRetryPolicy(
                        attempts=settings.alpha_vantage_retry_attempts,
                        base_seconds=settings.alpha_vantage_retry_base_seconds,
                        max_seconds=settings.alpha_vantage_retry_max_seconds,
                    ),
                )
            )
        elif provider_id == "yfinance":
            providers.append(YFinancePriceProviderV2())
    return PriceProviderRouter(tuple(providers))


def _numeric_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None
