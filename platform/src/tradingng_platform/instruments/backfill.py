from __future__ import annotations

import asyncio

import httpx

from tradingng_platform.config import Settings
from tradingng_platform.db import Database
from tradingng_platform.instruments.names import (
    InstrumentNameEnrichmentService,
    SecInstrumentNameProvider,
    SqlInstrumentMetadataStore,
)


async def backfill() -> int:
    settings = Settings()
    database = Database(settings)
    processed = 0
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            service = InstrumentNameEnrichmentService(
                SqlInstrumentMetadataStore(database.sessions),
                SecInstrumentNameProvider(
                    client,
                    user_agent=settings.sec_user_agent,
                    cache_dir=settings.sec_cache_dir / "instrument-names",
                ),
            )
            while await service.run_once():
                processed += 1
        return processed
    finally:
        await database.close()


def main() -> None:
    processed = asyncio.run(backfill())
    print(f"processed={processed}")


if __name__ == "__main__":
    main()
