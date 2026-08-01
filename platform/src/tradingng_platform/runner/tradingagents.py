import copy
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from io import StringIO
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import requests
from tradingagents.agents.analysts import sentiment_analyst
from tradingagents.dataflows import (
    alpha_vantage_common,
    alpha_vantage_fundamentals,
    alpha_vantage_indicator,
    alpha_vantage_news,
    alpha_vantage_stock,
    fred,
    market_data_validator,
)
from tradingagents.dataflows.alpha_vantage_common import (
    AlphaVantageNotConfiguredError,
    AlphaVantageRateLimitError,
)
from tradingagents.dataflows.errors import VendorRateLimitError
from tradingagents.dataflows.interface import VENDOR_METHODS
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

from tradingng_platform.integrity.contracts import IntegrityStatus
from tradingng_platform.integrity.financials import (
    AlphaEarningsAvailabilityResolver,
    CompositeAvailabilityResolver,
    SecFilingClient,
    filter_statement_payload,
)
from tradingng_platform.integrity.policy import PointInTimeRecorder
from tradingng_platform.memory import render_tradingagents_memory
from tradingng_platform.runner.callbacks import AuditCallback
from tradingng_platform.runner.contracts import RunnerInput
from tradingng_platform.runner.events import EventEmitter, StageTracker
from tradingng_platform.vendors.alpha_vantage import (
    AlphaVantageRetryPolicy,
    CrossProcessRateGate,
    alpha_key_fingerprint,
    classify_alpha_payload,
)
from tradingng_platform.vendors.alpha_vantage_client import (
    AlphaBrokerAuthenticationError,
    AlphaBrokerRateLimitError,
    AlphaBrokerTransientError,
    SyncAlphaVantageBrokerClient,
)
from tradingng_platform.vendors.stocklean_adapter import StockLeanResearchAdapter

logger = logging.getLogger(__name__)

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
_FINANCIAL_STATEMENT_METHODS = (
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
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


class AlphaVantageTransientError(RuntimeError):
    pass


class TradingAgentsRunner:
    def __init__(
        self,
        runner_input: RunnerInput,
        *,
        graph_factory=TradingAgentsGraph,
        event_sink=None,
        emitter: EventEmitter | None = None,
        availability_resolver=None,
    ):
        self.input = runner_input
        self.graph_factory = graph_factory
        if emitter is not None:
            self.emitter = emitter
        else:
            self.emitter = EventEmitter(event_sink or _stdout_event_sink)
        self.availability_resolver = availability_resolver

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
        integrity_recorder = PointInTimeRecorder(self.input.analysis_date)
        callback = AuditCallback(
            directories["working"],
            self.input.data_vendors,
            self.input.tool_vendors,
            model_routes={
                "codex-fast": {
                    "route": "fast",
                    "model": self.input.fast_codex_model,
                    "reasoning_effort": self.input.fast_codex_reasoning_effort,
                },
                "codex-slow": {
                    "route": "slow",
                    "model": self.input.slow_codex_model,
                    "reasoning_effort": self.input.slow_codex_reasoning_effort,
                },
            },
            analysis_date=self.input.analysis_date,
            integrity_recorder=integrity_recorder,
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
        with (
            _availability_resolver_context(self.input, self.availability_resolver) as resolver,
            _alpha_vantage_run_guard(self.input),
            _stocklean_run_guard(self.input),
            _historical_point_in_time_guard(
                self.input.analysis_date,
                integrity_recorder,
                resolver,
            ),
        ):
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
        integrity_path = directories["working"] / "point_in_time_integrity.json"
        _write_json(final_state_path, _json_safe(final_state))
        _write_json(decision_path, decision)
        _write_json(
            integrity_path,
            integrity_recorder.finalize().model_dump(mode="json"),
        )

        artifacts = [
            ("final_state", final_state_path),
            ("decision", decision_path),
            ("reports", report_dir),
            ("memory_context", memory_context_path),
            ("point_in_time_integrity", integrity_path),
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
                "deep_think_llm": "codex-slow",
                "quick_think_llm": "codex-fast",
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
                    "X-TradingNG-Codex-Fast-Model": self.input.fast_codex_model,
                    "X-TradingNG-Codex-Fast-Reasoning-Effort": (
                        self.input.fast_codex_reasoning_effort
                    ),
                    "X-TradingNG-Codex-Slow-Model": self.input.slow_codex_model,
                    "X-TradingNG-Codex-Slow-Reasoning-Effort": (
                        self.input.slow_codex_reasoning_effort
                    ),
                },
            }
        )
        return config


