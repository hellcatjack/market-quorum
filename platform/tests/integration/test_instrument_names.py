from datetime import datetime, timezone

from sqlalchemy import select

from tradingng_platform.instruments.names import (
    InstrumentNameEnrichmentService,
    ResolvedInstrumentName,
    SqlInstrumentMetadataStore,
)
from tradingng_platform.models import Instrument


class _Provider:
    async def resolve(self, ticker):
        assert ticker == "NVDA"
        return ResolvedInstrumentName(
            name="英伟达",
            exchange="NASDAQ",
            source="eastmoney",
            source_identifier="105.NVDA",
            locale="zh-CN",
        )


async def test_enrichment_persists_cached_name_and_provenance(session_factory):
    async with session_factory() as session, session.begin():
        session.add(
            Instrument(
                canonical_ticker="NVDA",
                asset_type="stock",
                metadata_json={},
            )
        )

    now = datetime(2026, 7, 25, 16, 0, tzinfo=timezone.utc)
    service = InstrumentNameEnrichmentService(
        SqlInstrumentMetadataStore(session_factory),
        _Provider(),
        clock=lambda: now,
    )

    assert await service.run_once() is True

    async with session_factory() as session:
        instrument = await session.scalar(select(Instrument))
    assert instrument.name == "英伟达"
    assert instrument.exchange == "NASDAQ"
    assert instrument.metadata_json["name_resolution"] == {
        "status": "resolved",
        "provider": "eastmoney",
        "source_identifier": "105.NVDA",
        "locale": "zh-CN",
        "resolved_at": now.isoformat(),
    }
