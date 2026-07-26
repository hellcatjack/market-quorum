import json
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from tradingng_platform.assessments.contracts import MemoryMode
from tradingng_platform.memory import MemoryCandidate, build_memory_snapshot
from tradingng_platform.runner.callbacks import AuditCallback
from tradingng_platform.runner.contracts import RunnerInput
from tradingng_platform.runner.tradingagents import TradingAgentsRunner

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
        "X-TradingNG-Codex-Model": "gpt-5.6-sol",
        "X-TradingNG-Codex-Reasoning-Effort": "xhigh",
    }
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


def test_callback_records_visible_llm_exchange_without_hidden_reasoning(tmp_path):
    callback = AuditCallback(tmp_path, {}, {})
    callback.on_chat_model_start(
        {"name": "codex"},
        [[HumanMessage(content="visible prompt")]],
        run_id="llm-1",
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
