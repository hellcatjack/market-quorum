from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from tradingng_platform.domain.instruments import AssetType, canonicalize_ticker
from tradingng_platform.vendors.stocklean import StockLeanClientError

_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
_QUOTE_TYPE_MAP = {
    "EQUITY": AssetType.STOCK,
    "ETF": AssetType.FUND,
    "MUTUALFUND": AssetType.FUND,
    "CRYPTOCURRENCY": AssetType.CRYPTO,
}


@dataclass(frozen=True)
class InstrumentClassification:
    ticker: str
    asset_type: AssetType
    quote_type: str
    source: str
    source_symbol: str
    exchange: str | None = None
    name: str | None = None


class InstrumentClassificationError(RuntimeError):
    def __init__(self, ticker: str, message: str):
        self.ticker = ticker
        super().__init__(message)


class InstrumentClassificationNotFound(InstrumentClassificationError):
    def __init__(self, ticker: str):
        super().__init__(ticker, f"instrument classification was not found for {ticker}")


class InstrumentClassificationUnavailable(InstrumentClassificationError):
    def __init__(self, ticker: str):
        super().__init__(
            ticker,
            f"instrument classification is temporarily unavailable for {ticker}",
        )


class InstrumentTypeUnsupported(InstrumentClassificationError):
    def __init__(self, ticker: str, quote_type: str):
        self.quote_type = quote_type
        super().__init__(ticker, f"instrument type {quote_type} is not supported for {ticker}")


class StockLeanInstrumentClassifier:
    def __init__(self, client):
        self.client = client

    async def classify_many(self, tickers: tuple[str, ...]) -> dict[str, InstrumentClassification]:
        unique = tuple(dict.fromkeys(canonicalize_ticker(ticker) for ticker in tickers))
        values = {}
        for ticker in unique:
            try:
                identity = await self.client.instrument(ticker)
            except StockLeanClientError as exc:
                if exc.status_code == 404:
                    raise InstrumentClassificationNotFound(ticker) from exc
                raise InstrumentClassificationUnavailable(ticker) from exc
            asset_type = {
                "stock": AssetType.STOCK,
                "fund": AssetType.FUND,
            }.get(identity.asset_type)
            if asset_type is None:
                raise InstrumentTypeUnsupported(ticker, identity.asset_type.upper())
            values[ticker] = InstrumentClassification(
                ticker=ticker,
                asset_type=asset_type,
                quote_type="EQUITY" if asset_type is AssetType.STOCK else "ETF",
                source="stocklean_alpha",
                source_symbol=identity.vendor_symbol,
                exchange=identity.exchange,
                name=identity.name,
            )
        return values


class YahooInstrumentClassifier:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        max_concurrency: int = 8,
    ):
        self.client = client
        self.max_concurrency = max_concurrency

    async def classify_many(
        self,
        tickers: tuple[str, ...],
    ) -> dict[str, InstrumentClassification]:
        unique = tuple(dict.fromkeys(canonicalize_ticker(ticker) for ticker in tickers))
        if self.client is not None:
            return await self._classify_with_client(self.client, unique)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(4.0),
            follow_redirects=True,
            headers={"User-Agent": "TradingNG/0.1 instrument-classification"},
        ) as client:
            return await self._classify_with_client(client, unique)

    async def _classify_with_client(
        self,
        client: httpx.AsyncClient,
        tickers: tuple[str, ...],
    ) -> dict[str, InstrumentClassification]:
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def classify(ticker: str) -> InstrumentClassification:
            async with semaphore:
                return await self._classify_one(client, ticker)

        values = await asyncio.gather(*(classify(ticker) for ticker in tickers))
        return dict(zip(tickers, values, strict=True))

    @staticmethod
    async def _classify_one(
        client: httpx.AsyncClient,
        ticker: str,
    ) -> InstrumentClassification:
        try:
            response = await client.get(
                _SEARCH_URL,
                params={
                    "q": ticker,
                    "quotesCount": "10",
                    "newsCount": "0",
                    "lang": "en-US",
                    "region": "US",
                },
            )
            response.raise_for_status()
            payload = response.json()
            quotes = payload.get("quotes")
            if not isinstance(quotes, list):
                raise TypeError("quotes must be a list")
        except (httpx.HTTPError, TypeError, ValueError) as error:
            raise InstrumentClassificationUnavailable(ticker) from error

        exact = next(
            (
                quote
                for quote in quotes
                if isinstance(quote, dict)
                and isinstance(quote.get("symbol"), str)
                and quote["symbol"].strip().upper() == ticker
            ),
            None,
        )
        if exact is None:
            raise InstrumentClassificationNotFound(ticker)

        quote_type_value = exact.get("quoteType")
        quote_type = quote_type_value.strip().upper() if isinstance(quote_type_value, str) else ""
        asset_type = _QUOTE_TYPE_MAP.get(quote_type)
        if asset_type is None:
            raise InstrumentTypeUnsupported(ticker, quote_type or "UNKNOWN")
        return InstrumentClassification(
            ticker=ticker,
            asset_type=asset_type,
            quote_type=quote_type,
            source="yahoo_finance_search",
            source_symbol=exact["symbol"].strip().upper(),
            exchange=_optional_text(exact.get("exchange")),
            name=_optional_text(exact.get("longname")) or _optional_text(exact.get("shortname")),
        )


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None
