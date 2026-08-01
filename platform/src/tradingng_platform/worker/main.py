import asyncio
import contextlib
import logging
import os
import signal
import socket

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.config import Settings
from tradingng_platform.db import Database
from tradingng_platform.worker.repository import WorkerRepository
from tradingng_platform.worker.service import WorkerService

logger = logging.getLogger(__name__)


def build_worker_instance_name(hostname: str, pid: int, instance: str | None) -> str:
    return f"{hostname}:{instance or pid}"


async def run_worker() -> None:
    settings = Settings()
    settings.job_dir.mkdir(parents=True, exist_ok=True)
    database = Database(settings)
    worker_name = build_worker_instance_name(
        socket.gethostname(),
        os.getpid(),
        os.getenv("TRADINGNG_WORKER_INSTANCE"),
    )
    async with database.sessions() as session, session.begin():
        worker = await WorkerRepository(session).register_worker(
            worker_name,
            os.getpid(),
            {"runner": "tradingagents"},
        )
        worker_id = worker.id

    service = WorkerService(
        database.sessions,
        job_dir=settings.job_dir,
        gateway_url=str(settings.gateway_url),
        stocklean_url=str(settings.stocklean_url),
        artifact_store=LocalArtifactStore(settings.artifact_dir),
        alpha_vantage_broker_url=str(settings.alpha_vantage_broker_url),
        alpha_vantage_broker_request_timeout_seconds=(
            settings.alpha_vantage_broker_request_timeout_seconds
        ),
        alpha_vantage_auto_retry_attempts=settings.alpha_vantage_auto_retry_attempts,
        sec_user_agent=settings.sec_user_agent,
        sec_request_timeout_seconds=settings.sec_request_timeout_seconds,
        sec_cache_dir=settings.sec_cache_dir,
    )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopping.set)

    try:
        while not stopping.is_set():
            try:
                handled = await service.run_once(worker_id)
                if handled:
                    continue
                async with database.sessions() as session, session.begin():
                    repository = WorkerRepository(session)
                    await repository.recover_stale_leases()
                    await repository.heartbeat_idle(worker_id)
            except Exception as exc:
                logger.error("worker_pass_failed error_type=%s", type(exc).__name__)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=2.0)
    finally:
        await database.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
