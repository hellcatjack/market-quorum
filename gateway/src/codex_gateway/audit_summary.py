from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def _write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def load_exchanges(audit_dir: Path) -> list[dict[str, Any]]:
    jsonl_path = Path(audit_dir) / "exchanges.jsonl"
    if not jsonl_path.is_file():
        return []
    records = []
    with jsonl_path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if line.strip():
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError(f"exchange line {line_number} is not an object")
                records.append(record)
    return records


def _sequence_from_path(path: Path) -> int:
    prefix = path.name.split("-", 1)[0]
    if not prefix.isdigit():
        raise ValueError(f"invalid exchange artifact name: {path.name}")
    return int(prefix)


def _validate_artifacts(audit_dir: Path, records: list[dict[str, Any]]) -> None:
    exchanges_dir = audit_dir / "exchanges"
    request_paths = sorted(exchanges_dir.glob("*-request.json"))
    request_sequences = {_sequence_from_path(path) for path in request_paths}
    terminal_sequences = {int(record.get("sequence", -1)) for record in records}
    if request_sequences != terminal_sequences or len(records) != len(terminal_sequences):
        raise ValueError("every request must have exactly one terminal record")
    expected = set(range(1, len(records) + 1))
    if terminal_sequences != expected:
        raise ValueError("exchange sequences must be contiguous from one")

    records_by_sequence = {int(record["sequence"]): record for record in records}
    for request_path in request_paths:
        sequence = _sequence_from_path(request_path)
        record = records_by_sequence[sequence]
        if _read_json(request_path) != record.get("request"):
            raise ValueError(f"request artifact does not match terminal record #{sequence}")
        terminal_kind = "response" if "response" in record else "error"
        terminal_path = exchanges_dir / f"{sequence:04d}-{terminal_kind}.json"
        if not terminal_path.is_file() or _read_json(terminal_path) != record.get(terminal_kind):
            raise ValueError(f"terminal artifact does not match record #{sequence}")


def _body(record: dict[str, Any], direction: str) -> dict[str, Any]:
    envelope = record.get(direction) or {}
    body = envelope.get("body") or {}
    return body if isinstance(body, dict) else {}


def _messages(request_body: dict[str, Any]) -> list[dict[str, Any]]:
    messages = request_body.get("messages") or []
    return [message for message in messages if isinstance(message, dict)]


def _tool_calls(response_body: dict[str, Any]) -> list[dict[str, Any]]:
    choices = response_body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return []
    message = choices[0].get("message") or {}
    calls = message.get("tool_calls") or []
    return [call for call in calls if isinstance(call, dict)]


def _finish_reason(response_body: dict[str, Any]) -> str:
    choices = response_body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    return str(choices[0].get("finish_reason") or "")


def _response_content(response_body: dict[str, Any]) -> Any:
    choices = response_body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message") or {}
    return message.get("content")


def _tool_name(tool: dict[str, Any]) -> str:
    function = tool.get("function") or {}
    return str(function.get("name") or "")


def _contains_chinese(value: Any) -> bool:
    if isinstance(value, str):
        return any("\u4e00" <= character <= "\u9fff" for character in value)
    if isinstance(value, list):
        return any(_contains_chinese(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_chinese(item) for item in value.values())
    return False


def _has_multimodal_content(request_body: dict[str, Any]) -> bool:
    for message in _messages(request_body):
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get("type") not in {"text", "input_text"}:
                    return True
    return False


def _parse_time(value: str) -> datetime:
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    return datetime.fromisoformat(value)


def _max_overlap(records: list[dict[str, Any]]) -> int:
    events = []
    for record in records:
        events.append((_parse_time(record["started_at"]), 1))
        events.append((_parse_time(record["completed_at"]), -1))
    active = 0
    maximum = 0
    for _timestamp, delta in sorted(events, key=lambda event: (event[0], event[1])):
        active += delta
        maximum = max(maximum, active)
    return maximum


def _sequence_evidence(
    chat_records: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]
) -> list[int]:
    return [int(record["sequence"]) for record in chat_records if predicate(record)]


