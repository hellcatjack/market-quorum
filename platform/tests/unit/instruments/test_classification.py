import httpx
import pytest

from tradingng_platform.domain.instruments import AssetType
from tradingng_platform.instruments.classification import (
    InstrumentClassificationNotFound,
    InstrumentClassificationUnavailable,
    InstrumentTypeUnsupported,
    StockLeanInstrumentClassifier,
    YahooInstrumentClassifier,
)
from tradingng_platform.vendors.stocklean import StockLeanInstrumentIdentity


def _quote(symbol: str, quote_type: str) -> dict:
    return {
        "symbol": symbol,
        "quoteType": quote_type,
        "exchange": "NMS",
        "longname": f"{symbol} name",
    }


async def test_classifier_maps_exact_yahoo_quote_types():
    quote_types = {
        "NVDA": "EQUITY",
        "GLD": "ETF",
        "VFIAX": "MUTUALFUND",
        "BTC-USD": "CRYPTOCURRENCY",
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        ticker = request.url.params["q"]
        return httpx.Response(200, json={"quotes": [_quote(ticker, quote_types[ticker])]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await YahooInstrumentClassifier(client=client).classify_many(tuple(quote_types))

    assert result["NVDA"].asset_type is AssetType.STOCK
    assert result["GLD"].asset_type is AssetType.FUND
    assert result["VFIAX"].asset_type is AssetType.FUND
    assert result["BTC-USD"].asset_type is AssetType.CRYPTO
    assert result["GLD"].quote_type == "ETF"
    assert result["GLD"].source == "yahoo_finance_search"


async def test_classifier_rejects_fuzzy_symbol_results():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"quotes": [_quote("NVDA.L", "EQUITY")]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(InstrumentClassificationNotFound) as captured:
            await YahooInstrumentClassifier(client=client).classify_many(("NVDA",))

    assert captured.value.ticker == "NVDA"


async def test_classifier_rejects_unsupported_quote_type():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"quotes": [_quote("GC=F", "FUTURE")]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(InstrumentTypeUnsupported) as captured:
            await YahooInstrumentClassifier(client=client).classify_many(("GC=F",))

    assert captured.value.ticker == "GC=F"
    assert captured.value.quote_type == "FUTURE"


async def test_classifier_reports_provider_failure_without_response_body():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="private upstream diagnostic")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(InstrumentClassificationUnavailable) as captured:
            await YahooInstrumentClassifier(client=client).classify_many(("NVDA",))

    assert captured.value.ticker == "NVDA"
    assert "private upstream diagnostic" not in str(captured.value)


async def test_stocklean_classifier_uses_persisted_identity_without_yahoo_network():
    class Client:
        async def instrument(self, ticker):
            return StockLeanInstrumentIdentity(
                asset_type="stock",
                exchange="NASDAQ",
                name="Example Corp",
                vendor_symbol=ticker,
            )

    result = await StockLeanInstrumentClassifier(Client()).classify_many(("XYZ",))

    assert result["XYZ"].asset_type is AssetType.STOCK
    assert result["XYZ"].source == "stocklean_alpha"
