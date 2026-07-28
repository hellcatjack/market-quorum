from datetime import datetime, timezone

import httpx
import pytest

from tradingng_platform.instruments.names import (
    InstrumentNameEnrichmentService,
    NameResolutionError,
    PendingInstrument,
    ResolvedInstrumentName,
    SecInstrumentNameProvider,
)

SEC_TICKERS = {
    "0": {"cik_str": 80424, "ticker": "PG", "title": "PROCTER & GAMBLE Co"},
    "1": {"cik_str": 1045609, "ticker": "PLD", "title": "Prologis, Inc."},
    "2": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
}


def _sec_client(
    tickers: dict | None = None,
    submissions: dict[str, dict] | None = None,
    *,
    status_code: int = 200,
) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == "MarketQuorum test"
        if status_code != 200:
            return httpx.Response(status_code)
        if request.url.path == "/files/company_tickers.json":
            return httpx.Response(200, json=tickers if tickers is not None else SEC_TICKERS)
        cik = request.url.path.removeprefix("/submissions/CIK").removesuffix(".json")
        payload = (submissions or {}).get(cik)
        return httpx.Response(200, json=payload) if payload is not None else httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("ticker", "platform_exchange", "cik", "name", "sec_exchange"),
    (
        ("PG", "NYQ", "0000080424", "PROCTER & GAMBLE Co", "NYSE"),
        ("PLD", "NYSE", "0001045609", "Prologis, Inc.", "NYSE"),
        ("NVDA", "NMS", "0001045810", "NVIDIA CORP", "Nasdaq"),
    ),
)
async def test_sec_provider_returns_registered_name_and_cik(
    tmp_path,
    ticker,
    platform_exchange,
    cik,
    name,
    sec_exchange,
):
    async with _sec_client(
        submissions={
            cik: {"name": name, "tickers": [ticker], "exchanges": [sec_exchange]}
        }
    ) as client:
        provider = SecInstrumentNameProvider(
            client,
            user_agent="MarketQuorum test",
            cache_dir=tmp_path,
        )
        result = await provider.resolve(ticker.lower(), platform_exchange)

    assert result == ResolvedInstrumentName(
        name=name,
        exchange="NASDAQ" if sec_exchange == "Nasdaq" else sec_exchange,
        source="sec_edgar",
        source_identifier=f"CIK{cik}",
        source_url=f"https://data.sec.gov/submissions/CIK{cik}.json",
        locale="en-US",
    )


async def test_sec_provider_spaces_uncached_upstream_requests(tmp_path):
    elapsed = [0.0]
    sleeps: list[float] = []

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)
        elapsed[0] += delay

    async with _sec_client(
        submissions={
            "0000080424": {
                "name": "PROCTER & GAMBLE Co",
                "tickers": ["PG"],
                "exchanges": ["NYSE"],
            }
        }
    ) as client:
        provider = SecInstrumentNameProvider(
            client,
            user_agent="MarketQuorum test",
            cache_dir=tmp_path,
            monotonic=lambda: elapsed[0],
            sleeper=sleeper,
        )
        await provider.resolve("PG", "NYSE")

    assert sleeps == [pytest.approx(0.125)]


async def test_sec_provider_distinguishes_missing_and_exchange_conflict(tmp_path):
    async with _sec_client(
        submissions={
            "0000080424": {
                "name": "PROCTER & GAMBLE Co",
                "tickers": ["PG"],
                "exchanges": ["NYSE"],
            }
        }
    ) as client:
        provider = SecInstrumentNameProvider(
            client,
            user_agent="MarketQuorum test",
            cache_dir=tmp_path,
        )
        with pytest.raises(NameResolutionError) as missing:
            await provider.resolve("MISSING", "NYSE")
        with pytest.raises(NameResolutionError) as conflict:
            await provider.resolve("PG", "NASDAQ")

    assert (missing.value.reason, missing.value.transient) == ("ticker_not_listed", False)
    assert (conflict.value.reason, conflict.value.transient) == (
        "exchange_mismatch",
        False,
    )


async def test_sec_provider_classifies_vendor_outage_as_transient(tmp_path):
    async with _sec_client(status_code=503) as client:
        provider = SecInstrumentNameProvider(
            client,
            user_agent="MarketQuorum test",
            cache_dir=tmp_path,
        )
        with pytest.raises(NameResolutionError) as observed:
            await provider.resolve("PG", "NYSE")

    assert (observed.value.reason, observed.value.transient) == (
        "upstream_unavailable",
        True,
    )


class _Store:
    def __init__(self):
        self.pending = PendingInstrument(
            "instrument-1",
            "NVDA",
            "stock",
            "NMS",
            None,
        )
        self.resolved = []
        self.unresolved = []

    async def next_due(self, now):
        value, self.pending = self.pending, None
        return value

    async def mark_resolved(self, instrument_id, result, resolved_at):
        self.resolved.append((instrument_id, result, resolved_at))

    async def mark_unresolved(
        self,
        instrument_id,
        attempted_at,
        retry_at,
        reason,
        *,
        transient,
    ):
        self.unresolved.append(
            (instrument_id, attempted_at, retry_at, reason, transient)
        )


class _Provider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def resolve(self, ticker, exchange):
        assert (ticker, exchange) == ("NVDA", "NMS")
        if self.error:
            raise self.error
        return self.result


async def test_enrichment_service_persists_resolved_name_with_provenance():
    now = datetime(2026, 7, 25, 16, 0, tzinfo=timezone.utc)
    result = ResolvedInstrumentName(
        name="NVIDIA CORP",
        exchange="NASDAQ",
        source="sec_edgar",
        source_identifier="CIK0001045810",
        source_url="https://data.sec.gov/submissions/CIK0001045810.json",
        locale="en-US",
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
        _Provider(
            error=NameResolutionError(
                "upstream_unavailable",
                transient=True,
            )
        ),
        clock=lambda: now,
    )

    assert await service.run_once() is True
    instrument_id, attempted_at, retry_at, reason, transient = store.unresolved[0]
    assert instrument_id == "instrument-1"
    assert attempted_at == now
    assert retry_at > now
    assert reason == "upstream_unavailable"
    assert transient is True
