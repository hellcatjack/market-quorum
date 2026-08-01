from __future__ import annotations

import asyncio
import logging
import os
import socket

from tradingng_platform.config import Settings
from tradingng_platform.data_readiness.repository import DataReadinessRepository
from tradingng_platform.data_readiness.service import DataReadinessService
from tradingng_platform.db import Database
from tradingng_platform.vendors.stocklean import StockLeanClient

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = Settings()
    token = settings.stocklean_internal_token.get_secret_value()
    if not token:
        raise RuntimeError("TRADINGNG_STOCKLEAN_INTERNAL_TOKEN is required")
    database = Database(settings)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    client = StockLeanClient(
        str(settings.stocklean_url),
        token=token,
        timeout=settings.stocklean_timeout_seconds,
    )
    service = DataReadinessService(
        DataReadinessRepository(database.sessions),
        client,
        worker_id=worker_id,
        poll_seconds=settings.stocklean_readiness_poll_seconds,
    )
    try:
        while True:
            processed = await service.reconcile_one()
            if not processed:
                await asyncio.sleep(settings.stocklean_readiness_poll_seconds)
    finally:
        await database.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
