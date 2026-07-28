from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingng_platform.assessments.repository import AssessmentRepository
from tradingng_platform.auth.principal import Principal
from tradingng_platform.model_routing import (
    AVAILABLE_CODEX_MODELS,
    AVAILABLE_REASONING_EFFORTS,
    ModelRoutingPolicyRepository,
)
from tradingng_platform.models import (
    AssessmentRun,
    CircuitBreaker,
    Instrument,
    ModelRoutingPolicyRecord,
    SchedulerPolicyRecord,
    Worker,
)
from tradingng_platform.scheduler.circuits import CircuitBreakerRepository
from tradingng_platform.scheduler.policy import CapacitySnapshot
from tradingng_platform.scheduler.repository import (
    ACTIVE_RUN_STATUSES,
    SchedulerPolicyRepository,
)
from tradingng_platform.system.contracts import (
    CapacityView,
    ModelRoutingPolicyCommand,
    ModelRoutingPolicyView,
    SchedulerPolicyCommand,
    SchedulerPolicyView,
)

_WORKER_HEARTBEAT_FRESHNESS = timedelta(seconds=30)


class SystemService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        gateway,
        system_probe,
        *,
        alpha_broker_client=None,
        alpha_broker_queue_limit: int = 6,
    ):
        self.sessions = sessions
        self.gateway = gateway
        self.system_probe = system_probe
        self.alpha_broker_client = alpha_broker_client
        self.alpha_broker_queue_limit = alpha_broker_queue_limit

    async def status(self, principal: Principal) -> dict:
        principal.require("system:read")
        gateway = await self.gateway.status()
        alpha_broker = (
            await self.alpha_broker_client.status()
            if self.alpha_broker_client is not None
            else None
        )
        fresh_after = datetime.now(timezone.utc) - _WORKER_HEARTBEAT_FRESHNESS
        async with self.sessions() as session:
            workers = list(
                await session.scalars(
                    select(Worker)
                    .where(Worker.heartbeat_at >= fresh_after)
                    .order_by(Worker.instance_name)
                )
            )
            circuits = list(
                await session.scalars(select(CircuitBreaker).order_by(CircuitBreaker.name))
            )
            instruments = list(
                await session.scalars(select(Instrument).order_by(Instrument.id))
            )
        name_health = {
            "total": len(instruments),
            "official": 0,
            "pending": 0,
            "unresolved": 0,
            "conflicts": 0,
        }
        for instrument in instruments:
            metadata = (
                instrument.metadata_json
                if isinstance(instrument.metadata_json, dict)
                else {}
            )
            resolution = metadata.get("name_resolution")
            resolution = resolution if isinstance(resolution, dict) else {}
            reason = resolution.get("reason")
            if (
                resolution.get("provider") == "sec_edgar"
                and resolution.get("status") == "resolved"
            ):
                name_health["official"] += 1
            elif reason in {"ambiguous_cik", "exchange_mismatch"}:
                name_health["conflicts"] += 1
            elif resolution.get("status") == "unresolved":
                name_health["unresolved"] += 1
            else:
                name_health["pending"] += 1
        return {
            "gateway": {
                "status": gateway.status,
                "active_completions": gateway.active_completions,
                "model": gateway.model,
                "reasoning_effort": gateway.reasoning_effort,
                "snapshot_id": gateway.snapshot_id,
                "latency_ms": gateway.latency_ms,
            },
            "workers": [
                {
                    "instance_name": worker.instance_name,
                    "status": worker.status,
                    "heartbeat_at": worker.heartbeat_at.isoformat(),
                    "capabilities": dict(worker.capabilities_json),
                }
                for worker in workers
            ],
            "circuits": [
                {
                    "name": circuit.name,
                    "status": circuit.status,
                    "failure_count": circuit.failure_count,
                    "opened_until": (
                        circuit.opened_until.isoformat() if circuit.opened_until else None
                    ),
                    "last_error_code": circuit.last_error_code,
                }
                for circuit in circuits
            ],
            "alpha_vantage": (
                alpha_broker.model_dump(mode="json") if alpha_broker is not None else None
            ),
            "instrument_names": name_health,
        }

    async def capacity(self, principal: Principal) -> CapacityView:
        principal.require("system:read")
        gateway = await self.gateway.status()
        alpha_broker = (
            await self.alpha_broker_client.status()
            if self.alpha_broker_client is not None
            else None
        )
        system = self.system_probe.sample()
        now = datetime.now(timezone.utc)
        async with self.sessions() as session, session.begin():
            policy = await SchedulerPolicyRepository(session).get()
            model_routing = await ModelRoutingPolicyRepository(session).get()
            active = int(
                await session.scalar(
                    select(func.count())
                    .select_from(AssessmentRun)
                    .where(AssessmentRun.status.in_(ACTIVE_RUN_STATUSES))
                )
                or 0
            )
            queued = int(
                await session.scalar(
                    select(func.count())
                    .select_from(AssessmentRun)
                    .where(AssessmentRun.status == "queued")
                )
                or 0
            )
            oldest = await session.scalar(
                select(func.min(AssessmentRun.created_at)).where(AssessmentRun.status == "queued")
            )
            circuits = await CircuitBreakerRepository(session).blockers(now)
        if alpha_broker is not None and not alpha_broker.admission_allowed(
            queue_limit=self.alpha_broker_queue_limit
        ):
            circuits = tuple(sorted({*circuits, "vendor:alpha_vantage:global_quota"}))
        decision = policy.evaluate(CapacitySnapshot(active, gateway, system, circuits))
        return CapacityView(
            admitted_or_running=active,
            max_running_total=policy.max_running_total,
            hard_max_running_total=policy.hard_max_running_total,
            queued=queued,
            oldest_queued_seconds=(
                max(0, int((now - oldest).total_seconds())) if oldest is not None else None
            ),
            gateway_active_completions=gateway.active_completions,
            gateway_model=gateway.model,
            gateway_reasoning_effort=gateway.reasoning_effort,
            model_routing=model_routing,
            open_circuits=list(circuits),
            admission_allowed=decision.allowed,
            admission_reasons=list(decision.reasons),
        )

    async def get_scheduler_policy(self, principal: Principal) -> SchedulerPolicyView:
        principal.require("system:read")
        async with self.sessions() as session, session.begin():
            policy = await SchedulerPolicyRepository(session).get()
            record = await session.get(SchedulerPolicyRecord, "default")
            return SchedulerPolicyView(
                **policy.as_dict(),
                version=record.version,
                updated_at=record.updated_at,
            )

    async def update_scheduler_policy(
        self,
        principal: Principal,
        command: SchedulerPolicyCommand,
        request_id: str,
    ) -> SchedulerPolicyView:
        principal.require("assessments:admin")
        async with self.sessions() as session, session.begin():
            await AssessmentRepository(session).upsert_user(principal)
            policy = await SchedulerPolicyRepository(session).update(
                principal,
                command.to_policy(),
                request_id,
            )
            record = await session.get(SchedulerPolicyRecord, "default")
            return SchedulerPolicyView(
                **policy.as_dict(),
                version=record.version,
                updated_at=record.updated_at,
            )

    async def get_model_routing(self, principal: Principal) -> ModelRoutingPolicyView:
        principal.require("system:read")
        async with self.sessions() as session, session.begin():
            policy = await ModelRoutingPolicyRepository(session).get()
            record = await session.get(ModelRoutingPolicyRecord, "default")
            if record is None:
                raise RuntimeError("model routing policy is unavailable")
            return ModelRoutingPolicyView(
                **policy.model_dump(),
                available_models=list(AVAILABLE_CODEX_MODELS),
                available_reasoning_efforts=list(AVAILABLE_REASONING_EFFORTS),
                routing_snapshot_id=policy.snapshot_id,
                version=record.version,
                updated_at=record.updated_at,
            )

    async def update_model_routing(
        self,
        principal: Principal,
        command: ModelRoutingPolicyCommand,
        request_id: str,
    ) -> ModelRoutingPolicyView:
        principal.require("assessments:admin")
        async with self.sessions() as session, session.begin():
            await AssessmentRepository(session).upsert_user(principal)
            policy = await ModelRoutingPolicyRepository(session).update(
                principal,
                command.to_policy(),
                request_id,
            )
            record = await session.get(ModelRoutingPolicyRecord, "default")
            if record is None:
                raise RuntimeError("model routing policy is unavailable")
            return ModelRoutingPolicyView(
                **policy.model_dump(),
                available_models=list(AVAILABLE_CODEX_MODELS),
                available_reasoning_efforts=list(AVAILABLE_REASONING_EFFORTS),
                routing_snapshot_id=policy.snapshot_id,
                version=record.version,
                updated_at=record.updated_at,
            )
