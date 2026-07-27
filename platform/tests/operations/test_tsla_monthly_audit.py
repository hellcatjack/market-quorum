from copy import deepcopy
from datetime import date

import pytest
from tsla_monthly_audit import (
    AUDIT_DATES,
    AuditFailure,
    load_state,
    save_state,
    validate_checkpoint,
)


@pytest.fixture
def valid_checkpoint():
    return {
        "run": {
            "id": "00000000-0000-0000-0000-000000000012",
            "ticker": "TSLA",
            "analysis_date": "2025-08-29",
            "status": "succeeded",
            "data_vendors": {
                "core_stock_apis": "alpha_vantage",
                "technical_indicators": "alpha_vantage",
                "fundamental_data": "alpha_vantage",
                "news_data": "alpha_vantage",
            },
            "memory": {
                "mode": "historical",
                "sources": [
                    {
                        "source_run_id": "00000000-0000-0000-0000-000000000011",
                        "validation_id": "00000000-0000-0000-0000-000000000021",
                        "analysis_date": "2025-07-31",
                        "exit_session": "2025-08-08",
                        "horizon": 5,
                    }
                ],
            },
        },
        "steps": [
            {
                "name": name,
                "status": "completed",
                "started_at": f"2026-07-27T00:0{index}:00Z",
                "finished_at": f"2026-07-27T00:0{index}:30Z",
            }
            for index, name in enumerate(
                (
                    "analyst_research",
                    "research_debate",
                    "trader_plan",
                    "risk_debate",
                    "portfolio_decision",
                )
            )
        ],
        "decision": {
            "rating": "Hold",
            "executive_summary": "Point-in-time summary",
            "investment_thesis": "Point-in-time thesis",
            "price_target": None,
            "time_horizon": "6-12 months",
        },
        "validations": [
            {
                "horizon": horizon,
                "status": "completed",
                "calculation_version": "validation.v2",
                "provider_id": "alphavantage",
                "provider_adapter_version": "alphavantage.v1",
                "normalization_version": "prices.v1",
                "entry_session": "2025-08-29",
                "exit_session": exit_session,
            }
            for horizon, exit_session in (
                (1, "2025-09-02"),
                (5, "2025-09-08"),
                (20, "2025-09-29"),
            )
        ],
        "artifacts": [
            {
                "id": "00000000-0000-0000-0000-000000000031",
                "kind": "final_report",
                "sha256": "a" * 64,
                "integrity_verified": True,
            }
        ],
    }


def test_schedule_has_one_session_per_month_and_a_mature_final_cutoff():
    assert (
        date(2025, 7, 31),
        date(2025, 8, 29),
        date(2025, 9, 30),
        date(2025, 10, 31),
        date(2025, 11, 28),
        date(2025, 12, 31),
        date(2026, 1, 30),
        date(2026, 2, 27),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 29),
        date(2026, 6, 25),
    ) == AUDIT_DATES
    assert len({(item.year, item.month) for item in AUDIT_DATES}) == 12


def test_checkpoint_accepts_complete_alpha_only_point_in_time_record(valid_checkpoint):
    summary = validate_checkpoint(valid_checkpoint)

    assert summary["analysis_date"] == "2025-08-29"
    assert summary["memory_source_count"] == 1
    assert summary["validation_horizons"] == [1, 5, 20]
    assert summary["artifact_count"] == 1


def test_checkpoint_rejects_nonexclusive_research_vendor(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    invalid["run"]["data_vendors"]["news_data"] = "alpha_vantage,yfinance"

    with pytest.raises(AuditFailure, match="exclusive"):
        validate_checkpoint(invalid)


def test_checkpoint_rejects_lookahead_memory(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    invalid["run"]["memory"]["sources"][0]["exit_session"] = "2025-08-29"

    with pytest.raises(AuditFailure, match="look-ahead"):
        validate_checkpoint(invalid)


@pytest.mark.parametrize("status", ["running", "failed", "cancelled"])
def test_checkpoint_rejects_noncompleted_step(valid_checkpoint, status):
    invalid = deepcopy(valid_checkpoint)
    invalid["steps"][2]["status"] = status

    with pytest.raises(AuditFailure, match="steps"):
        validate_checkpoint(invalid)


def test_checkpoint_rejects_missing_step_timestamp(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    invalid["steps"][0]["finished_at"] = None

    with pytest.raises(AuditFailure, match="timestamp"):
        validate_checkpoint(invalid)


def test_checkpoint_rejects_incomplete_or_nonalpha_validation(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    invalid["validations"][2]["provider_id"] = "yfinance"

    with pytest.raises(AuditFailure, match="Alpha Vantage"):
        validate_checkpoint(invalid)


def test_checkpoint_rejects_missing_decision_content(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    invalid["decision"]["time_horizon"] = None

    with pytest.raises(AuditFailure, match="decision"):
        validate_checkpoint(invalid)


def test_checkpoint_rejects_unverified_artifact(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    invalid["artifacts"][0]["integrity_verified"] = False

    with pytest.raises(AuditFailure, match="artifact"):
        validate_checkpoint(invalid)


def test_checkpoint_rejects_more_than_five_or_duplicate_memory_runs(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    source = invalid["run"]["memory"]["sources"][0]
    invalid["run"]["memory"]["sources"] = [deepcopy(source) for _ in range(2)]

    with pytest.raises(AuditFailure, match="distinct"):
        validate_checkpoint(invalid)

    invalid["run"]["memory"]["sources"] = [
        {
            **deepcopy(source),
            "source_run_id": f"00000000-0000-0000-0000-{index:012d}",
            "validation_id": f"10000000-0000-0000-0000-{index:012d}",
        }
        for index in range(6)
    ]
    with pytest.raises(AuditFailure, match="at most five"):
        validate_checkpoint(invalid)


def test_state_round_trip_is_atomic_and_rejects_secrets(tmp_path):
    path = tmp_path / "state.json"
    state = {"version": 1, "checkpoints": {"2025-07-31": {"status": "submitted"}}}

    save_state(path, state)

    assert load_state(path) == state
    assert not list(tmp_path.glob(".*.tmp"))
    with pytest.raises(AuditFailure, match="secret"):
        save_state(path, {"access_token": "must-not-persist"})
