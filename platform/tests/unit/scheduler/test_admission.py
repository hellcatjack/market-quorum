import uuid
from datetime import date
from decimal import Decimal

from tradingng_platform.assessments.contracts import MemoryMode
from tradingng_platform.gateway.client import GatewaySnapshot
from tradingng_platform.memory.context import MemoryCandidate, build_memory_snapshot
from tradingng_platform.scheduler.policy import AdmissionDecision, AdmissionPolicy, SystemSnapshot
from tradingng_platform.scheduler.repository import ExecutionMetadata, build_run_snapshot
from tradingng_platform.scheduler.service import AdmissionService


class _Gateway:
    def __init__(self):
        self.calls = 0

    async def status(self):
        self.calls += 1
        return GatewaySnapshot(
            status="ok",
            active_completions=self.calls - 1,
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
            snapshot_id="a" * 64,
            latency_ms=10,
        )


class _SystemProbe:
    def sample(self):
        return SystemSnapshot(40, 20, 100, 50, False)


class _PolicyRepository:
    def __init__(self):
        self.calls = 0

    async def get(self):
        self.calls += 1
        return AdmissionPolicy(max_running_total=self.calls)


class _SchedulerRepository:
    def __init__(self):
        self.calls = []

    async def admit_one(self, policy, gateway, system, metadata):
        self.calls.append((policy, gateway, system, metadata))
        return AdmissionDecision(True, ())


async def test_admission_uses_fresh_policy_and_gateway_on_every_pass():
    gateway = _Gateway()
    policy_repository = _PolicyRepository()
    scheduler_repository = _SchedulerRepository()
    metadata = ExecutionMetadata(
        root_commit="root-sha",
        tradingagents_commit="submodule-sha",
        prompt_schema_version="v1",
        data_vendors={"market_data": "yfinance"},
        tool_vendors={"get_stock_data": "yfinance"},
    )
    service = AdmissionService(
        scheduler_repository,
        policy_repository,
        gateway,
        _SystemProbe(),
        metadata,
    )

    await service.admit_one()
    await service.admit_one()

    assert gateway.calls == 2
    assert policy_repository.calls == 2
    assert [call[0].max_running_total for call in scheduler_repository.calls] == [1, 2]
    assert [call[1].active_completions for call in scheduler_repository.calls] == [0, 1]


def test_run_snapshot_is_canonical_and_resolves_depth_rounds():
    gateway = GatewaySnapshot(
        status="ok",
        active_completions=0,
        model="gpt-5.6-sol",
        reasoning_effort="xhigh",
        snapshot_id="b" * 64,
        latency_ms=9,
    )
    metadata = ExecutionMetadata(
        root_commit="root-sha",
        tradingagents_commit="submodule-sha",
        prompt_schema_version="v1",
        data_vendors={"market_data": "yfinance"},
        tool_vendors={"get_stock_data": "yfinance"},
    )
    request_config = {
        "ticker": "NVDA",
        "asset_type": "stock",
        "analysis_date": "2026-07-25",
        "analysts": ["market", "news"],
        "depth": "deep",
        "language": "Chinese",
    }

    memory = build_memory_snapshot(
        MemoryMode.HISTORICAL,
        "NVDA",
        date(2026, 7, 25),
        [
            MemoryCandidate(
                source_run_id=uuid.UUID(int=1),
                validation_id=uuid.UUID(int=2),
                ticker="NVDA",
                analysis_date=date(2026, 7, 1),
                exit_session=date(2026, 7, 6),
                horizon=5,
                rating="Buy",
                executive_summary="Earlier conclusion",
                investment_thesis="Earlier thesis",
                price_target=Decimal("200"),
                time_horizon="6 months",
                raw_return=Decimal("0.05"),
                alpha=Decimal("0.02"),
                max_adverse_excursion=Decimal("-0.03"),
                max_favorable_excursion=Decimal("0.07"),
                direction_correct=True,
                price_target_hit=False,
            )
        ],
    )

    first = build_run_snapshot(request_config, gateway, metadata, memory)
    second = build_run_snapshot(
        dict(reversed(list(request_config.items()))),
        gateway,
        metadata,
        memory,
    )

    assert first.sha256 == second.sha256
    assert first.content["resolved"]["debate_rounds"] == 3
    assert first.content["resolved"]["risk_rounds"] == 3
    assert first.content["gateway"]["snapshot_id"] == "b" * 64
    assert first.content["memory"]["mode"] == "historical"
    assert first.content["memory"]["entries"][0]["horizon"] == 5
    assert first.content["memory"]["snapshot_sha256"] == memory.snapshot_sha256
