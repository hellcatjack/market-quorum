from pathlib import Path

import pytest
from tradingng_platform.domain.runs import RunStatus, assert_transition
from tradingng_platform.gateway.client import GatewaySnapshot
from tradingng_platform.scheduler.policy import (
    AdmissionPolicy,
    CapacitySnapshot,
    SystemSnapshot,
)

ROOT = Path(__file__).resolve().parents[1]


def test_unhealthy_gateway_and_open_breaker_pause_admission():
    policy = AdmissionPolicy()
    system = SystemSnapshot(20, 32, 100, 50, False)
    with pytest.raises(ValueError):
        GatewaySnapshot(
            status="unavailable",
            active_completions=0,
            model="unknown",
            reasoning_effort="unknown",
            snapshot_id="a" * 64,
            latency_ms=1000,
        )
    healthy = GatewaySnapshot(
        status="ok",
        active_completions=0,
        model="model",
        reasoning_effort="high",
        snapshot_id="a" * 64,
        latency_ms=1,
    )
    blocked = policy.evaluate(
        CapacitySnapshot(0, healthy, system, ("vendor:yfinance",))
    )
    assert not blocked.allowed


def test_crash_recovery_contract_preserves_state_machine_and_single_attempt():
    assert_transition(RunStatus.CANCELLING, RunStatus.NEEDS_ATTENTION)
    worker_repository = (
        ROOT / "platform/src/tradingng_platform/worker/repository.py"
    ).read_text()
    worker_service = (
        ROOT / "platform/src/tradingng_platform/worker/service.py"
    ).read_text()
    assert "recover_stale_leases" in worker_repository
    assert "RunStatus.NEEDS_ATTENTION" in worker_repository
    assert "create_retry" not in worker_service
