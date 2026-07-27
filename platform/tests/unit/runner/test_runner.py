import json
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
from langchain_core.messages import AIMessage, HumanMessage
from tradingagents.agents.analysts import sentiment_analyst
from tradingagents.dataflows import fred
from tradingagents.dataflows.alpha_vantage_common import AlphaVantageRateLimitError
from tradingagents.dataflows.interface import VENDOR_METHODS

from tradingng_platform.assessments.contracts import MemoryMode
from tradingng_platform.memory import MemoryCandidate, build_memory_snapshot
from tradingng_platform.runner.callbacks import AuditCallback
from tradingng_platform.runner.contracts import RunnerInput
from tradingng_platform.runner.tradingagents import (
    TradingAgentsRunner,
    _alpha_ohlcv_loader,
    _guard_alpha_request,
)
from tradingng_platform.vendors.alpha_vantage import AlphaVantageRetryPolicy

DECISION = """**Rating**: Hold

**Executive Summary**: Wait for a better entry.

**Investment Thesis**: Valuation offsets durable demand.

**Price Target**: 175.50

**Time Horizon**: 6-12 months"""


class _FakeGraph:
    last_config = None

    def __init__(self, selected_analysts, debug, config, callbacks, event_callback):
        type(self).last_config = config
        self.callbacks = callbacks
        self.event_callback = event_callback

    def propagate(self, ticker, analysis_date, asset_type="stock"):
        callback = self.callbacks[0]
        callback.on_tool_start(
            {"name": "get_stock_data"},
            '{"ticker":"NVDA"}',
            run_id="tool-1",
            inputs={"ticker": ticker, "api_key": "must-not-leak"},
        )
        callback.on_tool_end(
            {"close": 172.0, "token": "must-not-leak"},
            run_id="tool-1",
        )
        self.event_callback({"market_report": "market"})
        self.event_callback(
            {
                "market_report": "market",
                "investment_debate_state": {"history": "debate"},
            }
        )
        self.event_callback(
            {
                "market_report": "market",
                "investment_debate_state": {"history": "debate"},
                "final_trade_decision": DECISION,
            }
        )
        return (
            {
                "company_of_interest": ticker,
                "final_trade_decision": DECISION,
                "messages": [AIMessage(content="visible answer")],
            },
            "Hold",
        )

    def save_reports(self, final_state, ticker, save_path):
        save_path.mkdir(parents=True, exist_ok=True)
        report = save_path / "complete_report.md"
        report.write_text("report", encoding="utf-8")
        return save_path


class _TemporalProbeGraph(_FakeGraph):
    observed_tools = None

    def propagate(self, ticker, analysis_date, asset_type="stock"):
        type(self).observed_tools = {
            "get_fundamentals": VENDOR_METHODS["get_fundamentals"]["yfinance"](
                ticker, analysis_date
            ),
            "get_insider_transactions": VENDOR_METHODS["get_insider_transactions"]["yfinance"](
                ticker
            ),
            "get_prediction_markets": VENDOR_METHODS["get_prediction_markets"]["polymarket"](
                ticker, 5
            ),
            "fetch_stocktwits_messages": sentiment_analyst.fetch_stocktwits_messages(
                ticker, limit=30
            ),
            "fetch_reddit_posts": sentiment_analyst.fetch_reddit_posts(ticker),
        }
        return super().propagate(ticker, analysis_date, asset_type)


class _FredTemporalProbeGraph(_FakeGraph):
    observed_macro = None

    def propagate(self, ticker, analysis_date, asset_type="stock"):
        type(self).observed_macro = VENDOR_METHODS["get_macro_indicators"]["fred"](
            "cpi", "2099-12-31", 365
        )
        return super().propagate(ticker, analysis_date, asset_type)


