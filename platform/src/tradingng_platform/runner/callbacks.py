import hashlib
import json
import os
import re
import threading
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from tradingng_platform.integrity.policy import (
    PointInTimeRecorder,
    evidence_temporal_metadata,
    record_observed_tool,
)

_SENSITIVE_KEY = re.compile(
    r"api[_-]?key|authorization|cookie|password|secret|token",
    re.IGNORECASE,
)
_SENSITIVE_TEXT_VALUE = re.compile(
    r"(?i)((?:[?&]|\b)(?:api[_-]?key|apikey|access_token|token|secret|password)=)"
    r"[^&\s)\]}>'\"]+"
)
_TOOL_CATEGORIES = {
    "get_stock_data": "core_stock_apis",
    "get_verified_market_snapshot": "core_stock_apis",
    "get_indicators": "technical_indicators",
    "get_fundamentals": "fundamental_data",
    "get_balance_sheet": "fundamental_data",
    "get_cashflow": "fundamental_data",
    "get_income_statement": "fundamental_data",
    "get_news": "news_data",
    "get_global_news": "news_data",
    "get_macro_indicators": "macro_data",
    "get_prediction_markets": "prediction_markets",
}


def redact(value: Any) -> Any:
    value = _jsonable(value)
    if isinstance(value, str):
        return _SENSITIVE_TEXT_VALUE.sub(r"\1[REDACTED]", value)
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (date, datetime, Decimal, Path)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "content") and hasattr(value, "type"):
        message = {
            "type": str(value.type),
            "content": _jsonable(value.content),
        }
        name = getattr(value, "name", None)
        if name:
            message["name"] = str(name)
        tool_calls = getattr(value, "tool_calls", None)
        if tool_calls:
            message["tool_calls"] = _jsonable(tool_calls)
        return message
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    return repr(value)


