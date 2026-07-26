#!/usr/bin/env python3
from __future__ import annotations

import asyncio

from sqlalchemy import text
from tradingng_platform.config import Settings
from tradingng_platform.db import Database


async def check() -> None:
    database = Database(Settings())
    try:
        async with database.sessions() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=10)
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(check())
