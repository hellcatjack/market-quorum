import asyncio
import contextlib
import logging
import signal

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.config import Settings
from tradingng_platform.db import Database
from tradingng_platform.validation.prices import YFinancePriceProvider
from tradingng_platform.validation.worker import ValidationWorker

logger = logging.getLogger(__name__)


async def run_validation_worker() -> None:
    settings = Settings()
    database = Database(settings)
    worker = ValidationWorker(
        database.sessions,
        YFinancePriceProvider(),
        LocalArtifactStore(settings.artifact_dir),
        settings.max_running_validation,
    )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopping.set)
    try:
        while not stopping.is_set():
            try:
                if await worker.run_once():
                    continue
            except Exception as error:
                logger.error("validation_pass_failed error_type=%s", type(error).__name__)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=30)
    finally:
        await database.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_validation_worker())


if __name__ == "__main__":
    main()