def _evidence_text(sequences: list[int], detail: str) -> str:
    if not sequences:
        return detail
    identifiers = ", ".join(f"#{sequence}" for sequence in sequences)
    return f"{identifiers}: {detail}"


def _is_structured_exchange(record: dict[str, Any]) -> bool:
    request_body = _body(record, "request")
    response_calls = _tool_calls(_body(record, "response"))
    if not response_calls:
        return False
    if request_body.get("tool_choice") is not None:
        return True
    request_tools = request_body.get("tools") or []
    if len(request_tools) != 1:
        return False
    function = request_tools[0].get("function") or {}
    description = str(function.get("description") or "")
    schema_name = str(function.get("name") or "")
    return "structured" in description.lower() and all(
        _tool_name(call) == schema_name for call in response_calls
    )


def _capability_rows(
    chat_records: list[dict[str, Any]], max_overlap: int
) -> list[tuple[str, str, str]]:
    ordered = _sequence_evidence(
        chat_records, lambda record: len(_messages(_body(record, "request"))) > 1
    )
    long_context = _sequence_evidence(
        chat_records,
        lambda record: (
            len(_messages(_body(record, "request"))) >= 5
            or len(json.dumps(_body(record, "request"), ensure_ascii=False)) >= 20_000
        ),
    )
    tool_definitions = _sequence_evidence(
        chat_records, lambda record: bool(_body(record, "request").get("tools"))
    )
    multiple_tools = _sequence_evidence(
        chat_records, lambda record: len(_tool_calls(_body(record, "response"))) > 1
    )
    tool_results = _sequence_evidence(
        chat_records,
        lambda record: any(
            message.get("role") == "tool" for message in _messages(_body(record, "request"))
        ),
    )
    structured = _sequence_evidence(chat_records, _is_structured_exchange)
    chinese = _sequence_evidence(
        chat_records,
        lambda record: _contains_chinese(_response_content(_body(record, "response"))),
    )
    streaming = _sequence_evidence(
        chat_records, lambda record: _body(record, "request").get("stream") is True
    )
    multimodal = _sequence_evidence(
        chat_records, lambda record: _has_multimodal_content(_body(record, "request"))
    )

    exercised = "exercised successfully"
    not_exercised = "not exercised"
    return [
        (
            "Ordered chat roles",
            exercised if ordered else not_exercised,
            _evidence_text(ordered, "multi-message role order"),
        ),
        (
            "Long context",
            exercised if long_context else not_exercised,
            _evidence_text(long_context, "large request context"),
        ),
        (
            "Tool definition and selection",
            exercised if tool_definitions else not_exercised,
            _evidence_text(tool_definitions, "function schemas supplied"),
        ),
        (
            "Multiple/parallel tool calls",
            exercised if multiple_tools else not_exercised,
            _evidence_text(multiple_tools, "more than one returned tool call"),
        ),
        (
            "Tool-result round trip",
            exercised if tool_results else not_exercised,
            _evidence_text(tool_results, "request contains a tool role"),
        ),
        (
            "Structured output/tool choice",
            exercised if structured else not_exercised,
            _evidence_text(structured, "schema-constrained tool response"),
        ),
        (
            "Chinese output",
            exercised if chinese else not_exercised,
            _evidence_text(chinese, "assistant content contains Han characters"),
        ),
        (
            "Streaming",
            exercised if streaming else not_exercised,
            _evidence_text(streaming, "stream=true"),
        ),
        (
            "Multimodal content",
            exercised if multimodal else not_exercised,
            _evidence_text(multimodal, "non-text message content"),
        ),
        (
            "Overlapping concurrency",
            exercised if max_overlap > 1 else not_exercised,
            f"maximum in-flight requests: {max_overlap}",
        ),
        (
            "Persistent Codex threads",
            "unsupported by Gateway contract",
            "each completion uses an ephemeral Codex thread",
        ),
        ("Repository editing", not_exercised, "Gateway uses a temporary read-only workspace"),
        (
            "Codex network use",
            "not observable",
            "HTTP audit proves permission inputs/outputs, not internal network activity",
        ),
    ]


