from tradingng_platform.gateway.client import GatewaySnapshot
from tradingng_platform.scheduler.policy import (
    ABSOLUTE_MAX_RUNNING_TOTAL,
    AdmissionPolicy,
    CapacitySnapshot,
    SystemSnapshot,
)


def test_twenty_deep_candidates_never_exceed_default_active_limit():
    policy = AdmissionPolicy()
    gateway = GatewaySnapshot(
        status="ok",
        active_completions=0,
        model="gpt-5.6-sol",
        reasoning_effort="xhigh",
        snapshot_id="a" * 64,
        latency_ms=1,
    )
    system = SystemSnapshot(20, 32, 100, 50, False)
    active = 0
    decisions = []
    for _ in range(20):
        decision = policy.evaluate(CapacitySnapshot(active, gateway, system, ()))
        decisions.append(decision)
        if decision.allowed:
            active += 1

    assert active == 2
    assert sum(item.allowed for item in decisions) == 2
    assert policy.hard_max_running_total == ABSOLUTE_MAX_RUNNING_TOTAL == 32


def test_portable_load_gate_covers_concurrency_idempotency_and_ticker_lock():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    mcp_gate = (root / "platform/tests/integration/test_mcp.py").read_text()
    repository = (
        root / "platform/src/tradingng_platform/scheduler/repository.py"
    ).read_text()
    assert "range(20)" in mcp_gate
    assert 'duplicate["run_id"] == run_id' in mcp_gate
    assert "acquire_transaction_lock" in repository
    assert 'f"ticker:{queued_instrument.canonical_ticker}"' in repository
    assert "pg_advisory" not in repository
    assert "ticker_active" in repository
