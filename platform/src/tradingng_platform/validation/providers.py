from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx
import pandas as pd
import yfinance as yf

from tradingng_platform.config import Settings
from tradingng_platform.validation.price_contracts import OhlcBasis, ProviderPriceSeries

_ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"


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
    ):
        if not api_key:
            raise ValueError("Alpha Vantage API key is required")
        self._api_key = api_key
        self._client = client
        self._timeout = timeout

    async def history(self, ticker: str, start: date, end: date) -> ProviderPriceSeries:
        params = {
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": ticker.upper(),
            "outputsize": "full",
            "apikey": self._api_key,
        }
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(_ALPHA_VANTAGE_URL, params=params)
            else:
                response = await self._client.get(_ALPHA_VANTAGE_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderUnavailable("Alpha Vantage request failed") from exc
        if not isinstance(payload, dict):
            raise ProviderInvalidData("Alpha Vantage response is not an object")
        if "Note" in payload or (
            "Information" in payload and "rate" in str(payload["Information"]).lower()
        ):
            raise ProviderRateLimited("Alpha Vantage request limit reached")
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


def build_price_provider(settings: Settings) -> PriceProviderRouter:
    providers: list[ProviderProtocol] = []
    for provider_id in settings.validation_price_providers:
        if provider_id == "alphavantage":
            if settings.alpha_vantage_api_key is None:
                continue
            providers.append(
                AlphaVantagePriceProvider(
                    settings.alpha_vantage_api_key.get_secret_value(),
                    timeout=settings.validation_provider_timeout_seconds,
                )
            )
        elif provider_id == "yfinance":
            providers.append(YFinancePriceProviderV2())
    return PriceProviderRouter(tuple(providers))
