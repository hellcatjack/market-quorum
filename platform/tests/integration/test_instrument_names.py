from datetime import datetime, timezone

from sqlalchemy import select

from tradingng_platform.instruments.names import (
    InstrumentNameEnrichmentService,
    NameResolutionError,
    ResolvedInstrumentName,
    SqlInstrumentMetadataStore,
)
from tradingng_platform.models import AuditEvent, Instrument

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


class _Provider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def resolve(self, ticker, exchange):
        self.calls.append((ticker, exchange))
        if self.error:
            raise self.error
        return self.result


async def _seed(
    session_factory,
    *,
    ticker="PG",
    name=None,
    exchange="NYQ",
    resolution=None,
):
    async with session_factory() as session, session.begin():
        instrument = Instrument(
            canonical_ticker=ticker,
            asset_type="stock",
            name=name,
            exchange=exchange,
            metadata_json={"name_resolution": resolution} if resolution else {},
        )
        session.add(instrument)
        await session.flush()
        return instrument.id


async def _load(session_factory, instrument_id):
    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        audits = list(
            await session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.object_type == "instrument",
                    AuditEvent.object_id == str(instrument_id),
                )
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
        return instrument, audits


def _pg_result():
    return ResolvedInstrumentName(
        name="PROCTER & GAMBLE Co",
        exchange="NYSE",
        source="sec_edgar",
        source_identifier="CIK0000080424",
        source_url="https://data.sec.gov/submissions/CIK0000080424.json",
        locale="en-US",
    )


async def test_enrichment_persists_official_name_and_provenance(session_factory):
    instrument_id = await _seed(session_factory)
    provider = _Provider(result=_pg_result())
    service = InstrumentNameEnrichmentService(
        SqlInstrumentMetadataStore(session_factory),
        provider,
        clock=lambda: NOW,
    )

    assert await service.run_once() is True

    instrument, audits = await _load(session_factory, instrument_id)
    assert provider.calls == [("PG", "NYQ")]
    assert instrument.name == "PROCTER & GAMBLE Co"
    assert instrument.exchange == "NYSE"
    assert instrument.metadata_json["name_resolution"] == {
        "status": "resolved",
        "provider": "sec_edgar",
        "source_identifier": "CIK0000080424",
        "source_url": "https://data.sec.gov/submissions/CIK0000080424.json",
        "locale": "en-US",
        "verified_at": NOW.isoformat(),
        "next_refresh_at": "2026-08-04T12:00:00+00:00",
    }
    assert [audit.action for audit in audits] == ["instrument.name_resolved"]


async def test_enrichment_replaces_eastmoney_name_and_archives_source(session_factory):
    old_resolution = {
        "status": "resolved",
        "provider": "eastmoney",
        "source_identifier": "106.PG",
        "locale": "zh-CN",
        "resolved_at": "2026-07-26T00:00:00+00:00",
    }
    instrument_id = await _seed(
        session_factory,
        name="宝洁",
        resolution=old_resolution,
    )
    service = InstrumentNameEnrichmentService(
        SqlInstrumentMetadataStore(session_factory),
        _Provider(result=_pg_result()),
        clock=lambda: NOW,
    )

    assert await service.run_once() is True

    instrument, _ = await _load(session_factory, instrument_id)
    assert instrument.name == "PROCTER & GAMBLE Co"
    assert instrument.metadata_json["name_resolution_history"] == [
        {
            "name": "宝洁",
            **old_resolution,
        }
    ]


async def test_refresh_failure_preserves_last_verified_official_name(session_factory):
    current_resolution = {
        "status": "resolved",
        "provider": "sec_edgar",
        "source_identifier": "CIK0000080424",
        "source_url": "https://data.sec.gov/submissions/CIK0000080424.json",
        "locale": "en-US",
        "verified_at": "2026-07-20T12:00:00+00:00",
        "next_refresh_at": "2026-07-27T12:00:00+00:00",
    }
    instrument_id = await _seed(
        session_factory,
        name="PROCTER & GAMBLE Co",
        exchange="NYSE",
        resolution=current_resolution,
    )
    service = InstrumentNameEnrichmentService(
        SqlInstrumentMetadataStore(session_factory),
        _Provider(
            error=NameResolutionError("upstream_unavailable", transient=True)
        ),
        clock=lambda: NOW,
    )

    assert await service.run_once() is True

    instrument, audits = await _load(session_factory, instrument_id)
    assert instrument.name == "PROCTER & GAMBLE Co"
    assert instrument.metadata_json["name_resolution"] == current_resolution
    assert instrument.metadata_json["name_resolution_last_failure"] == {
        "attempted_at": NOW.isoformat(),
        "next_retry_at": "2026-07-28T12:15:00+00:00",
        "reason": "upstream_unavailable",
        "transient": True,
    }
    assert audits == []


async def test_permanent_failure_removes_unverified_legacy_name(session_factory):
    instrument_id = await _seed(
        session_factory,
        ticker="MISSING",
        name="供应商简称",
        exchange="NYQ",
        resolution={
            "status": "resolved",
            "provider": "eastmoney",
            "source_identifier": "106.MISSING",
            "resolved_at": "2026-07-26T00:00:00+00:00",
        },
    )
    service = InstrumentNameEnrichmentService(
        SqlInstrumentMetadataStore(session_factory),
        _Provider(error=NameResolutionError("ticker_not_listed", transient=False)),
        clock=lambda: NOW,
    )

    assert await service.run_once() is True

    instrument, _ = await _load(session_factory, instrument_id)
    assert instrument.name is None
    assert instrument.metadata_json["name_resolution"]["status"] == "unresolved"
    assert instrument.metadata_json["name_resolution"]["provider"] == "sec_edgar"
    assert instrument.metadata_json["name_resolution"]["reason"] == "ticker_not_listed"
    assert instrument.metadata_json["name_resolution_history"][0]["name"] == "供应商简称"
