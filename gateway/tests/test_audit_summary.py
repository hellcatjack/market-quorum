import json

import pytest

from codex_gateway.audit_summary import summarize


def make_exchange(
    sequence,
    *,
    request_tools=(),
    response_tools=(),
    request_roles=("user",),
    tool_choice=None,
    structured_tools=(),
    started_second=None,
    completed_second=None,
):
    started_second = sequence if started_second is None else started_second
    completed_second = sequence + 1 if completed_second is None else completed_second
    request_body = {
        "model": "codex",
        "messages": [{"role": role, "content": "SPCX"} for role in request_roles],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": (
                        f"Structured output for {name}" if name in structured_tools else ""
                    ),
                    "parameters": {"type": "object"},
                },
            }
            for name in request_tools
        ],
    }
    if tool_choice is not None:
        request_body["tool_choice"] = tool_choice
    response_body = {
        "id": f"chatcmpl-{sequence}",
        "choices": [
            {
                "finish_reason": "tool_calls" if response_tools else "stop",
                "message": {
                    "role": "assistant",
                    "content": None if response_tools else "中文分析完成",
                    "tool_calls": [
                        {
                            "id": f"call_{index}",
                            "type": "function",
                            "function": {"name": name, "arguments": "{}"},
                        }
                        for index, name in enumerate(response_tools)
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }
    return {
        "sequence": sequence,
        "started_at": f"2026-07-24T00:00:{started_second:02d}+00:00",
        "completed_at": f"2026-07-24T00:00:{completed_second:02d}+00:00",
        "duration_ms": (completed_second - started_second) * 1000,
        "request": {
            "sequence": sequence,
            "started_at": f"2026-07-24T00:00:{started_second:02d}+00:00",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": {"content-type": "application/json"},
            "body": request_body,
            "body_bytes": 100,
            "body_sha256": f"request-hash-{sequence}",
        },
        "response": {
            "sequence": sequence,
            "completed_at": f"2026-07-24T00:00:{completed_second:02d}+00:00",
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body": response_body,
            "body_bytes": 80,
            "body_sha256": f"response-hash-{sequence}",
        },
    }


def write_audit(root, records):
    exchanges_dir = root / "exchanges"
    exchanges_dir.mkdir()
    with (root / "exchanges.jsonl").open("w", encoding="utf-8") as output:
        for record in records:
            sequence = record["sequence"]
            (exchanges_dir / f"{sequence:04d}-request.json").write_text(
                json.dumps(record["request"]), encoding="utf-8"
            )
            terminal = record.get("response") or record["error"]
            suffix = "response" if "response" in record else "error"
            (exchanges_dir / f"{sequence:04d}-{suffix}.json").write_text(
                json.dumps(terminal), encoding="utf-8"
            )
            output.write(json.dumps(record) + "\n")


def test_summary_reconciles_tools_and_writes_capability_evidence(tmp_path):
    records = [
        make_exchange(
            1,
            request_tools=("get_stock_data", "get_news"),
            response_tools=("get_stock_data", "get_news"),
            tool_choice="required",
            started_second=1,
            completed_second=4,
        ),
        make_exchange(
            2,
            request_roles=("system", "assistant", "tool", "user"),
            started_second=2,
            completed_second=3,
        ),
    ]
    write_audit(tmp_path, records)

    result = summarize(tmp_path)

    assert result["chat_requests"] == 2
    assert result["http_failures"] == 0
    assert result["max_overlapping_requests"] == 2
    assert result["total_tokens"] == 24
    matrix = (tmp_path / "capability_matrix.md").read_text()
    assert "Tool definition and selection" in matrix
    assert "#1" in matrix
    assert "Tool-result round trip" in matrix
    assert "#2" in matrix
    assert "Multiple/parallel tool calls" in matrix
    assert "not exercised" in matrix
    assert (tmp_path / "request_summary.csv").is_file()
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest == result


def test_summary_rejects_missing_terminal_record(tmp_path):
    exchanges_dir = tmp_path / "exchanges"
    exchanges_dir.mkdir()
    (exchanges_dir / "0001-request.json").write_text("{}", encoding="utf-8")
    (tmp_path / "exchanges.jsonl").write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="terminal record"):
        summarize(tmp_path)


def test_summary_rejects_noncontiguous_sequences(tmp_path):
    write_audit(tmp_path, [make_exchange(1), make_exchange(3)])

    with pytest.raises(ValueError, match="contiguous"):
        summarize(tmp_path)


def test_summary_detects_single_schema_structured_output_without_tool_choice(tmp_path):
    write_audit(
        tmp_path,
        [
            make_exchange(
                1,
                request_tools=("ResearchPlan",),
                response_tools=("ResearchPlan",),
                structured_tools=("ResearchPlan",),
            )
        ],
    )

    summarize(tmp_path)

    matrix = (tmp_path / "capability_matrix.md").read_text()
    assert (
        "| Structured output/tool choice | exercised successfully | #1: "
        "schema-constrained tool response |" in matrix
    )
