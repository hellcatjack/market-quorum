import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradingng_platform.assessments.contracts import Depth, MemoryMode
from tradingng_platform.assessments.repository import AssessmentRepository
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.runs import RunStatus, assert_transition
from tradingng_platform.gateway.client import GatewaySnapshot
from tradingng_platform.memory import HistoricalMemoryRepository, MemorySnapshot
from tradingng_platform.model_routing import ModelRoutingPolicy
from tradingng_platform.models import (
    AssessmentBatch,
    AssessmentRequest,
    AssessmentRun,
    AuditEvent,
    GatewayHealthSample,
    Instrument,
    RunConfigSnapshot,
    SchedulerPolicyRecord,
    User,
)
from tradingng_platform.persistence.locks import acquire_transaction_lock
from tradingng_platform.persistence.upsert import insert_ignore, session_dialect
from tradingng_platform.scheduler.circuits import CircuitBreakerRepository
from tradingng_platform.scheduler.policy import (
    DEPTH_ROUNDS,
    AdmissionDecision,
    AdmissionPolicy,
    CapacitySnapshot,
    SystemSnapshot,
)

ACTIVE_RUN_STATUSES = (
    RunStatus.ADMITTED.value,
    RunStatus.STARTING.value,
    RunStatus.RUNNING_ANALYSTS.value,
    RunStatus.RESEARCH_DEBATE.value,
    RunStatus.TRADER_PLAN.value,
    RunStatus.RISK_DEBATE.value,
    RunStatus.PORTFOLIO_DECISION.value,
    RunStatus.FINALIZING.value,
    RunStatus.CANCEL_REQUESTED.value,
    RunStatus.CANCELLING.value,
)


@dataclass(frozen=True)
class ExecutionMetadata:
    root_commit: str
    tradingagents_commit: str
    prompt_schema_version: str
    data_vendors: dict[str, str]
    tool_vendors: dict[str, str]
    vendor_policies: dict[str, dict] = field(default_factory=dict)


def _configured_vendors(metadata: ExecutionMetadata) -> set[str]:
    configured: set[str] = set()
    for vendor_chain in (*metadata.data_vendors.values(), *metadata.tool_vendors.values()):
        configured.update(
            vendor
            for vendor in (item.strip() for item in str(vendor_chain).split(","))
            if vendor and vendor != "default"
        )
    return configured


@dataclass(frozen=True)
class BuiltRunSnapshot:
    content: dict
    sha256: str


def build_run_snapshot(
    request_config: dict,
    gateway: GatewaySnapshot,
    metadata: ExecutionMetadata,
    memory: MemorySnapshot,
    model_routing: ModelRoutingPolicy | None = None,
) -> BuiltRunSnapshot:
    routing = model_routing or ModelRoutingPolicy()
    depth = Depth(request_config["depth"])
    debate_rounds, risk_rounds = DEPTH_ROUNDS[depth]
    benchmark_ticker = str(request_config.get("benchmark_ticker") or "SPY")
    content = {
        "request": dict(request_config),
        "resolved": {
            "debate_rounds": debate_rounds,
            "risk_rounds": risk_rounds,
            "benchmark_ticker": benchmark_ticker,
        },
        "gateway": {
            "model": gateway.model,
            "reasoning_effort": gateway.reasoning_effort,
            "snapshot_id": gateway.snapshot_id,
            "routes": routing.model_dump(mode="json"),
            "routing_snapshot_id": routing.snapshot_id,
        },
        "source": {
            "root_commit": metadata.root_commit,
            "tradingagents_commit": metadata.tradingagents_commit,
        },
        "prompt_schema_version": metadata.prompt_schema_version,
        "data_vendors": dict(metadata.data_vendors),
        "tool_vendors": dict(metadata.tool_vendors),
        "vendor_policies": dict(metadata.vendor_policies),
        "memory": memory.model_dump(mode="json"),
    }
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    return BuiltRunSnapshot(content=content, sha256=hashlib.sha256(canonical).hexdigest())


class SchedulerPolicyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self) -> AdmissionPolicy:
        default = AdmissionPolicy()
        await self.session.execute(
            insert_ignore(
                session_dialect(self.session),
                SchedulerPolicyRecord,
                {
                    "key": "default",
                    "content_json": default.as_dict(),
                    "version": 1,
                    "updated_by": None,
                    "updated_at": datetime.now(timezone.utc),
                },
                [SchedulerPolicyRecord.key],
            )
        )
        record = await self.session.get(SchedulerPolicyRecord, "default")
        if record is None:
            raise RuntimeError("scheduler policy seed is not visible")
        return AdmissionPolicy.from_dict(record.content_json)

    async def update(
        self,
        principal: Principal,
        policy: AdmissionPolicy,
        request_id: str,
    ) -> AdmissionPolicy:
        principal.require("assessments:admin")
        if "Admin" not in principal.roles:
            raise PermissionError("Admin role is required to update scheduler policy")
        record = await self.session.scalar(
            select(SchedulerPolicyRecord)
            .where(SchedulerPolicyRecord.key == "default")
            .with_for_update()
        )
        if record is None:
            await self.get()
            record = await self.session.scalar(
                select(SchedulerPolicyRecord)
                .where(SchedulerPolicyRecord.key == "default")
                .with_for_update()
            )
        if record is None:
            raise RuntimeError("scheduler policy is unavailable")

        user_id = await self.session.scalar(
            select(User.id).where(
                User.issuer == principal.issuer,
                User.subject == principal.subject,
            )
        )
        if user_id is None:
            raise RuntimeError("scheduler policy actor is not synchronized")
        old_value = dict(record.content_json)
        record.content_json = policy.as_dict()
        record.version += 1
        record.updated_by = user_id
        record.updated_at = datetime.now(timezone.utc)
        self.session.add(
            AuditEvent(
                actor_type=principal.actor_type,
                actor_id=principal.subject,
                action="scheduler.policy.update",
                object_type="scheduler_policy",
                object_id="default",
                request_id=request_id,
                metadata_json={"old": old_value, "new": policy.as_dict()},
            )
        )
        await self.session.flush()
        return policy


class SchedulerRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def admit_one(
        self,
        policy: AdmissionPolicy,
        gateway: GatewaySnapshot,
        system: SystemSnapshot,
        metadata: ExecutionMetadata,
        model_routing: ModelRoutingPolicy | None = None,
        *,
        external_blockers: tuple[str, ...] = (),
    ) -> AdmissionDecision:
        admission_lock = await acquire_transaction_lock(
            self.session,
            "global:admission",
            wait=False,
        )
        if not admission_lock:
            return AdmissionDecision(False, ("admission_lock",))

        now = datetime.now(timezone.utc)
        self.session.add(
            GatewayHealthSample(
                sampled_at=now,
                healthy=True,
                latency_ms=gateway.latency_ms,
                detail_json={"system": system.__dict__},
                active_completions=gateway.active_completions,
                model=gateway.model,
                reasoning_effort=gateway.reasoning_effort,
                snapshot_id=gateway.snapshot_id,
            )
        )

        candidates = (
            await self.session.execute(
                select(AssessmentRun, AssessmentRequest, Instrument, AssessmentBatch)
                .join(AssessmentRequest, AssessmentRun.request_id == AssessmentRequest.id)
                .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
                .join(AssessmentBatch, AssessmentRequest.batch_id == AssessmentBatch.id)
                .where(AssessmentRun.status == RunStatus.QUEUED.value)
                .order_by(AssessmentRun.created_at, AssessmentRun.id)
                .with_for_update(skip_locked=True)
            )
        ).all()
        if not candidates:
            return AdmissionDecision(False, ("queue_empty",))

        candidate = None
        for queued_candidate in candidates:
            _, _, queued_instrument, _ = queued_candidate
            ticker_lock = await acquire_transaction_lock(
                self.session,
                f"ticker:{queued_instrument.canonical_ticker}",
                wait=False,
            )
            if not ticker_lock:
                continue
            ticker_active = await self.session.scalar(
                select(func.count())
                .select_from(AssessmentRun)
                .join(AssessmentRequest, AssessmentRun.request_id == AssessmentRequest.id)
                .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
                .where(
                    Instrument.canonical_ticker == queued_instrument.canonical_ticker,
                    AssessmentRun.status.in_(ACTIVE_RUN_STATUSES),
                )
            )
            if not ticker_active:
                candidate = queued_candidate
                break

        if candidate is None:
            return AdmissionDecision(False, ("ticker_active",))
        run, request, instrument, batch = candidate

        active_runs = int(
            await self.session.scalar(
                select(func.count())
                .select_from(AssessmentRun)
                .where(AssessmentRun.status.in_(ACTIVE_RUN_STATUSES))
            )
            or 0
        )
        circuits = CircuitBreakerRepository(self.session)
        configured_vendors = _configured_vendors(metadata)
        persisted_circuits = await circuits.blockers(now, vendors=configured_vendors)
        open_circuits = tuple(sorted({*persisted_circuits, *external_blockers}))
        capacity = CapacitySnapshot(active_runs, gateway, system, open_circuits)
        decision = policy.evaluate(capacity)
        if not decision.allowed:
            return decision
        await circuits.acquire_expired_probes(now, vendors=configured_vendors)

        defaults = dict(batch.defaults_json)
        memory_mode = MemoryMode(defaults.get("memory_mode", MemoryMode.INDEPENDENT.value))
        request_config = {
            "ticker": instrument.canonical_ticker,
            "asset_type": instrument.asset_type,
            "analysis_date": request.analysis_date.isoformat(),
            "analysts": defaults["analysts"],
            "depth": defaults["depth"],
            "memory_mode": memory_mode.value,
            "language": defaults["language"],
            **dict(request.requested_config_json),
        }
        memory = await HistoricalMemoryRepository(self.session).build(
            ticker=instrument.canonical_ticker,
            analysis_date=request.analysis_date,
            mode=memory_mode,
        )
        built = build_run_snapshot(
            request_config,
            gateway,
            metadata,
            memory,
            model_routing,
        )
        snapshot = await self.session.scalar(
            select(RunConfigSnapshot).where(RunConfigSnapshot.sha256 == built.sha256)
        )
        if snapshot is None:
            snapshot = RunConfigSnapshot(
                content_json=built.content,
                sha256=built.sha256,
                gateway_snapshot_id=gateway.snapshot_id,
            )
            self.session.add(snapshot)
            await self.session.flush()

        if run.config_snapshot_id is not None and run.config_snapshot_id != snapshot.id:
            raise RuntimeError("run configuration snapshot is already pinned")
        assert_transition(RunStatus(run.status), RunStatus.ADMITTED)
        run.config_snapshot_id = snapshot.id
        run.status = RunStatus.ADMITTED.value
        run.admitted_at = now
        run.version += 1
        await AssessmentRepository(self.session).append_event(
            run.id,
            "assessment.admitted",
            {
                "config_snapshot_sha256": built.sha256,
                "memory_mode": memory.mode.value,
                "memory_entry_count": len(memory.entries),
                "memory_snapshot_sha256": memory.snapshot_sha256,
            },
        )
        return decision
