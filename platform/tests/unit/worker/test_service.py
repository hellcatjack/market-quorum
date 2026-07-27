import json
import uuid
from datetime import date

import pytest

from tradingng_platform.assessments.contracts import MemoryMode
from tradingng_platform.memory import build_memory_snapshot
from tradingng_platform.runner.contracts import DependencyHealthEvent, RunnerEvent
from tradingng_platform.worker.repository import ClaimedRun
from tradingng_platform.worker.service import RunnerProtocol, build_runner_input


def _event(sequence, event_type="stage", name="running_analysts"):
    return json.dumps(
        {
            "sequence": sequence,
            "type": event_type,
            "name": name,
            "payload": {"status": name},
            "emitted_at": "2026-07-25T12:00:00Z",
        }
    )


def test_runner_protocol_requires_contiguous_sequence_and_terminal_result():
    protocol = RunnerProtocol()

    first = protocol.consume(_event(1))
    result = protocol.consume(_event(2, "result", "assessment.completed"))

    assert isinstance(first, RunnerEvent)
    assert result.type == "result"
    assert protocol.saw_result
    with pytest.raises(ValueError, match="after terminal result"):
        protocol.consume(_event(3))


def test_runner_protocol_rejects_non_contiguous_sequence():
    protocol = RunnerProtocol()

    with pytest.raises(ValueError, match="expected runner sequence 1"):
        protocol.consume(_event(2))


def test_runner_protocol_preserves_stable_dependency_error_code():
    protocol = RunnerProtocol()

    protocol.consume(
        json.dumps(
            {
                "sequence": 1,
                "type": "error",
                "name": "runner.failed",
                "payload": {
                    "error_type": "RateLimitError",
                    "error_code": "gateway_overload",
                },
                "emitted_at": "2026-07-25T12:00:00Z",
            }
        )
    )

    assert protocol.last_error_code == "gateway_overload"


def test_claim_snapshot_builds_isolated_runner_input(tmp_path):
    run_id = uuid.UUID(int=1)
    claim = ClaimedRun(
        run_id=run_id,
        ticker="NVDA",
        asset_type="stock",
        analysis_date=date(2026, 7, 25),
        snapshot={
            "request": {
                "analysts": ["market", "news"],
                "language": "Chinese",
            },
            "resolved": {"debate_rounds": 3, "risk_rounds": 3},
            "gateway": {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
            },
            "data_vendors": {"core_stock_apis": "yfinance"},
            "tool_vendors": {},
            "vendor_policies": {
                "alpha_vantage": {
                    "requests_per_minute": 42,
                    "retry_attempts": 4,
                    "retry_base_seconds": 3,
                    "retry_max_seconds": 30,
                }
            },
            "memory": build_memory_snapshot(
                MemoryMode.INDEPENDENT,
                "NVDA",
                date(2026, 7, 25),
                (),
            ).model_dump(mode="json"),
        },
    )

    runner_input = build_runner_input(
        claim,
        job_dir=tmp_path / "jobs",
        gateway_url="http://127.0.0.1:8000",
    )

    assert runner_input.run_id == run_id
    assert runner_input.work_dir == tmp_path / "jobs" / str(run_id)
    assert runner_input.debate_rounds == 3
    assert runner_input.codex_reasoning_effort == "xhigh"
    assert runner_input.memory.mode is MemoryMode.INDEPENDENT
    assert runner_input.alpha_vantage_requests_per_minute == 42
    assert runner_input.alpha_vantage_retry_attempts == 4
    assert runner_input.alpha_vantage_retry_base_seconds == 3
    assert runner_input.alpha_vantage_retry_max_seconds == 30
    assert runner_input.alpha_vantage_coordination_dir == tmp_path / "vendor-limits"


def test_old_claim_snapshot_without_memory_remains_independent(tmp_path):
    claim = ClaimedRun(
        run_id=uuid.UUID(int=2),
        ticker="NVDA",
        asset_type="stock",
        analysis_date=date(2026, 7, 25),
        snapshot={
            "request": {
                "analysts": ["market"],
                "language": "Chinese",
            },
            "resolved": {"debate_rounds": 1, "risk_rounds": 1},
            "gateway": {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
            },
            "data_vendors": {},
            "tool_vendors": {},
        },
    )

    runner_input = build_runner_input(
        claim,
        job_dir=tmp_path / "jobs",
        gateway_url="http://127.0.0.1:8000",
    )

    assert runner_input.memory.mode is MemoryMode.INDEPENDENT
    assert runner_input.memory.entries == ()
    assert runner_input.alpha_vantage_requests_per_minute == 75
    assert runner_input.alpha_vantage_retry_attempts == 6


def test_dependency_health_event_requires_vendor_identity():
    with pytest.raises(ValueError, match="vendor and category"):
        DependencyHealthEvent.model_validate(
            {
                "scope": "vendor",
                "healthy": False,
                "latency_ms": 10,
                "error_code": "vendor_rate_limit",
                "observed_at": "2026-07-25T12:00:00Z",
            }
        )
