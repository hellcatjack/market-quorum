from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RESERVED_MODEL_IDS = frozenset({"codex", "codex-fast", "codex-slow"})


@dataclass(frozen=True)
class CodexModelOption:
    id: str
    default_reasoning_effort: str
    supported_reasoning_efforts: tuple[str, ...]


def _bounded_string(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        return None
    return normalized


def normalize_model_catalog(
    payload: dict[str, Any],
    *,
    max_models: int = 100,
) -> tuple[CodexModelOption, ...]:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows or len(rows) > max_models:
        raise ValueError("Codex model catalog envelope is invalid")

    options: list[CodexModelOption] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = _bounded_string(row.get("id"), 128)
        default = _bounded_string(row.get("defaultReasoningEffort"), 32)
        raw_efforts = row.get("supportedReasoningEfforts")
        if (
            model_id is None
            or model_id in RESERVED_MODEL_IDS
            or model_id in seen
            or default is None
            or not isinstance(raw_efforts, list)
        ):
            continue
        efforts: list[str] = []
        for item in raw_efforts:
            if not isinstance(item, dict):
                continue
            effort = _bounded_string(item.get("reasoningEffort"), 32)
            if effort is not None and effort not in efforts:
                efforts.append(effort)
        if not efforts or default not in efforts:
            continue
        seen.add(model_id)
        options.append(
            CodexModelOption(
                id=model_id,
                default_reasoning_effort=default,
                supported_reasoning_efforts=tuple(efforts),
            )
        )

    if not options:
        raise ValueError("Codex model catalog has no usable reasoning models")
    return tuple(options)
