import json

import pytest

from tradingng_platform.records.llm_interactions import (
    LlmAuditFormatError,
    parse_llm_interactions,
)


def _record(**overrides):
    record = {
        "route": "fast",
        "model_alias": "codex-fast",
        "physical_model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "status": "completed",
        "started_at": "2026-07-27T17:26:20+00:00",
        "completed_at": "2026-07-27T17:26:24+00:00",
        "duration_ms": 4426,
        "error_code": None,
        "messages": [{"content": "private prompt"}],
        "response": [{"content": "private response"}],
        "token_usage": {"total_tokens": 123},
    }
    record.update(overrides)
    return record


def _line(record):
    return (json.dumps(record) + "\n").encode()


def test_projects_only_safe_model_metadata():
    page = parse_llm_interactions(_line(_record()), source="live")

    assert page.source == "live"
    assert page.complete is False
    assert page.items[0].sequence == 1
    assert page.items[0].physical_model == "gpt-5.6-terra"
    serialized = page.items[0].model_dump(mode="json")
    assert "messages" not in serialized
    assert "response" not in serialized
    assert "token_usage" not in serialized
    assert "private prompt" not in json.dumps(serialized)
    assert "private response" not in json.dumps(serialized)


def test_live_feed_ignores_only_a_partial_final_line():
    content = _line(_record()) + b'{"route":"slow"'

    page = parse_llm_interactions(content, source="live")

    assert len(page.items) == 1


def test_sealed_feed_rejects_malformed_json():
    with pytest.raises(LlmAuditFormatError, match="valid JSON"):
        parse_llm_interactions(_line(_record()) + b"not-json\n", source="sealed")


def test_feed_rejects_invalid_required_metadata():
    with pytest.raises(LlmAuditFormatError, match="invalid metadata"):
        parse_llm_interactions(_line(_record(started_at=None)), source="sealed")


def test_feed_enforces_size_and_record_limits(monkeypatch):
    import tradingng_platform.records.llm_interactions as module

    monkeypatch.setattr(module, "MAX_BYTES", 16)
    with pytest.raises(LlmAuditFormatError, match="size limit"):
        module.parse_llm_interactions(_line(_record()), source="sealed")

    monkeypatch.setattr(module, "MAX_BYTES", 32 * 1024 * 1024)
    monkeypatch.setattr(module, "MAX_RECORDS", 1)
    with pytest.raises(LlmAuditFormatError, match="record limit"):
        module.parse_llm_interactions(
            _line(_record()) + _line(_record(route="slow")),
            source="sealed",
        )
