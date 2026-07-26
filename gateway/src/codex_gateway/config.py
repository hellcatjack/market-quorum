from __future__ import annotations

import os
import re
from dataclasses import dataclass

_VERSION_RE = re.compile(r"\bcodex-cli\s+(\d+)\.(\d+)\.(\d+)\b")


def parse_codex_version(output: str) -> tuple[int, int, int]:
    match = _VERSION_RE.search(output)
    if match is None:
        raise ValueError(f"Unable to parse Codex CLI version from {output!r}")
    return tuple(int(part) for part in match.groups())


def _positive_int(env_name: str, default: int, maximum: int | None = None) -> int:
    raw = os.getenv(env_name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be an integer") from exc
    if value <= 0 or (maximum is not None and value > maximum):
        raise ValueError(f"{env_name} is outside its valid range")
    return value


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    request_timeout_seconds: int = 600
    max_body_bytes: int = 2 * 1024 * 1024
    codex_bin: str = "codex"
    minimum_codex_version: tuple[int, int, int] = (0, 145, 0)
    verified_codex_version: tuple[int, int, int] = (0, 145, 0)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            port=_positive_int("CODEX_GATEWAY_PORT", 8000, 65535),
            request_timeout_seconds=_positive_int("CODEX_GATEWAY_REQUEST_TIMEOUT_SECONDS", 600),
        )
