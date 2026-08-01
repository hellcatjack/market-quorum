from __future__ import annotations

from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import func, select

from tradingng_platform.models import AssessmentRun, Validation, Worker

REGISTRY = CollectorRegistry()
RUNS = Gauge("tradingng_runs", "Assessment runs by status", ["status"], registry=REGISTRY)
QUEUE_OLDEST = Gauge(
    "tradingng_queue_oldest_seconds",
    "Age of the oldest queued assessment",
    registry=REGISTRY,
)
WAITING_OLDEST = Gauge(
    "tradingng_data_waiting_oldest_seconds",
    "Age of the oldest assessment waiting for StockLean data",
    registry=REGISTRY,
)
STEP_DURATION = Histogram(
    "tradingng_step_duration_seconds",
    "Assessment step duration",
    ["step"],
    registry=REGISTRY,
)
GATEWAY_ACTIVE = Gauge(
    "tradingng_gateway_active_completions",
    "Active Codex Gateway completions",
    registry=REGISTRY,
)
GATEWAY_DURATION = Histogram(
    "tradingng_gateway_request_duration_seconds",
    "Codex Gateway request duration",
    registry=REGISTRY,
)
DEPENDENCY_ERRORS = Counter(
    "tradingng_dependency_errors_total",
    "Dependency errors",
    ["dependency", "code"],
    registry=REGISTRY,
)
WORKER_HEARTBEAT_AGE = Gauge(
    "tradingng_worker_heartbeat_age_seconds",
    "Worker heartbeat age",
    ["worker"],
    registry=REGISTRY,
)
WEBHOOK_DELIVERIES = Counter(
    "tradingng_webhook_deliveries_total",
    "Webhook delivery outcomes",
    ["status"],
    registry=REGISTRY,
)
VALIDATIONS = Gauge(
    "tradingng_validation_total",
    "Validation jobs by state and horizon",
    ["status", "horizon"],
    registry=REGISTRY,
)


async def refresh_database_metrics(sessions) -> None:
    now = datetime.now(timezone.utc)
    async with sessions() as session:
        run_rows = (
            await session.execute(
                select(AssessmentRun.status, func.count()).group_by(AssessmentRun.status)
            )
        ).all()
        oldest = await session.scalar(
            select(func.min(AssessmentRun.created_at)).where(AssessmentRun.status == "queued")
        )
        oldest_waiting = await session.scalar(
            select(func.min(AssessmentRun.created_at)).where(
                AssessmentRun.status == "waiting_for_data"
            )
        )
        workers = list(await session.scalars(select(Worker).order_by(Worker.instance_name)))
        validation_rows = (
            await session.execute(
                select(Validation.status, Validation.horizon, func.count()).group_by(
                    Validation.status, Validation.horizon
                )
            )
        ).all()
    RUNS.clear()
    for status, count in run_rows:
        RUNS.labels(status=status).set(count)
    QUEUE_OLDEST.set(max(0, (now - oldest).total_seconds()) if oldest else 0)
    WAITING_OLDEST.set(max(0, (now - oldest_waiting).total_seconds()) if oldest_waiting else 0)
    WORKER_HEARTBEAT_AGE.clear()
    for worker in workers:
        WORKER_HEARTBEAT_AGE.labels(worker=worker.instance_name).set(
            max(0, (now - worker.heartbeat_at).total_seconds())
        )
    VALIDATIONS.clear()
    for status, horizon, count in validation_rows:
        VALIDATIONS.labels(status=status, horizon=str(horizon)).set(count)


def render_metrics() -> bytes:
    return generate_latest(REGISTRY)
