from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EffectiveCodexConfig:
    model: str | None
    reasoning_effort: str | None

    @property
    def snapshot_id(self) -> str:
        payload = json.dumps(
            {"model": self.model, "reasoning_effort": self.reasoning_effort},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    def require_complete(self) -> EffectiveCodexConfig:
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("effective Codex model and reasoning effort are required")
        if not isinstance(self.reasoning_effort, str) or not self.reasoning_effort.strip():
            raise ValueError("effective Codex model and reasoning effort are required")
        return self

    @classmethod
    def from_read_response(cls, response: dict[str, Any]) -> EffectiveCodexConfig:
        config = response["config"]
        if not isinstance(config, dict):
            raise TypeError("config/read response config must be an object")
        model = _optional_nonempty_string(config.get("model"), "model")
        effort = _optional_nonempty_string(
            config.get("model_reasoning_effort"), "model_reasoning_effort"
        )
        return cls(model=model, reasoning_effort=effort)


def _optional_nonempty_string(value: Any, name: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise TypeError(f"effective Codex {name} must be a string or null")
    return value
