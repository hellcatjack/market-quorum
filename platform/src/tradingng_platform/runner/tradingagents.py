import copy
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from tradingagents.agents.analysts import sentiment_analyst
from tradingagents.dataflows.interface import VENDOR_METHODS
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

from tradingng_platform.memory import render_tradingagents_memory
from tradingng_platform.runner.callbacks import AuditCallback
from tradingng_platform.runner.contracts import RunnerInput
from tradingng_platform.runner.events import EventEmitter, StageTracker

_DECISION_LABEL = re.compile(
    r"^\s*\*\*(Rating|Executive Summary|Investment Thesis|"
    r"Price Target|Time Horizon)\*\*\s*:\s*(.*)$",
    re.IGNORECASE,
)
_RATINGS = {"Buy", "Overweight", "Hold", "Underweight", "Sell"}
_CURRENT_SNAPSHOT_METHODS = (
    "get_fundamentals",
    "get_insider_transactions",
    "get_prediction_markets",
)
_CURRENT_SOCIAL_FETCHERS = (
    "fetch_stocktwits_messages",
    "fetch_reddit_posts",
)


@dataclass(frozen=True)
class RunnerResult:
    final_state: dict
    signal: str
    decision: dict
    report_dir: Path


class TradingAgentsRunner:
    def __init__(
        self,
        runner_input: RunnerInput,
        *,
        graph_factory=TradingAgentsGraph,
        event_sink=None,
        emitter: EventEmitter | None = None,
    ):
        self.input = runner_input
        self.graph_factory = graph_factory
        if emitter is not None:
            self.emitter = emitter
        else:
            self.emitter = EventEmitter(event_sink or _stdout_event_sink)

    def run(self) -> RunnerResult:
        directories = self._create_directories()
        memory_context_path = directories["working"] / "memory_context.json"
        _write_json(
            memory_context_path,
            self.input.memory.model_dump(mode="json"),
        )
        memory_content = render_tradingagents_memory(self.input.memory)
        if memory_content:
            _write_text(
                directories["memory"] / "trading_memory.md",
                memory_content,
            )
        callback = AuditCallback(
            directories["working"],
            self.input.data_vendors,
            self.input.tool_vendors,
        )
        stage_tracker = StageTracker()

        def on_chunk(chunk: dict) -> None:
            update = stage_tracker.consume(chunk)
            if update is None:
                return
            self.emitter.emit(
                "stage",
                update.status,
                {
                    "status": update.status,
                    "progress_key": update.progress_key,
                    "content_hash": update.content_hash,
                    "transitioned": update.transitioned,
                },
            )

        config = self._build_config(directories)
        with _historical_point_in_time_guard(self.input.analysis_date):
            graph = self.graph_factory(
                selected_analysts=self.input.analysts,
                debug=False,
                config=config,
                callbacks=[callback],
                event_callback=on_chunk,
            )
            final_state, signal = graph.propagate(
                self.input.ticker,
                self.input.analysis_date.isoformat(),
                asset_type=self.input.asset_type,
            )
        report_dir = graph.save_reports(
            final_state,
            self.input.ticker,
            save_path=directories["reports"],
        )
        decision = parse_decision(final_state["final_trade_decision"])
        final_state_path = self.input.work_dir / "final_state.json"
        decision_path = self.input.work_dir / "decision.json"
        _write_json(final_state_path, _json_safe(final_state))
        _write_json(decision_path, decision)

        artifacts = [
            ("final_state", final_state_path),
            ("decision", decision_path),
            ("reports", report_dir),
            ("memory_context", memory_context_path),
        ]
        for name, path in artifacts:
            self.emitter.emit(
                "artifact",
                name,
                {"path": path.relative_to(self.input.work_dir).as_posix()},
            )
        for name, path in (
            ("evidence", callback.evidence_path),
            ("llm_interactions", callback.llm_path),
        ):
            if path.is_file():
                self.emitter.emit(
                    "artifact",
                    name,
                    {"path": path.relative_to(self.input.work_dir).as_posix()},
                )
        self.emitter.emit(
            "result",
            "assessment.completed",
            {
                "signal": str(signal),
                "rating": decision["rating"],
                "artifact_count": len(artifacts),
            },
        )
        return RunnerResult(final_state, str(signal), decision, report_dir)

    def _create_directories(self) -> dict[str, Path]:
        self.input.work_dir.mkdir(parents=True, exist_ok=True)
        directories = {
            name: self.input.work_dir / name
            for name in (
                "cache",
                "results",
                "memory",
                "reports",
                "logs",
                "checkpoints",
                "working",
            )
        }
        for directory in directories.values():
            directory.mkdir(parents=True, exist_ok=True)
        return directories

    def _build_config(self, directories: dict[str, Path]) -> dict:
        config = copy.deepcopy(DEFAULT_CONFIG)
        config.update(
            {
                "llm_provider": "openai_compatible",
                "deep_think_llm": "codex",
                "quick_think_llm": "codex",
                "backend_url": str(self.input.gateway_url).rstrip("/") + "/v1",
                "output_language": self.input.language,
                "max_debate_rounds": self.input.debate_rounds,
                "max_risk_discuss_rounds": self.input.risk_rounds,
                "checkpoint_enabled": True,
                "data_cache_dir": str(directories["cache"]),
                "results_dir": str(directories["results"]),
                "memory_log_path": str(directories["memory"] / "trading_memory.md"),
                "data_vendors": dict(self.input.data_vendors),
                "tool_vendors": dict(self.input.tool_vendors),
                "llm_default_headers": {
                    "X-TradingNG-Run-ID": str(self.input.run_id),
                    "X-TradingNG-Codex-Model": self.input.codex_model,
                    "X-TradingNG-Codex-Reasoning-Effort": self.input.codex_reasoning_effort,
                },
            }
        )
        return config