def _write_capability_matrix(
    audit_dir: Path, chat_records: list[dict[str, Any]], max_overlap: int
) -> None:
    rows = _capability_rows(chat_records, max_overlap)
    lines = [
        "# Codex Capability Matrix",
        "",
        "| Capability | Status | Evidence |",
        "| --- | --- | --- |",
    ]
    lines.extend(
        f"| {capability} | {status} | {evidence} |" for capability, status, evidence in rows
    )
    lines.append("")
    (audit_dir / "capability_matrix.md").write_text("\n".join(lines), encoding="utf-8")


def _summary_row(record: dict[str, Any]) -> dict[str, Any]:
    request = record["request"]
    request_body = _body(record, "request")
    response = record.get("response") or {}
    response_body = _body(record, "response")
    messages = _messages(request_body)
    request_tools = request_body.get("tools") or []
    response_tools = _tool_calls(response_body)
    usage = response_body.get("usage") or {}
    status = response.get("status_code", "")
    return {
        "sequence": record["sequence"],
        "method": request.get("method", ""),
        "path": request.get("path", ""),
        "status": status,
        "duration_ms": record.get("duration_ms", ""),
        "model": request_body.get("model", ""),
        "message_count": len(messages),
        "roles": ",".join(str(message.get("role") or "") for message in messages),
        "request_tool_count": len(request_tools),
        "request_tool_names": ",".join(_tool_name(tool) for tool in request_tools),
        "response_tool_call_count": len(response_tools),
        "response_tool_names": ",".join(_tool_name(tool) for tool in response_tools),
        "finish_reason": _finish_reason(response_body),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "request_bytes": request.get("body_bytes", 0),
        "response_bytes": response.get("body_bytes", 0),
        "error_type": (record.get("error") or {}).get("type", ""),
    }


def _write_csv(audit_dir: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with (audit_dir / "request_summary.csv").open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarize(audit_dir: Path) -> dict[str, Any]:
    audit_dir = Path(audit_dir)
    records = load_exchanges(audit_dir)
    _validate_artifacts(audit_dir, records)
    rows = [_summary_row(record) for record in records]
    _write_csv(audit_dir, rows)
    chat_records = [
        record
        for record in records
        if str(record["request"].get("path", "")).startswith("/v1/chat/completions")
    ]
    latencies = [int(record.get("duration_ms", 0)) for record in records]
    max_overlap = _max_overlap(records) if records else 0
    chat_rows = [rows[index] for index, record in enumerate(records) if record in chat_records]
    request_character_counts = [
        len(json.dumps(_body(record, "request"), ensure_ascii=False)) for record in chat_records
    ]
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_exchanges": len(records),
        "chat_requests": len(chat_records),
        "http_failures": sum(
            1
            for record in records
            if "error" in record or int((record.get("response") or {}).get("status_code", 0)) >= 400
        ),
        "minimum_latency_ms": min(latencies, default=0),
        "maximum_latency_ms": max(latencies, default=0),
        "mean_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0,
        "prompt_tokens": sum(int(row["prompt_tokens"] or 0) for row in chat_rows),
        "completion_tokens": sum(int(row["completion_tokens"] or 0) for row in chat_rows),
        "total_tokens": sum(int(row["total_tokens"] or 0) for row in chat_rows),
        "maximum_request_message_count": max(
            (len(_messages(_body(record, "request"))) for record in chat_records), default=0
        ),
        "maximum_request_character_count": max(request_character_counts, default=0),
        "max_overlapping_requests": max_overlap,
        "first_started_at": records[0]["started_at"] if records else None,
        "last_completed_at": records[-1]["completed_at"] if records else None,
    }
    _write_json(audit_dir / "manifest.json", manifest)
    _write_capability_matrix(audit_dir, chat_records, max_overlap)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize a Gateway HTTP audit")
    parser.add_argument("--audit-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.audit_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
