import asyncio
import contextlib
import logging
import signal
import subprocess
from pathlib import Path

from tradingagents.default_config import DEFAULT_CONFIG

from tradingng_platform.config import Settings
from tradingng_platform.db import Database
from tradingng_platform.gateway.client import GatewayClient, GatewayStatusError
from tradingng_platform.instruments.names import run_instrument_name_enrichment
from tradingng_platform.scheduler.circuits import CircuitBreakerRepository
from tradingng_platform.scheduler.probes import SystemProbe
from tradingng_platform.scheduler.repository import (
    ExecutionMetadata,
    SchedulerPolicyRepository,
    SchedulerRepository,
)
from tradingng_platform.scheduler.service import AdmissionService

logger = logging.getLogger(__name__)


def _commit(path: Path) -> str:
    revision = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    )
    status = subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    suffix = "-dirty" if status.stdout.strip() else ""
    return revision.stdout.strip() + suffix


def _execution_metadata() -> ExecutionMetadata:
    project_root = Path(__file__).resolve().parents[4]
    return ExecutionMetadata(
        root_commit=_commit(project_root),
        tradingagents_commit=_commit(project_root / "TradingAgents"),
        prompt_schema_version="v1",
        data_vendors=dict(DEFAULT_CONFIG["data_vendors"]),
        tool_vendors=dict(DEFAULT_CONFIG["tool_vendors"]),
    )


async def run_scheduler() -> None:
    settings = Settings()
    database = Database(settings)
    gateway = GatewayClient(str(settings.gateway_url))
    system_probe = SystemProbe(settings.data_dir)
    metadata = _execution_metadata()
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopping.set)
    name_enrichment = asyncio.create_task(
        run_instrument_name_enrichment(database.sessions, stopping),
        name="instrument-name-enrichment",
    )

    try:
        while not stopping.is_set():
            try:
                async with database.sessions() as session, session.begin():
                    service = AdmissionService(
                        SchedulerRepository(session),
                        SchedulerPolicyRepository(session),
                        gateway,
                        system_probe,
                        metadata,
                    )
                    decision = await service.admit_one()
                if decision.allowed:
                    continue
                logger.debug("admission_paused reasons=%s", ",".join(decision.reasons))
            except GatewayStatusError as exc:
                async with database.sessions() as session, session.begin():
                    await CircuitBreakerRepository(session).record_gateway_sample(
                        healthy=False,
                        latency_ms=exc.latency_ms,
                        error_code=exc.error_code,
                        detail={"status_code": exc.status_code},
                    )
                logger.warning("gateway_status_failed error_code=%s", exc.error_code)
            except Exception as exc:
                logger.error("scheduler_pass_failed error_type=%s", type(exc).__name__)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=2.0)
    finally:
        stopping.set()
        await name_enrichment
        await database.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()