@contextmanager
def _historical_point_in_time_guard(analysis_date: date):
    if analysis_date >= datetime.now(timezone.utc).date():
        yield
        return

    original_routes = {method: dict(VENDOR_METHODS[method]) for method in _CURRENT_SNAPSHOT_METHODS}
    original_social = {name: getattr(sentiment_analyst, name) for name in _CURRENT_SOCIAL_FETCHERS}
    try:
        for method, vendor_routes in original_routes.items():
            unavailable = _point_in_time_unavailable(method, analysis_date)
            VENDOR_METHODS[method] = dict.fromkeys(vendor_routes, unavailable)
        for name in original_social:
            setattr(
                sentiment_analyst,
                name,
                _point_in_time_social_unavailable(name, analysis_date),
            )
        yield
    finally:
        for method, vendor_routes in original_routes.items():
            VENDOR_METHODS[method] = vendor_routes
        for name, fetcher in original_social.items():
            setattr(sentiment_analyst, name, fetcher)


def _point_in_time_unavailable(method: str, analysis_date: date):
    def unavailable(*args, **kwargs) -> str:
        return (
            f"POINT_IN_TIME_DATA_UNAVAILABLE: {method} cannot guarantee data as of "
            f"{analysis_date.isoformat()}. Proceed without it and do not use current "
            "data, estimate, or fabricate values."
        )

    return unavailable


def _point_in_time_social_unavailable(name: str, analysis_date: date):
    def unavailable(*args, **kwargs) -> str:
        return (
            f"<point-in-time unavailable: {name} cannot guarantee data as of "
            f"{analysis_date.isoformat()}; proceed without it and do not use current data>"
        )

    return unavailable


def parse_decision(markdown: str) -> dict:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in markdown.splitlines():
        match = _DECISION_LABEL.match(line)
        if match:
            current = match.group(1).lower().replace(" ", "_")
            sections[current] = [match.group(2).strip()]
        elif current is not None:
            sections[current].append(line)
    values = {key: "\n".join(lines).strip() for key, lines in sections.items()}
    required = ("rating", "executive_summary", "investment_thesis")
    if any(not values.get(key) for key in required):
        raise ValueError("final decision is missing required labeled sections")
    rating = values["rating"].strip("* ").title()
    if rating not in _RATINGS:
        raise ValueError(f"unsupported portfolio rating: {rating}")
    price_target = None
    if values.get("price_target"):
        normalized = re.sub(r"[^0-9.+-]", "", values["price_target"].splitlines()[0])
        try:
            price_target = format(Decimal(normalized), "f")
        except InvalidOperation as exc:
            raise ValueError("price target is not numeric") from exc
    return {
        "rating": rating,
        "executive_summary": values["executive_summary"],
        "investment_thesis": values["investment_thesis"],
        "price_target": price_target,
        "time_horizon": values.get("time_horizon") or None,
        "structured_json": values,
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (date, datetime, Decimal, Path)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "content") and hasattr(value, "type"):
        return {
            "type": str(value.type),
            "content": _json_safe(value.content),
        }
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    return repr(value)


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _stdout_event_sink(event) -> None:
    print(event.model_dump_json(), flush=True)
