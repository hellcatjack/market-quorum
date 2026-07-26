from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_body(body: bytes) -> Any:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return {"base64": base64.b64encode(body).decode("ascii")}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


def _audit_headers(headers: Mapping[str, str]) -> dict[str, str]:
    retained = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered in {"accept", "content-type", "user-agent"} or lowered.startswith(
            "x-stainless-"
        ):
            retained[lowered] = value
    return retained


def _body_record(body: bytes) -> dict[str, Any]:
    return {
        "body": _decode_body(body),
        "body_bytes": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
    }


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, separators=(",", ":"))
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


@dataclass(frozen=True)
class PendingExchange:
    sequence: int
    started_at: str
    started_monotonic: float
    request: dict[str, Any]


class AuditStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.exchanges_dir = self.root / "exchanges"
        self.exchanges_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.root / "exchanges.jsonl"
        existing = [
            int(path.name.split("-", 1)[0])
            for path in self.exchanges_dir.glob("*-request.json")
            if path.name.split("-", 1)[0].isdigit()
        ]
        self._next_sequence = max(existing, default=0) + 1
        self._sequence_lock = asyncio.Lock()
        self._append_lock = asyncio.Lock()

    async def begin(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> PendingExchange:
        async with self._sequence_lock:
            sequence = self._next_sequence
            self._next_sequence += 1
            started_at = _utc_now()
            request = {
                "sequence": sequence,
                "started_at": started_at,
                "method": method,
                "path": path,
                "headers": _audit_headers(headers),
                **_body_record(body),
            }
            request_path = self.exchanges_dir / f"{sequence:04d}-request.json"
            await asyncio.to_thread(_atomic_json, request_path, request)
        return PendingExchange(sequence, started_at, time.monotonic(), request)

    async def complete(
        self,
        pending: PendingExchange,
        *,
        status_code: int,
        headers: Mapping[str, str],
        body: bytes,
    ) -> None:
        completed_at = _utc_now()
        response = {
            "sequence": pending.sequence,
            "completed_at": completed_at,
            "status_code": status_code,
            "headers": _audit_headers(headers),
            **_body_record(body),
        }
        response_path = self.exchanges_dir / f"{pending.sequence:04d}-response.json"
        await asyncio.to_thread(_atomic_json, response_path, response)
        await self._append_terminal(
            {
                "sequence": pending.sequence,
                "started_at": pending.started_at,
                "completed_at": completed_at,
                "duration_ms": round((time.monotonic() - pending.started_monotonic) * 1000),
                "request": pending.request,
                "response": response,
            }
        )

    async def fail(self, pending: PendingExchange, *, error_type: str) -> None:
        completed_at = _utc_now()
        error = {"type": error_type}
        error_path = self.exchanges_dir / f"{pending.sequence:04d}-error.json"
        await asyncio.to_thread(_atomic_json, error_path, error)
        await self._append_terminal(
            {
                "sequence": pending.sequence,
                "started_at": pending.started_at,
                "completed_at": completed_at,
                "duration_ms": round((time.monotonic() - pending.started_monotonic) * 1000),
                "request": pending.request,
                "error": error,
            }
        )

    async def _append_terminal(self, value: dict[str, Any]) -> None:
        async with self._append_lock:
            await asyncio.to_thread(_append_jsonl, self.jsonl_path, value)
