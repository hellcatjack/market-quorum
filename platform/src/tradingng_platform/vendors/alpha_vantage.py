from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AlphaResponseClassification = Literal["rate_limit", "authentication", "transient"]

_RATE_LIMIT_MARKERS = (
    "rate limit",
    "call frequency",
    "requests per minute",
    "requests per day",
    "too many requests",
)
_AUTHENTICATION_MARKERS = (
    "invalid api key",
    "missing api key",
    "api key is invalid",
    "apikey is invalid",
)
_NOTICE_KEYS = ("Information", "Note", "Error Message", "message", "error")


@dataclass(frozen=True)
class AlphaVantageRetryPolicy:
    attempts: int = 6
    base_seconds: float = 5
    max_seconds: float = 60

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("retry attempts must be positive")
        if self.base_seconds <= 0 or self.max_seconds <= 0:
            raise ValueError("retry delays must be positive")
        if self.base_seconds > self.max_seconds:
            raise ValueError("retry base must not exceed retry maximum")

    def delay(self, attempt: int, *, retry_after: float | None = None) -> float:
        if attempt < 1:
            raise ValueError("retry attempt must be positive")
        exponential = self.base_seconds * (2 ** (attempt - 1))
        requested = max(exponential, retry_after or 0)
        return min(requested, self.max_seconds)


class CrossProcessRateGate:
    """Reserve smooth request slots shared by every local process."""

    def __init__(
        self,
        state_path: Path,
        requests_per_minute: int,
        *,
        clock=time.time,
        sleep=time.sleep,
    ) -> None:
        if requests_per_minute < 1:
            raise ValueError("requests per minute must be positive")
        self.state_path = state_path
        self.lock_path = state_path.with_suffix(state_path.suffix + ".lock")
        self.interval_seconds = 60 / requests_per_minute
        self.clock = clock
        self.sleep = sleep

    def acquire(self) -> None:
        observed_now = self.clock()
        with self._locked_state() as state:
            reserved_at = max(observed_now, state)
            self._write_state(reserved_at + self.interval_seconds)
        delay = max(0.0, reserved_at - observed_now)
        if delay:
            self.sleep(delay)

    def defer(self, seconds: float) -> None:
        if seconds <= 0:
            return
        observed_now = self.clock()
        with self._locked_state() as state:
            self._write_state(max(state, observed_now + seconds))

    class _StateLock:
        def __init__(self, gate: CrossProcessRateGate):
            self.gate = gate
            self.stream = None

        def __enter__(self) -> float:
            self.gate.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.stream = self.gate.lock_path.open("a+")
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX)
            return self.gate._read_state()

        def __exit__(self, exc_type, exc, traceback) -> None:
            if self.stream is not None:
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
                self.stream.close()

    def _locked_state(self) -> CrossProcessRateGate._StateLock:
        return self._StateLock(self)

    def _read_state(self) -> float:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return float(value["next_allowed_at"])
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self.clock()

    def _write_state(self, next_allowed_at: float) -> None:
        temporary = self.state_path.with_name(
            f".{self.state_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps({"next_allowed_at": next_allowed_at}, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self.state_path)
        finally:
            temporary.unlink(missing_ok=True)


def classify_alpha_payload(payload: object) -> AlphaResponseClassification | None:
    if not isinstance(payload, dict):
        return None
    notices = [str(payload[key]) for key in _NOTICE_KEYS if payload.get(key)]
    if not notices:
        return None
    message = " ".join(notices).lower()
    if any(marker in message for marker in _RATE_LIMIT_MARKERS):
        return "rate_limit"
    if any(marker in message for marker in _AUTHENTICATION_MARKERS):
        return "authentication"
    return "transient"


def alpha_key_fingerprint(api_key: str) -> str:
    if not api_key:
        raise ValueError("Alpha Vantage API key is required")
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]