def _content_hash(value: Any) -> str:
    encoded = json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class AuditCallback(BaseCallbackHandler):
    def __init__(
        self,
        working_dir: Path,
        data_vendors: dict[str, str],
        tool_vendors: dict[str, str],
        model_routes: dict[str, dict[str, str]] | None = None,
        analysis_date: date | None = None,
        integrity_recorder: PointInTimeRecorder | None = None,
    ):
        self.working_dir = working_dir
        self.working_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_path = self.working_dir / "evidence.jsonl"
        self.llm_path = self.working_dir / "llm_interactions.jsonl"
        self.health_path = self.working_dir / "dependency_health.jsonl"
        self.data_vendors = data_vendors
        self.tool_vendors = tool_vendors
        self.model_routes = model_routes or {}
        self.analysis_date = analysis_date
        self.integrity_recorder = integrity_recorder
        self._tools: dict[Any, dict] = {}
        self._llm: dict[Any, dict] = {}
        self._lock = threading.Lock()

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: Any = None,
        inputs: Any = None,
        **kwargs,
    ) -> None:
        name = str(serialized.get("name") or serialized.get("id") or "unknown_tool")
        arguments = inputs if inputs is not None else _parse_json_or_text(input_str)
        self._tools[run_id] = {
            "tool_name": name,
            "arguments": arguments,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "started_monotonic": time.monotonic(),
        }

    def on_tool_end(self, output: Any, *, run_id: Any = None, **kwargs) -> None:
        pending = self._tools.pop(run_id, {"tool_name": "unknown_tool", "arguments": {}})
        tool_name = pending["tool_name"]
        category = _TOOL_CATEGORIES.get(tool_name)
        source = self.tool_vendors.get(tool_name)
        if source is None and category is not None:
            source = self.data_vendors.get(category)
        effective_at, freshness = evidence_temporal_metadata(
            tool_name,
            self.analysis_date,
            output,
        )
        record = {
            "tool_name": tool_name,
            "source": source or "default",
            "arguments": redact(pending["arguments"]),
            "output": redact(output),
            "output_sha256": _content_hash(output),
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "effective_at": effective_at,
            "freshness": freshness,
            "retention_class": "raw_180d",
        }
        self._append(self.evidence_path, record)
        record_observed_tool(self.integrity_recorder, tool_name, output)
        self._record_tool_health(tool_name, pending, healthy=True)

    def on_tool_error(self, error: BaseException, *, run_id: Any = None, **kwargs) -> None:
        pending = self._tools.pop(run_id, {"tool_name": "unknown_tool", "arguments": {}})
        tool_name = pending["tool_name"]
        error_type = type(error).__name__
        record = {
            "tool_name": tool_name,
            "source": self._tool_source(tool_name)[0],
            "arguments": redact(pending["arguments"]),
            "output": {"error_type": error_type},
            "output_sha256": _content_hash({"error_type": error_type}),
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "effective_at": None,
            "freshness": "collection_failed",
            "retention_class": "raw_180d",
        }
        self._append(self.evidence_path, record)
        self._record_tool_health(
            tool_name,
            pending,
            healthy=False,
            error_code=_dependency_error_code(error, "vendor"),
        )

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any = None,
        **kwargs,
    ) -> None:
        invocation_params = kwargs.get("invocation_params") or {}
        model_alias = invocation_params.get("model_name") or invocation_params.get("model")
        route_metadata = self.model_routes.get(str(model_alias), {})
        pending = {
            "model": serialized.get("name") or serialized.get("id") or "unknown_model",
            "messages": redact(messages),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "started_monotonic": time.monotonic(),
        }
        if route_metadata:
            pending.update(
                {
                    "route": route_metadata["route"],
                    "model_alias": str(model_alias),
                    "physical_model": route_metadata["model"],
                    "reasoning_effort": route_metadata["reasoning_effort"],
                }
            )
        self._llm[run_id] = pending

    def on_llm_end(self, response: Any, *, run_id: Any = None, **kwargs) -> None:
        pending = self._llm.pop(run_id, {})
        duration_ms = _elapsed_ms(pending)
        visible = []
        for generations in getattr(response, "generations", ()):
            for generation in generations:
                message = getattr(generation, "message", None)
                visible.append(_jsonable(message if message is not None else generation))
        usage = _jsonable(getattr(response, "llm_output", None) or {})
        record = {
            **_terminal_pending(pending),
            "status": "completed",
            "response": redact(visible),
            "response_sha256": _content_hash(visible),
            "token_usage": redact(usage),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": duration_ms,
            "retention_class": "raw_180d",
        }
        self._append(self.llm_path, record)
        self._record_health(
            {
                "scope": "gateway",
                "healthy": True,
                "latency_ms": duration_ms,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "error_code": None,
                "vendor": None,
                "category": None,
            }
        )

    def on_llm_error(self, error: BaseException, *, run_id: Any = None, **kwargs) -> None:
        pending = self._llm.pop(run_id, {})
        duration_ms = _elapsed_ms(pending)
        error_code = _dependency_error_code(error, "gateway")
        self._append(
            self.llm_path,
            {
                **_terminal_pending(pending),
                "status": "failed",
                "error_type": type(error).__name__,
                "error_code": error_code,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": duration_ms,
                "retention_class": "raw_180d",
            },
        )
        self._record_health(
            {
                "scope": "gateway",
                "healthy": False,
                "latency_ms": duration_ms,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "error_code": error_code,
                "vendor": None,
                "category": None,
            }
        )

    def _record_tool_health(
        self,
        tool_name: str,
        pending: dict,
        *,
        healthy: bool,
        error_code: str | None = None,
    ) -> None:
        source, category = self._tool_source(tool_name)
        self._record_health(
            {
                "scope": "vendor",
                "healthy": healthy,
                "latency_ms": _elapsed_ms(pending),
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "error_code": error_code,
                "vendor": source,
                "category": category,
            }
        )

    def _tool_source(self, tool_name: str) -> tuple[str, str]:
        category = _TOOL_CATEGORIES.get(tool_name, "unknown")
        source = self.tool_vendors.get(tool_name) or self.data_vendors.get(category)
        return source or "default", category

    def _record_health(self, record: dict) -> None:
        self._append(self.health_path, record)

    def _append(self, path: Path, record: dict) -> None:
        line = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with self._lock, path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def _parse_json_or_text(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _elapsed_ms(pending: dict) -> int:
    started = pending.get("started_monotonic")
    if not isinstance(started, (int, float)):
        return 0
    return max(0, int((time.monotonic() - started) * 1000))


def _terminal_pending(pending: dict) -> dict:
    return {key: value for key, value in pending.items() if key != "started_monotonic"}


def _dependency_error_code(error: BaseException, scope: str) -> str:
    error_type = type(error).__name__.lower()
    rate_limited = any(
        marker in error_type for marker in ("ratelimit", "toomanyrequests", "overload")
    )
    if scope == "gateway":
        return "gateway_overload" if rate_limited else "gateway_unavailable"
    return "vendor_rate_limit" if rate_limited else "vendor_unavailable"
