from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingng_platform.domain.instruments import canonicalize_ticker
from tradingng_platform.models import AuditEvent, Instrument

logger = logging.getLogger(__name__)

_SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SEC_SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
_TICKER_CACHE_TTL = timedelta(hours=24)
_SUBMISSION_CACHE_TTL = timedelta(days=7)
_TRANSIENT_RETRY_DELAY = timedelta(minutes=15)
_PERMANENT_RETRY_DELAY = timedelta(hours=24)
_MAX_PENDING_SCAN = 500
_SEC_MIN_REQUEST_INTERVAL_SECONDS = 0.125
_EXCHANGE_ALIASES = {
    "ASE": "AMEX",
    "AMEX": "AMEX",
    "NASDAQ": "NASDAQ",
    "NASD": "NASDAQ",
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",
    "NCM": "NASDAQ",
    "NYSE": "NYSE",
    "NYQ": "NYSE",
}


class NameResolutionError(RuntimeError):
    def __init__(self, reason: str, *, transient: bool):
        super().__init__(reason)
        self.reason = reason
        self.transient = transient


@dataclass(frozen=True)
class ResolvedInstrumentName:
    name: str
    exchange: str | None
    source: str
    source_identifier: str
    source_url: str
    locale: str


@dataclass(frozen=True)
class PendingInstrument:
    id: uuid.UUID | str
    ticker: str
    asset_type: str
    exchange: str | None
    current_name: str | None


class InstrumentNameProvider(Protocol):
    async def resolve(
        self,
        ticker: str,
        exchange: str | None,
    ) -> ResolvedInstrumentName: ...