def _guard_alpha_request(
    request,
    gate: CrossProcessRateGate,
    policy: AlphaVantageRetryPolicy,
    *,
    sleep=time.sleep,
):
    @wraps(request)
    def guarded(function_name: str, params: dict):
        for attempt in range(1, policy.attempts + 1):
            gate.acquire()
            retry_after = None
            classification = None
            try:
                result = request(function_name, params)
                if isinstance(result, str) and result.lstrip().startswith("{"):
                    try:
                        classification = classify_alpha_payload(json.loads(result))
                    except json.JSONDecodeError:
                        classification = None
                if classification is None:
                    return result
                if classification == "authentication":
                    raise AlphaVantageNotConfiguredError(
                        "Alpha Vantage rejected the configured API credentials"
                    )
            except AlphaVantageNotConfiguredError:
                raise
            except VendorRateLimitError:
                classification = "rate_limit"
            except requests.HTTPError as exc:
                response = getattr(exc, "response", None)
                if response is None or response.status_code != 429:
                    raise
                classification = "rate_limit"
                retry_after = _numeric_retry_after(response.headers.get("Retry-After"))

            if attempt == policy.attempts:
                if classification == "rate_limit":
                    raise AlphaVantageRateLimitError(
                        "Alpha Vantage rate limit persisted after delayed retries"
                    )
                raise AlphaVantageTransientError(
                    "Alpha Vantage returned transient errors after delayed retries"
                )

            delay = policy.delay(attempt, retry_after=retry_after)
            gate.defer(delay)
            logger.warning(
                "alpha_vantage_retry function=%s attempt=%d delay_seconds=%.1f",
                function_name,
                attempt,
                delay,
            )
            sleep(delay)

        raise RuntimeError("unreachable Alpha Vantage retry state")

    return guarded


