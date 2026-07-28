from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from tradingng_platform.config import Settings
from tradingng_platform.vendors.alpha_vantage import AlphaVantageRetryPolicy
from tradingng_platform.vendors.alpha_vantage_broker import AlphaVantageBroker
from tradingng_platform.vendors.alpha_vantage_client import (
    AlphaBrokerAuthenticationError,
    AlphaBrokerError,
    AlphaBrokerRateLimitError,
)


class BrokerQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    function: str = Field(pattern=r"^[A-Za-z0-9_]+$", min_length=1, max_length=64)
    params: dict = Field(default_factory=dict)
    run_id: str | None = Field(default=None, max_length=64)
    analysis_date: str | None = Field(default=None, max_length=32)


def create_app(
    *,
    settings: Settings | None = None,
    broker: AlphaVantageBroker | None = None,
) -> FastAPI:
    app_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = broker is None
        resolved = broker or _build_broker(app_settings)
        app.state.broker = resolved
        await resolved.start()
        try:
            yield
        finally:
            if owned:
                await resolved.close()

    app = FastAPI(title="TradingNG Alpha Vantage Broker", lifespan=lifespan)

    @app.exception_handler(AlphaBrokerError)
    async def broker_error_handler(request, error: AlphaBrokerError):
        if isinstance(error, AlphaBrokerAuthenticationError):
            status_code = 401
        elif isinstance(error, AlphaBrokerRateLimitError):
            status_code = 429
        else:
            status_code = 503
        return JSONResponse(
            status_code=status_code,
            content={"code": error.code, "message": str(error)},
        )

    @app.get("/health/ready", include_in_schema=False)
    async def ready():
        status = app.state.broker.status()
        return {"status": "ok" if status.status != "unavailable" else "unavailable"}

    @app.get("/v1/status")
    async def status():
        return app.state.broker.status().model_dump(mode="json")

    @app.post("/v1/query")
    async def query(
        command: BrokerQuery,
        consumer: str = Header(alias="X-TradingNG-Consumer", max_length=32),
    ):
        result = await app.state.broker.query(
            command.function,
            command.params,
            consumer,
            run_id=command.run_id,
            analysis_date=command.analysis_date,
        )
        return {"body": result.body, "cache_hit": result.cache_hit}

    return app


def _build_broker(settings: Settings) -> AlphaVantageBroker:
    keys = {}
    if settings.research_alpha_vantage_api_key is not None:
        keys["research"] = settings.research_alpha_vantage_api_key.get_secret_value()
    if settings.alpha_vantage_api_key is not None:
        keys["validation"] = settings.alpha_vantage_api_key.get_secret_value()
    return AlphaVantageBroker(
        keys,
        requests_per_minute=settings.alpha_vantage_requests_per_minute,
        utilization=settings.alpha_vantage_broker_utilization,
        max_in_flight=settings.alpha_vantage_broker_max_in_flight,
        retry_policy=AlphaVantageRetryPolicy(
            attempts=settings.alpha_vantage_retry_attempts,
            base_seconds=settings.alpha_vantage_retry_base_seconds,
            max_seconds=settings.alpha_vantage_retry_max_seconds,
        ),
        minute_cooldown_seconds=settings.alpha_vantage_broker_minute_cooldown_seconds,
        daily_cooldown_seconds=settings.alpha_vantage_broker_daily_cooldown_seconds,
        cache_dir=settings.alpha_vantage_cache_dir,
    )


def main() -> None:
    settings = Settings()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    uvicorn.run(
        create_app(settings=settings),
        host=settings.alpha_vantage_broker_host,
        port=settings.alpha_vantage_broker_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
