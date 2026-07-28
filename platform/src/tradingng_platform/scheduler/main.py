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
from tradingng_platform.model_routing import ModelRoutingPolicyRepository
from tradingng_platform.scheduler.circuits import CircuitBreakerRepository
from tradingng_platform.scheduler.probes import SystemProbe
from tradingng_platform.scheduler.repository import (
    ExecutionMetadata,
    SchedulerPolicyRepository,
    SchedulerRepository,
)
from tradingng_platform.scheduler.service import AdmissionService
from tradingng_platform.vendors.alpha_vantage_client import AsyncAlphaVantageBrokerClient

logger = logging.getLogger(__name__)

_ALPHA_VANTAGE_RESEARCH_CATEGORIES = (
    "core_stock_apis",
    "technical_indicators",
    "fundamental_data",
    "news_data",
)


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


def _execution_metadata(settings: Settings) -> ExecutionMetadata:
    project_root = Path(__file__).resolve().parents[4]
    data_vendors = dict(DEFAULT_CONFIG["data_vendors"])
    research_vendor_chain = ",".join(settings.effective_research_data_vendor_chain)
    for category in _ALPHA_VANTAGE_RESEARCH_CATEGORIES:
        data_vendors[category] = research_vendor_chain
    return ExecutionMetadata(
        root_commit=_commit(project_root),
        tradingagents_commit=_commit(project_root / "TradingAgents"),
        prompt_schema_version="v1",
        data_vendors=data_vendors,
        tool_vendors=dict(DEFAULT_CONFIG["tool_vendors"]),
        vendor_policies={
            "alpha_vantage": {
                "requests_per_minute": settings.alpha_vantage_requests_per_minute,
                "retry_attempts": settings.alpha_vantage_retry_attempts,
                "retry_base_seconds": settings.alpha_vantage_retry_base_seconds,
                "retry_max_seconds": settings.alpha_vantage_retry_max_seconds,
            }
        },
    )


async def run_scheduler() -> None:
    settings = Settings()
    database = Database(settings)
    gateway = GatewayClient(str(settings.gateway_url))
    system_probe = SystemProbe(settings.data_dir)
    metadata = _execution_metadata(settings)
    alpha_broker = AsyncAlphaVantageBrokerClient(
        str(settings.alpha_vantage_broker_url),
        consumer="scheduler",
        timeout=5,
    )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopping.set)
    name_enrichment = asyncio.create_task(
        run_instrument_name_enrichment(
            database.sessions,
            stopping,
            user_agent=settings.sec_user_agent,
            cache_dir=settings.sec_cache_dir / "instrument-names",
        ),
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
                        model_routing_repository=ModelRoutingPolicyRepository(session),
                        alpha_broker_client=alpha_broker,
                        alpha_broker_queue_limit=(
                            settings.alpha_vantage_broker_admission_queue_limit
                        ),
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
