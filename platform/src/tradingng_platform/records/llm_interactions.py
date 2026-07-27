import json
from typing import Literal

from pydantic import ValidationError

from tradingng_platform.records.contracts import LlmInteractionPage, LlmInteractionView

MAX_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 2_000


class LlmAuditFormatError(ValueError):
    pass


def parse_llm_interactions(
    content: bytes,
    *,
    source: Literal["live", "sealed"],
) -> LlmInteractionPage:
    if len(content) > MAX_BYTES:
        raise LlmAuditFormatError("LLM audit exceeds the size limit")

    lines = content.splitlines()
    items: list[LlmInteractionView] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            partial_live_tail = (
                source == "live"
                and index == len(lines) - 1
                and not content.endswith((b"\n", b"\r"))
            )
            if partial_live_tail:
                break
            raise LlmAuditFormatError("LLM audit line is not valid JSON") from error
        if not isinstance(decoded, dict):
            raise LlmAuditFormatError("LLM audit line contains invalid metadata")
        if len(items) >= MAX_RECORDS:
            raise LlmAuditFormatError("LLM audit exceeds the record limit")
        try:
            items.append(
                LlmInteractionView.model_validate(
                    {
                        "sequence": len(items) + 1,
                        "route": decoded.get("route"),
                        "model_alias": decoded.get("model_alias"),
                        "physical_model": decoded.get("physical_model"),
                        "reasoning_effort": decoded.get("reasoning_effort"),
                        "status": decoded.get("status"),
                        "started_at": decoded.get("started_at"),
                        "completed_at": decoded.get("completed_at"),
                        "duration_ms": decoded.get("duration_ms"),
                        "error_code": decoded.get("error_code"),
                    }
                )
            )
        except ValidationError as error:
            raise LlmAuditFormatError("LLM audit line contains invalid metadata") from error

    return LlmInteractionPage(
        items=items,
        source=source,
        complete=source == "sealed",
    )
