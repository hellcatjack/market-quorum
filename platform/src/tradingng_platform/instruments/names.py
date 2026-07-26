from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingng_platform.domain.instruments import canonicalize_ticker
from tradingng_platform.models import Instrument

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://searchapi.eastmoney.com/api/suggest/get"
_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_SEARCH_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"  # gitleaks:allow
_US_MARKETS = (
    ("105", "NASDAQ"),
    ("106", "NYSE"),
    ("107", "AMEX"),
)
_RETRY_DELAY = timedelta(hours=6)
_MAX_PENDING_SCAN = 500


@dataclass(frozen=True)
class ResolvedInstrumentName:
    name: str
    exchange: str | None
    source: str
    source_identifier: str | None
    locale: str


@dataclass(frozen=True)
class PendingInstrument:
    id: uuid.UUID | str
    ticker: str
    asset_type: str


class InstrumentNameProvider(Protocol):
    async def resolve(self, ticker: str) -> ResolvedInstrumentName | None: ...


class InstrumentMetadataStore(Protocol):
    async def next_due(self, now: datetime) -> PendingInstrument | None: ...

    async def mark_resolved(
        self,
        instrument_id: uuid.UUID | str,
        result: ResolvedInstrumentName,
        resolved_at: datetime,
    ) -> None: ...

    async def mark_unresolved(
        self,
        instrument_id: uuid.UUID | str,
        attempted_at: datetime,
        retry_at: datetime,
        reason: str,
    ) -> None: ...


