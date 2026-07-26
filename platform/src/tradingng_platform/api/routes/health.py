from pathlib import Path

import httpx
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request
from sqlalchemy import text

from tradingng_platform.api.errors import ApiError

health_router = APIRouter(prefix="/health", tags=["health"])


@health_router.get("/live", operation_id="health_liveness")
async def liveness() -> dict:
    return {"status": "ok"}


@health_router.get("/ready", operation_id="health_readiness")
async def readiness(request: Request) -> dict:
    unavailable = []
    try:
        async with request.app.state.database.sessions() as session:
            await session.execute(text("SELECT 1"))
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        root = Path(__file__).resolve().parents[5]
        expected = ScriptDirectory.from_config(
            Config(str(root / "platform" / "alembic.ini"))
        ).get_current_head()
        if revision != expected:
            unavailable.append("database_migration")
    except Exception:
        unavailable.append("database")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{str(request.app.state.settings.gateway_url).rstrip('/')}/healthz",
                timeout=3,
            )
            response.raise_for_status()
    except Exception:
        unavailable.append("gateway")

    if unavailable:
        raise ApiError(
            503,
            "not_ready",
            "One or more dependencies are unavailable",
            {"dependencies": sorted(set(unavailable))},
        )
    return {
        "status": "ok",
        "database": {"dialect": request.app.state.settings.database_dialect},
    }
