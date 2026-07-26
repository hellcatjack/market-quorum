from __future__ import annotations

import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from codex_gateway.config import Settings
from codex_gateway.effective_config import EffectiveCodexConfig
from codex_gateway.errors import (
    CodexUnavailable,
    GatewayError,
    InvalidRequest,
    ModelNotFound,
    RequestTooLarge,
)
from codex_gateway.models import ChatCompletionRequest, GatewayStatus
from codex_gateway.request_adapter import build_codex_prompt
from codex_gateway.response_adapter import to_chat_completion
from codex_gateway.runtime import CodexRuntime

logger = logging.getLogger(__name__)
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        chunks = []
        size = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                error = RequestTooLarge(f"Request body exceeds {self.max_bytes} bytes")
                await JSONResponse(error.envelope(), status_code=error.status_code)(
                    scope, receive, send
                )
                return
            chunks.append(chunk)
            more = message.get("more_body", False)
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return {"type": "http.request", "body": b"", "more_body": False}
            replayed = True
            return {
                "type": "http.request",
                "body": b"".join(chunks),
                "more_body": False,
            }

        await self.app(scope, replay, send)


def create_app(*, runtime=None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    runtime = runtime or CodexRuntime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await runtime.start()
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="TradingNG Codex Gateway", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError):
        return JSONResponse(exc.envelope(), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        error = InvalidRequest(str(exc))
        return JSONResponse(error.envelope(), status_code=400)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        logger.error("unhandled_error=%s", type(exc).__name__)
        error = GatewayError("Internal server error")
        return JSONResponse(error.envelope(), status_code=error.status_code)

    @app.get("/healthz")
    async def health():
        if not runtime.ready:
            raise CodexUnavailable(runtime.health_detail)
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models():
        return {
            "object": "list",
            "data": [{"id": "codex", "object": "model", "owned_by": "local"}],
        }

    @app.get("/internal/status", response_model=GatewayStatus)
    async def internal_status():
        effective = (await runtime.effective_config()).require_complete()
        return GatewayStatus(
            status="ok",
            active_completions=runtime.active_completions,
            model=effective.model,
            reasoning_effort=effective.reasoning_effort,
            snapshot_id=effective.snapshot_id,
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(
        body: ChatCompletionRequest,
        tradingng_run_id: Annotated[str | None, Header(alias="X-TradingNG-Run-ID")] = None,
        codex_model: Annotated[
            str | None,
            Header(alias="X-TradingNG-Codex-Model"),
        ] = None,
        codex_reasoning_effort: Annotated[
            str | None,
            Header(alias="X-TradingNG-Codex-Reasoning-Effort"),
        ] = None,
    ):
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        try:
            if body.model != "codex":
                raise ModelNotFound(body.model)
            pin_values = (tradingng_run_id, codex_model, codex_reasoning_effort)
            pinned_config = None
            if any(value is not None for value in pin_values):
                if not all(value is not None and value.strip() for value in pin_values):
                    raise InvalidRequest("TradingNG run pin headers must be supplied together")
                if not _RUN_ID.fullmatch(tradingng_run_id):
                    raise InvalidRequest("X-TradingNG-Run-ID is invalid")
                if len(codex_model) > 128 or len(codex_reasoning_effort) > 32:
                    raise InvalidRequest("TradingNG Codex pin header is too long")
                pinned_config = EffectiveCodexConfig(
                    model=codex_model,
                    reasoning_effort=codex_reasoning_effort,
                ).require_complete()
                logger.info(
                    "request_id=%s tradingng_run_id=%s codex_snapshot_id=%s",
                    request_id,
                    tradingng_run_id,
                    pinned_config.snapshot_id,
                )
            adapted = build_codex_prompt(body)
            if pinned_config is None:
                result = await runtime.complete(adapted.text, adapted.output_schema)
            else:
                result = await runtime.complete(
                    adapted.text,
                    adapted.output_schema,
                    pinned_config=pinned_config,
                )
            response = to_chat_completion(body, adapted, result)
            logger.info(
                "request_id=%s status=200 duration_ms=%d tool_calls=%d total_tokens=%d",
                request_id,
                int((time.monotonic() - started) * 1000),
                len(response["choices"][0]["message"].get("tool_calls", [])),
                response["usage"]["total_tokens"],
            )
            return response
        except GatewayError as exc:
            logger.info(
                "request_id=%s status=%d duration_ms=%d code=%s",
                request_id,
                exc.status_code,
                int((time.monotonic() - started) * 1000),
                exc.code,
            )
            raise
        except Exception as exc:
            logger.error(
                "request_id=%s status=500 duration_ms=%d code=gateway_error error_type=%s",
                request_id,
                int((time.monotonic() - started) * 1000),
                type(exc).__name__,
            )
            raise GatewayError("Internal server error") from None

    return app