class EastMoneyInstrumentNameProvider:
    """Resolve localized security names without trusting fuzzy search matches."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def resolve(self, ticker: str) -> ResolvedInstrumentName | None:
        ticker = canonicalize_ticker(ticker)
        search_match = await self._search(ticker)
        if search_match is not None:
            return search_match

        results = await asyncio.gather(
            *(self._market_quote(ticker, market, exchange) for market, exchange in _US_MARKETS)
        )
        return next((result for result in results if result is not None), None)

    async def _search(self, ticker: str) -> ResolvedInstrumentName | None:
        try:
            response = await self.client.get(
                _SEARCH_URL,
                params={
                    "input": ticker,
                    "type": "14",
                    "token": _SEARCH_TOKEN,
                },
            )
            response.raise_for_status()
            payload = response.json()
            table = payload.get("QuotationCodeTable") or {}
            rows = table.get("Data") or []
            if not isinstance(rows, list):
                return None
            for row in rows:
                if not isinstance(row, dict) or not _row_matches_ticker(row, ticker):
                    continue
                name = _valid_name(row.get("Name"))
                if name is None:
                    continue
                return ResolvedInstrumentName(
                    name=name,
                    exchange=_optional_text(row.get("JYS")),
                    source="eastmoney",
                    source_identifier=_optional_text(row.get("QuoteID")),
                    locale="zh-CN",
                )
        except (httpx.HTTPError, TypeError, ValueError):
            return None
        return None

    async def _market_quote(
        self,
        ticker: str,
        market: str,
        exchange: str,
    ) -> ResolvedInstrumentName | None:
        source_identifier = f"{market}.{ticker}"
        try:
            response = await self.client.get(
                _QUOTE_URL,
                params={"secid": source_identifier, "fields": "f57,f58,f107"},
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data")
            if not isinstance(data, dict):
                return None
            returned_ticker = _optional_text(data.get("f57"))
            if returned_ticker is None or returned_ticker.upper() != ticker:
                return None
            name = _valid_name(data.get("f58"))
            if name is None:
                return None
            return ResolvedInstrumentName(
                name=name,
                exchange=exchange,
                source="eastmoney",
                source_identifier=source_identifier,
                locale="zh-CN",
            )
        except (httpx.HTTPError, TypeError, ValueError):
            return None


class SqlInstrumentMetadataStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    async def next_due(self, now: datetime) -> PendingInstrument | None:
        async with self.sessions() as session:
            instruments = list(
                await session.scalars(
                    select(Instrument)
                    .where(Instrument.name.is_(None))
                    .order_by(Instrument.created_at, Instrument.id)
                    .limit(_MAX_PENDING_SCAN)
                )
            )
        for instrument in instruments:
            metadata = _metadata(instrument.metadata_json)
            resolution = metadata.get("name_resolution")
            retry_at = _retry_at(resolution)
            if retry_at is None or retry_at <= now:
                return PendingInstrument(
                    id=instrument.id,
                    ticker=instrument.canonical_ticker,
                    asset_type=instrument.asset_type,
                )
        return None

    async def mark_resolved(
        self,
        instrument_id: uuid.UUID | str,
        result: ResolvedInstrumentName,
        resolved_at: datetime,
    ) -> None:
        async with self.sessions() as session, session.begin():
            instrument = await session.get(Instrument, instrument_id, with_for_update=True)
            if instrument is None or instrument.name is not None:
                return
            metadata = _metadata(instrument.metadata_json)
            metadata["name_resolution"] = {
                "status": "resolved",
                "provider": result.source,
                "source_identifier": result.source_identifier,
                "locale": result.locale,
                "resolved_at": resolved_at.isoformat(),
            }
            instrument.name = result.name
            instrument.exchange = result.exchange
            instrument.metadata_json = metadata

    async def mark_unresolved(
        self,
        instrument_id: uuid.UUID | str,
        attempted_at: datetime,
        retry_at: datetime,
        reason: str,
    ) -> None:
        async with self.sessions() as session, session.begin():
            instrument = await session.get(Instrument, instrument_id, with_for_update=True)
            if instrument is None or instrument.name is not None:
                return
            metadata = _metadata(instrument.metadata_json)
            metadata["name_resolution"] = {
                "status": "unresolved",
                "provider": "eastmoney",
                "attempted_at": attempted_at.isoformat(),
                "next_retry_at": retry_at.isoformat(),
                "reason": reason,
            }
            instrument.metadata_json = metadata


class InstrumentNameEnrichmentService:
    def __init__(
        self,
        store: InstrumentMetadataStore,
        provider: InstrumentNameProvider,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.provider = provider
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def run_once(self) -> bool:
        now = self.clock()
        pending = await self.store.next_due(now)
        if pending is None:
            return False
        try:
            result = await self.provider.resolve(pending.ticker)
        except Exception as error:
            await self.store.mark_unresolved(
                pending.id,
                now,
                now + _RETRY_DELAY,
                type(error).__name__,
            )
            return True
        if result is None:
            await self.store.mark_unresolved(
                pending.id,
                now,
                now + _RETRY_DELAY,
                "not_found",
            )
            return True
        await self.store.mark_resolved(pending.id, result, now)
        return True


async def run_instrument_name_enrichment(
    sessions: async_sessionmaker[AsyncSession],
    stopping: asyncio.Event,
) -> None:
    timeout = httpx.Timeout(3.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "TradingNG/0.1 instrument-metadata"},
    ) as client:
        service = InstrumentNameEnrichmentService(
            SqlInstrumentMetadataStore(sessions),
            EastMoneyInstrumentNameProvider(client),
        )
        while not stopping.is_set():
            delay = 30.0
            try:
                if await service.run_once():
                    delay = 1.0
            except Exception as error:
                logger.warning(
                    "instrument_name_enrichment_failed error_type=%s",
                    type(error).__name__,
                )
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=delay)


def _row_matches_ticker(row: dict, ticker: str) -> bool:
    candidates = (row.get("Code"), row.get("UnifiedCode"))
    return any(
        isinstance(candidate, str) and candidate.strip().upper() == ticker
        for candidate in candidates
    )


def _valid_name(value: object) -> str | None:
    text = _optional_text(value)
    if text is None or len(text) > 255:
        return None
    return text


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _metadata(value: object) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _retry_at(value: object) -> datetime | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("next_retry_at")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)
