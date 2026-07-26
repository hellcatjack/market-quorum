from datetime import datetime, timezone

import httpx

from tradingng_platform.instruments.names import (
    EastMoneyInstrumentNameProvider,
    InstrumentNameEnrichmentService,
    PendingInstrument,
    ResolvedInstrumentName,
)


async def test_provider_accepts_only_an_exact_search_result():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/api/suggest/get")
        return httpx.Response(
            200,
            json={
                "QuotationCodeTable": {
                    "Data": [
                        {"Code": "NVDA.L", "Name": "错误结果", "JYS": "LSE"},
                        {
                            "Code": "NVDA",
                            "UnifiedCode": "NVDA",
                            "Name": "英伟达",
                            "JYS": "NASDAQ",
                            "QuoteID": "105.NVDA",
                        },
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await EastMoneyInstrumentNameProvider(client).resolve("nvda")

    assert result == ResolvedInstrumentName(
        name="英伟达",
        exchange="NASDAQ",
        source="eastmoney",
        source_identifier="105.NVDA",
        locale="zh-CN",
    )


async def test_provider_rejects_ambiguous_search_and_uses_exact_market_quote():
    requested_markets = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/suggest/get"):
            return httpx.Response(
                200,
                json={
                    "QuotationCodeTable": {
                        "Data": [
                            {
                                "Code": "603931",
                                "UnifiedCode": "603931",
                                "Name": "格林达",
                                "JYS": "SHANGHAI",
                            }
                        ]
                    }
                },
            )
        market = request.url.params["secid"].split(".", 1)[0]
        requested_markets.append(market)
        data = {"f57": "GLD", "f58": "黄金ETF-SPDR", "f107": 107} if market == "107" else None
        return httpx.Response(200, json={"data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await EastMoneyInstrumentNameProvider(client).resolve("GLD")

    assert result == ResolvedInstrumentName(
        name="黄金ETF-SPDR",
        exchange="AMEX",
        source="eastmoney",
        source_identifier="107.GLD",
        locale="zh-CN",
    )
    assert set(requested_markets) == {"105", "106", "107"}


async def test_provider_degrades_to_unresolved_when_vendor_is_unavailable():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await EastMoneyInstrumentNameProvider(client).resolve("NVDA")

    assert result is None


class _Store:
    def __init__(self):
        self.pending = PendingInstrument("instrument-1", "NVDA", "stock")
        self.resolved = []
        self.unresolved = []

    async def next_due(self, now):
        value, self.pending = self.pending, None
        return value

    async def mark_resolved(self, instrument_id, result, resolved_at):
        self.resolved.append((instrument_id, result, resolved_at))

    async def mark_unresolved(self, instrument_id, attempted_at, retry_at, reason):
        self.unresolved.append((instrument_id, attempted_at, retry_at, reason))


class _Provider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def resolve(self, ticker):
        if self.error:
            raise self.error
        return self.result


async def test_enrichment_service_persists_resolved_name_with_provenance():
    now = datetime(2026, 7, 25, 16, 0, tzinfo=timezone.utc)
    result = ResolvedInstrumentName(
        name="英伟达",
        exchange="NASDAQ",
        source="eastmoney",
        source_identifier="105.NVDA",
        locale="zh-CN",
    )
    store = _Store()
    service = InstrumentNameEnrichmentService(store, _Provider(result), clock=lambda: now)

    assert await service.run_once() is True
    assert store.resolved == [("instrument-1", result, now)]
    assert store.unresolved == []


async def test_enrichment_service_backs_off_after_provider_failure():
    now = datetime(2026, 7, 25, 16, 0, tzinfo=timezone.utc)
    store = _Store()
    service = InstrumentNameEnrichmentService(
        store,
        _Provider(error=httpx.ConnectError("offline")),
        clock=lambda: now,
    )

    assert await service.run_once() is True
    instrument_id, attempted_at, retry_at, reason = store.unresolved[0]
    assert instrument_id == "instrument-1"
    assert attempted_at == now
    assert retry_at > now
    assert reason == "ConnectError"
