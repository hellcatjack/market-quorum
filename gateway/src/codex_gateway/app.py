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
_RETRY_COUNT = re.compile(r"^[0-9]{1,3}$")
_INHERITED_MODEL = "codex"
_PRIVATE_ROUTE_MODELS = ("codex-fast", "codex-slow")
_MODEL_ALIASES = (_INHERITED_MODEL, *_PRIVATE_ROUTE_MODELS)


def _parse_retry_count(raw: str | None) -> int:
    if raw is None:
        return 0
    if not _RETRY_COUNT.fullmatch(raw):
        raise InvalidRequest("x-stainless-retry-count is invalid")
    value = int(raw)
    if value > 100:
        raise InvalidRequest("x-stainless-retry-count is invalid")
    return value


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
        physical = await runtime.available_models()
        return {
            "object": "list",
            "data": [
                {"id": "codex", "object": "model", "owned_by": "local"},
                *[
                    {
                        "id": item.id,
                        "object": "model",
                        "owned_by": "openai-codex",
                        "default_reasoning_effort": item.default_reasoning_effort,
                        "supported_reasoning_efforts": list(item.supported_reasoning_efforts),
                    }
                    for item in physical
                ],
            ],
        }

    @app.get("/internal/status", response_model=GatewayStatus)
    async def internal_status():
        effective = (await runtime.effective_config()).require_complete()
        activity = runtime.activity_snapshot()
        return GatewayStatus(
            status="ok",
            accepting=activity.accepting,
            active_completions=activity.active_completions,
            oldest_active_seconds=activity.oldest_active_seconds,
            stalest_progress_seconds=activity.stalest_progress_seconds,
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
        fast_codex_model: Annotated[
            str | None,
            Header(alias="X-TradingNG-Codex-Fast-Model"),
        ] = None,
        fast_codex_reasoning_effort: Annotated[
            str | None,
            Header(alias="X-TradingNG-Codex-Fast-Reasoning-Effort"),
        ] = None,
        slow_codex_model: Annotated[
            str | None,
            Header(alias="X-TradingNG-Codex-Slow-Model"),
        ] = None,
        slow_codex_reasoning_effort: Annotated[
            str | None,
            Header(alias="X-TradingNG-Codex-Slow-Reasoning-Effort"),
        ] = None,
        x_stainless_retry_count: Annotated[
            str | None,
            Header(alias="x-stainless-retry-count"),
        ] = None,
    ):
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        try:
            retry_count = _parse_retry_count(x_stainless_retry_count)
            pinned_config = None
            is_tradingng_pin = False
            route_values = (
                tradingng_run_id,
                fast_codex_model,
                fast_codex_reasoning_effort,
                slow_codex_model,
                slow_codex_reasoning_effort,
            )
            legacy_values = (tradingng_run_id, codex_model, codex_reasoning_effort)
            if body.model == _INHERITED_MODEL:
                if any(
                    value is not None
                    for value in (
                        fast_codex_model,
                        fast_codex_reasoning_effort,
                        slow_codex_model,
                        slow_codex_reasoning_effort,
                    )
                ):
                    raise InvalidRequest("TradingNG route pin headers require a route model alias")
                if any(value is not None for value in legacy_values):
                    if not all(value is not None and value.strip() for value in legacy_values):
                        raise InvalidRequest("TradingNG run pin headers must be supplied together")
                    pinned_config = EffectiveCodexConfig(
                        model=codex_model,
                        reasoning_effort=codex_reasoning_effort,
                    ).require_complete()
                    is_tradingng_pin = True
                elif body.reasoning_effort is not None:
                    raise InvalidRequest(
                        "reasoning_effort requires an explicit physical model",
                        param="reasoning_effort",
                    )
            elif body.model in _PRIVATE_ROUTE_MODELS:
                if codex_model is not None or codex_reasoning_effort is not None:
                    raise InvalidRequest("Legacy and route pin headers cannot be mixed")
                if not all(value is not None and value.strip() for value in route_values):
                    raise InvalidRequest("TradingNG route pin headers must be supplied together")
                if body.model == "codex-fast":
                    pinned_config = EffectiveCodexConfig(
                        model=fast_codex_model,
                        reasoning_effort=fast_codex_reasoning_effort,
                    ).require_complete()
                else:
                    pinned_config = EffectiveCodexConfig(
                        model=slow_codex_model,
                        reasoning_effort=slow_codex_reasoning_effort,
                    ).require_complete()
                is_tradingng_pin = True
            else:
                private_values = (
                    tradingng_run_id,
                    codex_model,
                    codex_reasoning_effort,
                    fast_codex_model,
                    fast_codex_reasoning_effort,
                    slow_codex_model,
                    slow_codex_reasoning_effort,
                )
                if any(value is not None for value in private_values):
                    raise InvalidRequest("TradingNG pin headers require a private model alias")
                catalog = await runtime.available_models()
                selected = next((item for item in catalog if item.id == body.model), None)
                if selected is None:
                    raise ModelNotFound(body.model)
                effort = (
                    selected.default_reasoning_effort
                    if body.reasoning_effort is None
                    else body.reasoning_effort
                )
                if effort not in selected.supported_reasoning_efforts:
                    raise InvalidRequest(
                        f"reasoning_effort {effort!r} is not supported by model {body.model!r}",
                        param="reasoning_effort",
                    )
                pinned_config = EffectiveCodexConfig(
                    model=selected.id,
                    reasoning_effort=effort,
                ).require_complete()

            if is_tradingng_pin:
                if tradingng_run_id is None or not _RUN_ID.fullmatch(tradingng_run_id):
                    raise InvalidRequest("X-TradingNG-Run-ID is invalid")
                pinned_values = (
                    fast_codex_model,
                    fast_codex_reasoning_effort,
                    slow_codex_model,
                    slow_codex_reasoning_effort,
                )
                if body.model == "codex":
                    pinned_values = (codex_model, codex_reasoning_effort)
                if any(
                    len(value) > (32 if "effort" in name else 128)
                    for name, value in zip(
                        (
                            "model",
                            "effort",
                            "model",
                            "effort",
                        ),
                        (value for value in pinned_values if value is not None),
                        strict=False,
                    )
                ):
                    raise InvalidRequest("TradingNG Codex pin header is too long")
                logger.info(
                    "request_id=%s tradingng_run_id=%s route=%s codex_snapshot_id=%s",
                    request_id,
                    tradingng_run_id,
                    body.model,
                    pinned_config.snapshot_id,
                )
            adapted = build_codex_prompt(body)
            result = await runtime.complete(
                adapted.text,
                adapted.output_schema,
                pinned_config=pinned_config,
                request_id=request_id,
                run_id=tradingng_run_id if is_tradingng_pin else None,
                retry_count=retry_count,
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
