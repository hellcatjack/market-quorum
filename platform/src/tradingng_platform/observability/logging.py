from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

_SENSITIVE_KEY = re.compile(r"authorization|cookie|api[_-]?key|password|secret|token", re.I)
_CONTEXT_FIELDS = ("request_id", "run_id", "ticker", "actor", "event")


def _sensitive_environment_values() -> tuple[str, ...]:
    return tuple(
        value
        for key, value in os.environ.items()
        if value and len(value) >= 6 and _SENSITIVE_KEY.search(key)
    )


def redact(value, *, key: str = "", secrets: tuple[str, ...] | None = None):
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    known = secrets if secrets is not None else _sensitive_environment_values()
    if isinstance(value, dict):
        return {
            str(item_key): redact(item, key=str(item_key), secrets=known)
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets=known) for item in value]
    if isinstance(value, str):
        redacted = value
        for secret in known:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        details = getattr(record, "details", None)
        if details is not None:
            payload["details"] = details
        return json.dumps(redact(payload), sort_keys=True, separators=(",", ":"), default=str)


def configure_json_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
