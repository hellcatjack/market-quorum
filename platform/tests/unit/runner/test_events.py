import uuid
from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from tradingng_platform.runner.contracts import RunnerEvent, RunnerInput
from tradingng_platform.runner.events import StageTracker


def test_runner_models_round_trip_as_strict_json_lines(tmp_path):
    runner_input = RunnerInput(
        run_id=uuid.UUID(int=1),
        ticker="NVDA",
        asset_type="stock",
        analysis_date=date(2026, 7, 25),
        analysts=("market", "news"),
        debate_rounds=3,
        risk_rounds=3,
        language="Chinese",
        gateway_url="http://127.0.0.1:8000",
        codex_model="gpt-5.6-sol",
        codex_reasoning_effort="xhigh",
        work_dir=tmp_path,
        data_vendors={"core_stock_apis": "yfinance"},
        tool_vendors={},
    )
    event = RunnerEvent(
        sequence=1,
        type="stage",
        name="running_analysts",
        payload={"status": "running_analysts"},
        emitted_at=datetime.now(timezone.utc),
    )

    assert RunnerInput.model_validate_json(runner_input.model_dump_json()) == runner_input
    assert RunnerEvent.model_validate_json(event.model_dump_json()) == event
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RunnerEvent.model_validate({**event.model_dump(), "unknown": True})


def test_stage_tracker_emits_progress_without_regressing():
    tracker = StageTracker()

    market = tracker.consume({"market_report": "market"})
    repeated = tracker.consume({"market_report": "market"})
    analyst_progress = tracker.consume({"market_report": "market", "news_report": "news"})
    research = tracker.consume(
        {
            "market_report": "market",
            "news_report": "news",
            "investment_debate_state": {"history": "debate"},
        }
    )
    late_analyst_change = tracker.consume(
        {
            "market_report": "changed",
            "news_report": "news",
            "investment_debate_state": {"history": "debate"},
        }
    )

    assert market.status == "running_analysts" and market.transitioned
    assert repeated is None
    assert analyst_progress.status == "running_analysts" and not analyst_progress.transitioned
    assert research.status == "research_debate" and research.transitioned
    assert late_analyst_change is None


def test_stage_tracker_ignores_empty_full_state_debate_scaffolding():
    tracker = StageTracker()
    initial_state = {
        "market_report": "",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "",
        "investment_debate_state": {
            "bull_history": "",
            "bear_history": "",
            "history": "",
            "current_response": "",
            "judge_decision": "",
            "count": 0,
        },
        "risk_debate_state": {
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "history": "",
            "latest_speaker": "",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "judge_decision": "",
            "count": 0,
        },
    }

    assert tracker.consume(initial_state) is None
    market = tracker.consume({**initial_state, "market_report": "market analysis"})
    assert market.status == "running_analysts"
