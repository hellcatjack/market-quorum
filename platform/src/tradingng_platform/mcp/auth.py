from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from tradingng_platform.mcp.context import reset_principal, set_principal

_BEARER = re.compile(r"^Bearer ([^\s,]+)$", re.IGNORECASE)
_MAX_BODY_BYTES = 1024 * 1024

AsgiApp = Callable[
    [dict[str, Any], Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]],
    Awaitable[None],
]


class McpSecurityMiddleware:
    """Authenticate MCP HTTP requests and reject browser-origin confusion."""

    def __init__(
        self,
        app: AsgiApp,
        *,
        verifier,
        allowed_origins: tuple[str, ...],
        resource_metadata_url: str,
    ):
        self.app = app
        self.verifier = verifier
        self.allowed_origins = frozenset(allowed_origins)
        self.resource_metadata_url = resource_metadata_url

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = _headers(scope)
        origin = headers.get("origin")
        if origin is not None and origin not in self.allowed_origins:
            await _response(send, 403, "MCP Origin is not allowed")
            return

        match = _BEARER.fullmatch(headers.get("authorization", ""))
        if match is None or match.group(1).startswith("tng_"):
            await self._unauthorized(send)
            return
        try:
            principal = await self.verifier.verify(match.group(1))
        except Exception:
            await self._unauthorized(send)
            return

        if scope.get("method") == "POST":
            content_type = headers.get("content-type", "").partition(";")[0].strip().lower()
            if content_type != "application/json":
                await _response(send, 415, "MCP requests require application/json")
                return
            try:
                body = await _read_body(receive, headers.get("content-length"))
            except ValueError:
                await _response(send, 413, "MCP request body is too large")
                return
            receive = _replay_body(body)

        token = set_principal(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_principal(token)

    async def _unauthorized(self, send) -> None:
        challenge = (
            f'Bearer resource_metadata="{self.resource_metadata_url}" scope="assessments:read"'
        )
        await _response(
            send,
            401,
            "MCP bearer token is required or invalid",
            [(b"www-authenticate", challenge.encode("ascii"))],
        )


def _headers(scope: dict[str, Any]) -> dict[str, str]:
    return {
        name.decode("latin-1").lower(): value.decode("latin-1")
        for name, value in scope.get("headers", ())
    }


async def _read_body(receive, raw_length: str | None) -> bytes:
    if raw_length is not None:
        try:
            if int(raw_length) > _MAX_BODY_BYTES:
                raise ValueError("body too large")
        except ValueError as error:
            raise ValueError("invalid or oversized body") from error
    body = bytearray()
    more = True
    while more:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        body.extend(message.get("body", b""))
        if len(body) > _MAX_BODY_BYTES:
            raise ValueError("body too large")
        more = message.get("more_body", False)
    return bytes(body)


def _replay_body(body: bytes):
    sent = False

    async def receive() -> dict:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


async def _response(send, status: int, message: str, headers=None) -> None:
    body = json.dumps({"error": message}, separators=(",", ":")).encode()
    response_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    response_headers.extend(headers or [])
    await send({"type": "http.response.start", "status": status, "headers": response_headers})
    await send({"type": "http.response.body", "body": body})