class SecInstrumentNameProvider:
    """Resolve official instrument identities from SEC EDGAR."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        user_agent: str,
        cache_dir: Path,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ):
        if not user_agent.strip():
            raise ValueError("SEC User-Agent cannot be empty")
        self.client = client
        self.user_agent = user_agent.strip()
        self.cache_dir = cache_dir
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic or time.monotonic
        self.sleeper = sleeper or asyncio.sleep
        self._request_lock = asyncio.Lock()
        self._last_request_started: float | None = None

    async def resolve(
        self,
        ticker: str,
        exchange: str | None,
    ) -> ResolvedInstrumentName:
        normalized = canonicalize_ticker(ticker)
        index = await self._fetch_json(_SEC_TICKERS_URL, _TICKER_CACHE_TTL)
        candidates = _sec_candidates(index, normalized)
        if not candidates:
            raise NameResolutionError("ticker_not_listed", transient=False)
        matches: list[tuple[str, str, tuple[str, str | None]]] = []
        for cik in candidates:
            source_url = f"{_SEC_SUBMISSIONS_BASE}/CIK{cik}.json"
            payload = await self._fetch_json(source_url, _SUBMISSION_CACHE_TTL)
            identity = _submission_identity(payload, normalized, exchange)
            if identity is not None:
                matches.append((cik, source_url, identity))
        if not matches:
            raise NameResolutionError("exchange_mismatch", transient=False)
        if len(matches) != 1:
            raise NameResolutionError("ambiguous_cik", transient=False)
        cik, source_url, (name, resolved_exchange) = matches[0]
        return ResolvedInstrumentName(
            name=name,
            exchange=resolved_exchange,
            source="sec_edgar",
            source_identifier=f"CIK{cik}",
            source_url=source_url,
            locale="en-US",
        )

    async def _fetch_json(self, url: str, ttl: timedelta) -> dict:
        cache_path = self.cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.json"
        cached = _read_cache(cache_path)
        now = self.clock()
        if cached is not None and now - cached[0] <= ttl:
            return cached[1]
        try:
            response = await self._get(url)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            if cached is not None:
                return cached[1]
            raise NameResolutionError("upstream_unavailable", transient=True) from None
        if not isinstance(payload, dict):
            if cached is not None:
                return cached[1]
            raise NameResolutionError("invalid_payload", transient=True)
        _write_cache(cache_path, now, payload)
        return payload

    async def _get(self, url: str) -> httpx.Response:
        async with self._request_lock:
            now = self.monotonic()
            if self._last_request_started is not None:
                wait = (
                    _SEC_MIN_REQUEST_INTERVAL_SECONDS
                    - (now - self._last_request_started)
                )
                if wait > 0:
                    await self.sleeper(wait)
                    now = self.monotonic()
            self._last_request_started = now
            return await self.client.get(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept-Encoding": "gzip, deflate",
                },
            )


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
        *,
        transient: bool,
    ) -> None: ...


class SqlInstrumentMetadataStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    async def next_due(self, now: datetime) -> PendingInstrument | None:
        async with self.sessions() as session:
            instruments = list(
                await session.scalars(
                    select(Instrument)
                    .order_by(Instrument.created_at, Instrument.id)
                    .limit(_MAX_PENDING_SCAN)
                )
            )
        for instrument in instruments:
            metadata = _metadata(instrument.metadata_json)
            resolution = metadata.get("name_resolution")
            provider = resolution.get("provider") if isinstance(resolution, dict) else None
            if provider == "sec_edgar":
                failure = metadata.get("name_resolution_last_failure")
                retry_at = _timestamp(failure, "next_retry_at") or _timestamp(
                    resolution,
                    "next_retry_at",
                )
                if retry_at is not None and retry_at > now:
                    continue
                if resolution.get("status") == "resolved":
                    refresh_at = _timestamp(resolution, "next_refresh_at")
                    if refresh_at is not None and refresh_at > now:
                        continue
            return PendingInstrument(
                id=instrument.id,
                ticker=instrument.canonical_ticker,
                asset_type=instrument.asset_type,
                exchange=instrument.exchange,
                current_name=instrument.name,
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
            if instrument is None:
                return
            metadata = _metadata(instrument.metadata_json)
            previous_name = instrument.name
            previous_resolution = metadata.get("name_resolution")
            previous_provider = (
                previous_resolution.get("provider")
                if isinstance(previous_resolution, dict)
                else None
            )
            if isinstance(previous_resolution, dict) and (
                previous_name != result.name or previous_provider != result.source
            ):
                _archive_resolution(metadata, previous_name, previous_resolution)
            metadata["name_resolution"] = {
                "status": "resolved",
                "provider": result.source,
                "source_identifier": result.source_identifier,
                "source_url": result.source_url,
                "locale": result.locale,
                "verified_at": resolved_at.isoformat(),
                "next_refresh_at": (resolved_at + timedelta(days=7)).isoformat(),
            }
            metadata.pop("name_resolution_last_failure", None)
            instrument.name = result.name
            if result.exchange is not None:
                instrument.exchange = result.exchange
            instrument.metadata_json = metadata
            if previous_name != result.name or previous_provider != result.source:
                session.add(
                    AuditEvent(
                        actor_type="system",
                        actor_id="instrument-name-enrichment",
                        action="instrument.name_resolved",
                        object_type="instrument",
                        object_id=str(instrument.id),
                        request_id=uuid.uuid4().hex,
                        metadata_json={
                            "ticker": instrument.canonical_ticker,
                            "provider": result.source,
                            "source_identifier": result.source_identifier,
                            "previous_name": previous_name,
                            "name": result.name,
                        },
                    )
                )

    async def mark_unresolved(
        self,
        instrument_id: uuid.UUID | str,
        attempted_at: datetime,
        retry_at: datetime,
        reason: str,
        *,
        transient: bool,
    ) -> None:
        async with self.sessions() as session, session.begin():
            instrument = await session.get(Instrument, instrument_id, with_for_update=True)
            if instrument is None:
                return
            metadata = _metadata(instrument.metadata_json)
            previous_resolution = metadata.get("name_resolution")
            if (
                isinstance(previous_resolution, dict)
                and previous_resolution.get("provider") == "sec_edgar"
                and previous_resolution.get("status") == "resolved"
                and instrument.name is not None
            ):
                metadata["name_resolution_last_failure"] = {
                    "attempted_at": attempted_at.isoformat(),
                    "next_retry_at": retry_at.isoformat(),
                    "reason": reason,
                    "transient": transient,
                }
                instrument.metadata_json = metadata
                return
            if isinstance(previous_resolution, dict):
                _archive_resolution(metadata, instrument.name, previous_resolution)
            metadata["name_resolution"] = {
                "status": "unresolved",
                "provider": "sec_edgar",
                "attempted_at": attempted_at.isoformat(),
                "next_retry_at": retry_at.isoformat(),
                "reason": reason,
                "transient": transient,
            }
            metadata.pop("name_resolution_last_failure", None)
            instrument.name = None
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
            result = await self.provider.resolve(pending.ticker, pending.exchange)
        except NameResolutionError as error:
            delay = (
                _TRANSIENT_RETRY_DELAY if error.transient else _PERMANENT_RETRY_DELAY
            )
            await self.store.mark_unresolved(
                pending.id,
                now,
                now + delay,
                error.reason,
                transient=error.transient,
            )
            return True
        except Exception as error:
            logger.warning(
                "instrument_name_resolution_failed ticker=%s error_type=%s",
                pending.ticker,
                type(error).__name__,
            )
            await self.store.mark_unresolved(
                pending.id,
                now,
                now + _TRANSIENT_RETRY_DELAY,
                "internal_error",
                transient=True,
            )
            return True
        await self.store.mark_resolved(pending.id, result, now)
        return True


async def run_instrument_name_enrichment(
    sessions: async_sessionmaker[AsyncSession],
    stopping: asyncio.Event,
    *,
    user_agent: str,
    cache_dir: Path,
) -> None:
    timeout = httpx.Timeout(10.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        service = InstrumentNameEnrichmentService(
            SqlInstrumentMetadataStore(sessions),
            SecInstrumentNameProvider(
                client,
                user_agent=user_agent,
                cache_dir=cache_dir,
            ),
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


def _read_cache(path: Path) -> tuple[datetime, dict] | None:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(envelope["fetched_at"])
        payload = envelope["payload"]
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if fetched_at.tzinfo is None or not isinstance(payload, dict):
        return None
    return fetched_at.astimezone(timezone.utc), payload


def _write_cache(path: Path, fetched_at: datetime, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(
            {"fetched_at": fetched_at.isoformat(), "payload": payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _sec_candidates(payload: dict, ticker: str) -> tuple[str, ...]:
    matches = []
    for value in payload.values():
        if not isinstance(value, dict):
            continue
        if str(value.get("ticker") or "").strip().upper() != ticker:
            continue
        try:
            matches.append(f"{int(value.get('cik_str')):010d}")
        except (TypeError, ValueError):
            continue
    return tuple(dict.fromkeys(matches))


def _submission_identity(
    payload: dict,
    ticker: str,
    expected_exchange: str | None,
) -> tuple[str, str | None] | None:
    name = _optional_text(payload.get("name"))
    tickers = payload.get("tickers")
    exchanges = payload.get("exchanges")
    if name is None or len(name) > 255:
        return None
    if not isinstance(tickers, list) or not isinstance(exchanges, list):
        return None
    expected = _normalize_exchange(expected_exchange)
    matches = []
    for index, candidate in enumerate(tickers):
        if not isinstance(candidate, str) or candidate.strip().upper() != ticker:
            continue
        raw_exchange = exchanges[index] if index < len(exchanges) else None
        normalized_exchange = _normalize_exchange(raw_exchange)
        if expected is not None and normalized_exchange != expected:
            continue
        matches.append(normalized_exchange)
    if not matches:
        return None
    unique_exchanges = tuple(dict.fromkeys(matches))
    if len(unique_exchanges) != 1:
        return None
    return name, unique_exchanges[0]


def _normalize_exchange(value: object) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    normalized = text.upper()
    return _EXCHANGE_ALIASES.get(normalized, normalized)


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _metadata(value: object) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _archive_resolution(metadata: dict, name: str | None, resolution: dict) -> None:
    archived = {"name": name, **resolution}
    history = [
        dict(item)
        for item in metadata.get("name_resolution_history", [])
        if isinstance(item, dict)
    ]
    if archived not in history:
        history.append(archived)
    metadata["name_resolution_history"] = history


def _timestamp(value: object, key: str) -> datetime | None:
    if not isinstance(value, dict):
        return None
    raw = value.get(key)
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)