def _numeric_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _alpha_ohlcv_loader(request, symbol: str, curr_date: str) -> pd.DataFrame:
    csv_data = request(
        "TIME_SERIES_DAILY_ADJUSTED",
        {"symbol": symbol, "outputsize": "full", "datatype": "csv"},
    )
    if not isinstance(csv_data, str):
        raise ValueError("Alpha Vantage daily response is not CSV text")
    frame = pd.read_csv(StringIO(csv_data))
    renamed = frame.rename(
        columns={
            "timestamp": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    required = ["Date", "Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in renamed.columns]
    if missing:
        raise ValueError(f"Alpha Vantage daily CSV is missing columns: {','.join(missing)}")
    result = renamed[required].copy()
    result["Date"] = pd.to_datetime(result["Date"], errors="coerce")
    for column in required[1:]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=required)
    result = result[result["Date"] <= pd.to_datetime(curr_date)].sort_values("Date")
    if result.empty:
        raise ValueError(f"No Alpha Vantage OHLCV rows on or before {curr_date}")
    return result.reset_index(drop=True)


@contextmanager
def _availability_resolver_context(runner_input: RunnerInput, override=None):
    if override is not None:
        yield override
        return
    cache_dir = runner_input.sec_cache_dir or runner_input.work_dir / "cache" / "sec"
    with httpx.Client(follow_redirects=True) as client:
        sec = SecFilingClient(
            client=client,
            user_agent=runner_input.sec_user_agent,
            cache_dir=cache_dir,
            timeout_seconds=runner_input.sec_request_timeout_seconds,
        )
        configured = {
            vendor.strip()
            for chain in runner_input.data_vendors.values()
            for vendor in str(chain).split(",")
            if vendor.strip()
        }
        if "stocklean" in configured:
            token = os.getenv("TRADINGNG_STOCKLEAN_INTERNAL_TOKEN", "")
            snapshot_id = runner_input.stocklean_manifest_snapshot_id or ""
            adapter = StockLeanResearchAdapter(
                str(runner_input.stocklean_url),
                token=token,
                snapshot_id=snapshot_id,
            )

            def earnings_loader(ticker):
                return adapter.get_earnings(ticker, runner_input.analysis_date.isoformat())
        else:

            def earnings_loader(ticker):
                return alpha_vantage_fundamentals._make_api_request("EARNINGS", {"symbol": ticker})

        alpha = AlphaEarningsAvailabilityResolver(earnings_loader)
        yield CompositeAvailabilityResolver(sec, alpha)


@contextmanager
def _stocklean_run_guard(runner_input: RunnerInput):
    configured = {
        vendor.strip()
        for chain in (*runner_input.data_vendors.values(), *runner_input.tool_vendors.values())
        for vendor in str(chain).split(",")
        if vendor.strip()
    }
    if "stocklean" not in configured:
        yield
        return
    token = os.getenv("TRADINGNG_STOCKLEAN_INTERNAL_TOKEN", "")
    if not token:
        raise RuntimeError("TRADINGNG_STOCKLEAN_INTERNAL_TOKEN is required")
    if not runner_input.stocklean_manifest_snapshot_id:
        raise RuntimeError("StockLean manifest snapshot is required")
    adapter = StockLeanResearchAdapter(
        str(runner_input.stocklean_url),
        token=token,
        snapshot_id=runner_input.stocklean_manifest_snapshot_id,
    )
    routes = {
        "get_stock_data": adapter.get_stock,
        "get_indicators": adapter.get_indicator,
        "get_fundamentals": adapter.get_fundamentals,
        "get_balance_sheet": adapter.get_balance_sheet,
        "get_cashflow": adapter.get_cashflow,
        "get_income_statement": adapter.get_income_statement,
        "get_news": adapter.get_news,
        "get_global_news": adapter.get_global_news,
        "get_insider_transactions": adapter.get_insider_transactions,
    }
    originals = {method: dict(VENDOR_METHODS[method]) for method in routes}
    original_loader = market_data_validator.load_ohlcv
    try:
        for method, route in routes.items():
            VENDOR_METHODS[method]["stocklean"] = route

        def load_ohlcv(symbol: str, curr_date: str):
            start = (date.fromisoformat(curr_date) - timedelta(days=500)).isoformat()
            frame = pd.read_csv(StringIO(adapter.get_stock(symbol, start, curr_date)))
            return frame.rename(
                columns={
                    "timestamp": "Date",
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                    "volume": "Volume",
                }
            )[["Date", "Open", "High", "Low", "Close", "Volume"]]

        market_data_validator.load_ohlcv = load_ohlcv
        yield
    finally:
        for method, original in originals.items():
            VENDOR_METHODS[method] = original
        market_data_validator.load_ohlcv = original_loader


@contextmanager
def _alpha_vantage_run_guard(runner_input: RunnerInput):
    configured = {
        vendor.strip()
        for chain in (*runner_input.data_vendors.values(), *runner_input.tool_vendors.values())
        for vendor in str(chain).split(",")
        if vendor.strip()
    }
    if "alpha_vantage" not in configured:
        yield
        return

    original_request = alpha_vantage_common._make_api_request
    if runner_input.alpha_vantage_broker_url is not None:
        broker = SyncAlphaVantageBrokerClient(
            str(runner_input.alpha_vantage_broker_url).rstrip("/"),
            consumer="research",
            timeout=runner_input.alpha_vantage_broker_request_timeout_seconds,
        )

        def guarded_request(function_name: str, params: dict):
            try:
                return broker.query(
                    function_name,
                    params,
                    run_id=str(runner_input.run_id),
                    analysis_date=runner_input.analysis_date.isoformat(),
                )
            except AlphaBrokerRateLimitError as error:
                raise AlphaVantageRateLimitError(str(error)) from error
            except AlphaBrokerAuthenticationError as error:
                raise AlphaVantageNotConfiguredError(str(error)) from error
            except AlphaBrokerTransientError as error:
                raise AlphaVantageTransientError(str(error)) from error
    else:
        coordination_dir = runner_input.alpha_vantage_coordination_dir
        if coordination_dir is None:
            coordination_dir = runner_input.work_dir.parent.parent / "vendor-limits"
        api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
        key_identity = alpha_key_fingerprint(api_key) if api_key else "unconfigured"
        gate = CrossProcessRateGate(
            coordination_dir / f"alpha-vantage-{key_identity}.json",
            runner_input.alpha_vantage_requests_per_minute,
        )
        policy = AlphaVantageRetryPolicy(
            attempts=runner_input.alpha_vantage_retry_attempts,
            base_seconds=runner_input.alpha_vantage_retry_base_seconds,
            max_seconds=runner_input.alpha_vantage_retry_max_seconds,
        )
        guarded_request = _guard_alpha_request(original_request, gate, policy)
    request_modules = (
        alpha_vantage_common,
        alpha_vantage_fundamentals,
        alpha_vantage_indicator,
        alpha_vantage_news,
        alpha_vantage_stock,
    )
    original_module_requests = {module: module._make_api_request for module in request_modules}
    original_loader = market_data_validator.load_ohlcv
    try:
        for module in request_modules:
            module._make_api_request = guarded_request
        market_data_validator.load_ohlcv = lambda symbol, curr_date: _alpha_ohlcv_loader(
            guarded_request,
            symbol,
            curr_date,
        )
        yield
    finally:
        for module, module_request in original_module_requests.items():
            module._make_api_request = module_request
        market_data_validator.load_ohlcv = original_loader


@contextmanager
def _historical_point_in_time_guard(
    analysis_date: date,
    recorder: PointInTimeRecorder,
    availability_resolver,
):
    if analysis_date >= datetime.now(timezone.utc).date():
        yield
        return

    original_routes = {method: dict(VENDOR_METHODS[method]) for method in _CURRENT_SNAPSHOT_METHODS}
    original_financial_routes = {
        method: dict(VENDOR_METHODS[method]) for method in _FINANCIAL_STATEMENT_METHODS
    }
    original_macro_routes = dict(VENDOR_METHODS["get_macro_indicators"])
    original_social = {name: getattr(sentiment_analyst, name) for name in _CURRENT_SOCIAL_FETCHERS}
    original_fred_request = fred._request
    try:
        for method, vendor_routes in original_routes.items():
            unavailable = _point_in_time_unavailable(method, analysis_date, recorder)
            VENDOR_METHODS[method] = dict.fromkeys(vendor_routes, unavailable)
        for method, vendor_routes in original_financial_routes.items():
            VENDOR_METHODS[method] = {
                vendor: _point_in_time_financial_route(
                    route,
                    method,
                    analysis_date,
                    recorder,
                    availability_resolver,
                )
                for vendor, route in vendor_routes.items()
            }
        fred._request = _point_in_time_fred_request(original_fred_request, analysis_date)
        VENDOR_METHODS["get_macro_indicators"] = {
            vendor: (
                _point_in_time_fred_route(route, analysis_date, recorder)
                if vendor == "fred"
                else _point_in_time_unavailable(
                    f"get_macro_indicators via {vendor}",
                    analysis_date,
                    recorder,
                )
            )
            for vendor, route in original_macro_routes.items()
        }
        for name in original_social:
            setattr(
                sentiment_analyst,
                name,
                _point_in_time_social_unavailable(name, analysis_date, recorder),
            )
        yield
    finally:
        for method, vendor_routes in original_routes.items():
            VENDOR_METHODS[method] = vendor_routes
        for method, vendor_routes in original_financial_routes.items():
            VENDOR_METHODS[method] = vendor_routes
        VENDOR_METHODS["get_macro_indicators"] = original_macro_routes
        fred._request = original_fred_request
        for name, fetcher in original_social.items():
            setattr(sentiment_analyst, name, fetcher)


def _point_in_time_unavailable(
    method: str,
    analysis_date: date,
    recorder: PointInTimeRecorder,
):
    def unavailable(*args, **kwargs) -> str:
        recorder.record(method, IntegrityStatus.SAFE, "current_snapshot_blocked")
        return (
            f"POINT_IN_TIME_DATA_UNAVAILABLE: {method} cannot guarantee data as of "
            f"{analysis_date.isoformat()}. Proceed without it and do not use current "
            "data, estimate, or fabricate values."
        )

    return unavailable


def _point_in_time_social_unavailable(
    name: str,
    analysis_date: date,
    recorder: PointInTimeRecorder,
):
    def unavailable(*args, **kwargs) -> str:
        recorder.record(name, IntegrityStatus.SAFE, "current_snapshot_blocked")
        return (
            f"<point-in-time unavailable: {name} cannot guarantee data as of "
            f"{analysis_date.isoformat()}; proceed without it and do not use current data>"
        )

    return unavailable


def _point_in_time_fred_request(request, analysis_date: date):
    @wraps(request)
    def vintage_request(path: str, params: dict) -> dict:
        guarded_params = dict(params)
        if path == "series/observations":
            guarded_params.update(
                {
                    "realtime_start": analysis_date.isoformat(),
                    "realtime_end": analysis_date.isoformat(),
                }
            )
        return request(path, guarded_params)

    return vintage_request


def _point_in_time_fred_route(
    route,
    analysis_date: date,
    recorder: PointInTimeRecorder,
):
    @wraps(route)
    def vintage_route(
        indicator: str,
        curr_date: str,
        look_back_days: int | None = None,
    ) -> str:
        report = route(indicator, analysis_date.isoformat(), look_back_days)
        recorder.record("get_macro_indicators", IntegrityStatus.SAFE, "fred_vintage_applied")
        return (
            "POINT_IN_TIME_VINTAGE: FRED observations are limited to values "
            f"available on {analysis_date.isoformat()}. Later releases and revisions "
            "are excluded.\n\n"
            f"{report}"
        )

    return vintage_route


def _point_in_time_financial_route(
    route,
    method: str,
    analysis_date: date,
    recorder: PointInTimeRecorder,
    availability_resolver,
):
    @wraps(route)
    def filtered_route(*args, **kwargs):
        ticker = kwargs.get("ticker") or (args[0] if args else None)
        if not isinstance(ticker, str) or not ticker.strip():
            recorder.record(method, IntegrityStatus.SAFE, "invalid_ticker_filtered")
            return json.dumps(
                {
                    "annualReports": [],
                    "quarterlyReports": [],
                    "integrityNotice": (
                        "Historical statement data was unavailable under point-in-time.v1."
                    ),
                },
                sort_keys=True,
            )
        payload = route(*args, **kwargs)
        filtered, findings = filter_statement_payload(
            payload,
            ticker=ticker,
            analysis_date=analysis_date,
            statement_kind=method,
            resolver=availability_resolver,
        )
        for finding in findings:
            recorder.record(
                finding.tool_name,
                finding.status,
                finding.reason_code,
                finding.details,
            )
        return filtered

    return filtered_route


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