def _runner_input(tmp_path):
    memory = build_memory_snapshot(
        MemoryMode.HISTORICAL,
        "NVDA",
        date(2026, 7, 25),
        [
            MemoryCandidate(
                source_run_id=uuid.UUID(int=2),
                validation_id=uuid.UUID(int=3),
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
    return RunnerInput(
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
        fast_codex_model="gpt-5.6-terra",
        fast_codex_reasoning_effort="medium",
        slow_codex_model="gpt-5.6-sol",
        slow_codex_reasoning_effort="high",
        work_dir=tmp_path / "job",
        data_vendors={"core_stock_apis": "yfinance"},
        tool_vendors={"get_stock_data": "yfinance"},
        memory=memory,
    )


def test_runner_isolated_config_artifacts_and_redaction(tmp_path):
    events = []
    runner_input = _runner_input(tmp_path)
    runner = TradingAgentsRunner(
        runner_input,
        graph_factory=_FakeGraph,
        event_sink=events.append,
    )

    result = runner.run()

    headers = _FakeGraph.last_config["llm_default_headers"]
    assert headers == {
        "X-TradingNG-Run-ID": str(runner_input.run_id),
        "X-TradingNG-Codex-Fast-Model": "gpt-5.6-terra",
        "X-TradingNG-Codex-Fast-Reasoning-Effort": "medium",
        "X-TradingNG-Codex-Slow-Model": "gpt-5.6-sol",
        "X-TradingNG-Codex-Slow-Reasoning-Effort": "high",
    }
    assert _FakeGraph.last_config["quick_think_llm"] == "codex-fast"
    assert _FakeGraph.last_config["deep_think_llm"] == "codex-slow"
    assert _FakeGraph.last_config["max_debate_rounds"] == 3
    assert _FakeGraph.last_config["max_risk_discuss_rounds"] == 3
    assert [event.name for event in events if event.type == "stage"] == [
        "running_analysts",
        "research_debate",
        "portfolio_decision",
    ]
    assert events[-1].type == "result"
    assert result.decision["rating"] == "Hold"
    assert (
        json.loads((runner_input.work_dir / "decision.json").read_text())["price_target"]
        == "175.50"
    )
    assert (
        json.loads((runner_input.work_dir / "final_state.json").read_text())["messages"][0][
            "content"
        ]
        == "visible answer"
    )
    evidence = (runner_input.work_dir / "working" / "evidence.jsonl").read_text()
    assert "must-not-leak" not in evidence
    assert evidence.count("[REDACTED]") == 2
    assert (runner_input.work_dir / "reports" / "complete_report.md").is_file()
    memory_path = runner_input.work_dir / "memory" / "trading_memory.md"
    memory_context_path = runner_input.work_dir / "working" / "memory_context.json"
    assert "Earlier conclusion" in memory_path.read_text(encoding="utf-8")
    assert (
        json.loads(memory_context_path.read_text(encoding="utf-8"))["snapshot_sha256"]
        == runner_input.memory.snapshot_sha256
    )
    assert "memory_context" in [event.name for event in events if event.type == "artifact"]


def test_historical_runner_blocks_current_snapshot_tools_and_restores_routes(tmp_path, monkeypatch):
    current_snapshots = {
        (method, vendor): (lambda *args, _method=method: f"CURRENT_DATA: {_method}")
        for method, vendor in (
            ("get_fundamentals", "yfinance"),
            ("get_insider_transactions", "yfinance"),
            ("get_prediction_markets", "polymarket"),
        )
    }
    for (method, vendor), current_snapshot in current_snapshots.items():
        monkeypatch.setitem(VENDOR_METHODS[method], vendor, current_snapshot)
    current_social = {
        "fetch_stocktwits_messages": lambda *args, **kwargs: "CURRENT_STOCKTWITS_DATA",
        "fetch_reddit_posts": lambda *args, **kwargs: "CURRENT_REDDIT_DATA",
    }
    for name, current_snapshot in current_social.items():
        monkeypatch.setattr(sentiment_analyst, name, current_snapshot)
    runner_input = _runner_input(tmp_path).model_copy(update={"analysis_date": date(2000, 1, 3)})

    TradingAgentsRunner(runner_input, graph_factory=_TemporalProbeGraph).run()

    for method in (
        "get_fundamentals",
        "get_insider_transactions",
        "get_prediction_markets",
    ):
        assert _TemporalProbeGraph.observed_tools[method] == (
            f"POINT_IN_TIME_DATA_UNAVAILABLE: {method} cannot guarantee data as "
            "of 2000-01-03. Proceed without it and do not use current data, "
            "estimate, or fabricate values."
        )
    for (method, vendor), current_snapshot in current_snapshots.items():
        assert VENDOR_METHODS[method][vendor] is current_snapshot
    for name, current_snapshot in current_social.items():
        assert _TemporalProbeGraph.observed_tools[name] == (
            f"<point-in-time unavailable: {name} cannot guarantee data as of "
            "2000-01-03; proceed without it and do not use current data>"
        )
        assert getattr(sentiment_analyst, name) is current_snapshot


def test_historical_runner_uses_fred_vintage_available_on_analysis_date(tmp_path, monkeypatch):
    requests = []

    def fake_fred_request(path, params):
        requests.append((path, dict(params)))
        if path == "series":
            return {
                "seriess": [
                    {
                        "title": "Consumer Price Index",
                        "units_short": "Index",
                        "frequency": "Monthly",
                        "seasonal_adjustment_short": "SA",
                    }
                ]
            }
        return {
            "observations": [
                {"date": "1999-12-01", "value": "100.0"},
                {"date": "2000-01-01", "value": "101.0"},
            ]
        }

    original_route = VENDOR_METHODS["get_macro_indicators"]["fred"]
    monkeypatch.setattr(fred, "_request", fake_fred_request)
    runner_input = _runner_input(tmp_path).model_copy(update={"analysis_date": date(2000, 1, 3)})

    TradingAgentsRunner(runner_input, graph_factory=_FredTemporalProbeGraph).run()

    observation_request = next(params for path, params in requests if path == "series/observations")
    assert observation_request["observation_end"] == "2000-01-03"
    assert observation_request["realtime_start"] == "2000-01-03"
    assert observation_request["realtime_end"] == "2000-01-03"
    assert _FredTemporalProbeGraph.observed_macro.startswith(
        "POINT_IN_TIME_VINTAGE: FRED observations are limited to values available on 2000-01-03."
    )
    assert VENDOR_METHODS["get_macro_indicators"]["fred"] is original_route


def test_callback_records_visible_llm_exchange_without_hidden_reasoning(tmp_path):
    callback = AuditCallback(
        tmp_path,
        {},
        {},
        model_routes={
            "codex-fast": {
                "route": "fast",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "medium",
            }
        },
    )
    callback.on_chat_model_start(
        {"name": "LocalCompatibleChatOpenAI"},
        [[HumanMessage(content="visible prompt")]],
        run_id="llm-1",
        invocation_params={"model_name": "codex-fast"},
    )
    response = SimpleNamespace(
        generations=[
            [
                SimpleNamespace(
                    message=AIMessage(
                        content="visible response",
                        additional_kwargs={"reasoning_content": "hidden chain"},
                    )
                )
            ]
        ],
        llm_output={
            "token_usage": {"input_tokens": 10, "output_tokens": 4},
            "authorization": "must-not-leak",
        },
    )

    callback.on_llm_end(response, run_id="llm-1")

    interaction = callback.llm_path.read_text(encoding="utf-8")
    record = json.loads(interaction)
    assert "visible prompt" in interaction
    assert "visible response" in interaction
    assert "hidden chain" not in interaction
    assert "must-not-leak" not in interaction
    assert "[REDACTED]" in interaction
    assert record["status"] == "completed"
    assert record["route"] == "fast"
    assert record["model_alias"] == "codex-fast"
    assert record["physical_model"] == "gpt-5.6-terra"
    assert record["reasoning_effort"] == "medium"
    assert record["completed_at"]
    assert isinstance(record["duration_ms"], int)


def test_callback_records_safe_failed_llm_interaction(tmp_path):
    callback = AuditCallback(tmp_path, {}, {})
    callback.on_chat_model_start(
        {"name": "codex"},
        [[HumanMessage(content="visible prompt")]],
        run_id="llm-failed",
    )
    error_type = type("GatewayTimeoutError", (Exception,), {})

    callback.on_llm_error(
        error_type("authorization=must-not-leak"),
        run_id="llm-failed",
    )

    interaction = callback.llm_path.read_text(encoding="utf-8")
    record = json.loads(interaction)
    assert record["status"] == "failed"
    assert record["error_type"] == "GatewayTimeoutError"
    assert record["error_code"] == "gateway_unavailable"
    assert record["completed_at"]
    assert isinstance(record["duration_ms"], int)
    assert "authorization" not in interaction
    assert "must-not-leak" not in interaction


def test_callback_records_vendor_failure_as_safe_health_event(tmp_path):
    callback = AuditCallback(
        tmp_path,
        {"core_stock_apis": "alpha_vantage"},
        {"get_stock_data": "alpha_vantage"},
    )
    error_type = type("AlphaVantageRateLimitError", (Exception,), {})
    callback.on_tool_start(
        {"name": "get_stock_data"},
        '{"ticker":"NVDA","api_key":"must-not-leak"}',
        run_id="tool-error",
    )

    callback.on_tool_error(error_type("api_key=must-not-leak"), run_id="tool-error")

    health = json.loads(callback.health_path.read_text(encoding="utf-8"))
    assert health["scope"] == "vendor"
    assert health["vendor"] == "alpha_vantage"
    assert health["category"] == "core_stock_apis"
    assert health["error_code"] == "vendor_rate_limit"
    assert "must-not-leak" not in callback.health_path.read_text(encoding="utf-8")


class _FakeRateGate:
    def __init__(self):
        self.acquired = 0
        self.deferred = []

    def acquire(self):
        self.acquired += 1

    def defer(self, seconds):
        self.deferred.append(seconds)


def test_alpha_request_guard_retries_rate_limit_on_same_provider():
    responses = [
        AlphaVantageRateLimitError("secret provider detail"),
        "time,MACD,MACD_Hist,MACD_Signal\n2026-07-24,1.2,0.2,1.0\n",
    ]
    calls = []
    sleeps = []
    gate = _FakeRateGate()

    def request(function_name, params):
        calls.append((function_name, params))
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    guarded = _guard_alpha_request(
        request,
        gate,
        AlphaVantageRetryPolicy(attempts=3, base_seconds=5, max_seconds=60),
        sleep=sleeps.append,
    )

    result = guarded("MACD", {"symbol": "JPM", "datatype": "csv"})

    assert result.startswith("time,MACD,MACD_Hist,MACD_Signal")
    assert [call[0] for call in calls] == ["MACD", "MACD"]
    assert gate.acquired == 2
    assert gate.deferred == [5]
    assert sleeps == [5]


def test_alpha_request_guard_retries_json_error_instead_of_returning_fake_csv():
    responses = [
        '{"Error Message":"temporary upstream error"}',
        "time,MACD,MACD_Hist,MACD_Signal\n2026-07-24,1.2,0.2,1.0\n",
    ]
    gate = _FakeRateGate()

    guarded = _guard_alpha_request(
        lambda function_name, params: responses.pop(0),
        gate,
        AlphaVantageRetryPolicy(attempts=2, base_seconds=1, max_seconds=2),
        sleep=lambda seconds: None,
    )

    assert guarded("MACD", {"symbol": "JPM"}).startswith("time,MACD")
    assert gate.acquired == 2


def test_alpha_ohlcv_loader_builds_raw_point_in_time_frame_without_yahoo():
    csv = """timestamp,open,high,low,close,adjusted_close,volume,dividend_amount,split_coefficient
2026-07-28,103,105,102,104,104,1200,0,1
2026-07-27,101,104,100,103,102,1100,1,1
2026-07-24,99,102,98,101,100,1000,0,1
"""
    calls = []

    def request(function_name, params):
        calls.append((function_name, params))
        return csv

    frame = _alpha_ohlcv_loader(request, "JPM", "2026-07-27")

    assert list(frame.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert frame["Date"].tolist() == [pd.Timestamp("2026-07-24"), pd.Timestamp("2026-07-27")]
    assert frame["Close"].tolist() == [101, 103]
    assert calls == [
        (
            "TIME_SERIES_DAILY_ADJUSTED",
            {"symbol": "JPM", "outputsize": "full", "datatype": "csv"},
        )
    ]
