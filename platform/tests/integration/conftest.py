import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tradingng_platform.domain.instruments import AssetType
from tradingng_platform.instruments.classification import InstrumentClassification
from tradingng_platform.models import Base


class StaticInstrumentClassifier:
    async def classify_many(self, tickers):
        classifications = {}
        for ticker in tickers:
            if ticker in {"GLD", "SPCX"}:
                asset_type = AssetType.FUND
                quote_type = "ETF"
            elif ticker.endswith("-USD"):
                asset_type = AssetType.CRYPTO
                quote_type = "CRYPTOCURRENCY"
            else:
                asset_type = AssetType.STOCK
                quote_type = "EQUITY"
            classifications[ticker] = InstrumentClassification(
                ticker=ticker,
                asset_type=asset_type,
                quote_type=quote_type,
                source="test",
                source_symbol=ticker,
                exchange="TEST",
                name=f"{ticker} test instrument",
            )
        return classifications


async def _truncate_tables(connection) -> None:
    if connection.dialect.name == "mysql":
        await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        try:
            preparer = connection.dialect.identifier_preparer
            for table in reversed(Base.metadata.sorted_tables):
                table_name = preparer.quote(table.name)
                await connection.execute(text(f"TRUNCATE TABLE {table_name}"))
        finally:
            await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        return

    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    await connection.execute(text(f"TRUNCATE TABLE {tables} CASCADE"))


@pytest.fixture
def test_database_url(monkeypatch):
    database_url = os.getenv("TRADINGNG_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TRADINGNG_TEST_DATABASE_URL is not configured")

    monkeypatch.setenv("TRADINGNG_DATABASE_URL", database_url)
    project_root = Path(__file__).resolve().parents[3]
    command.upgrade(Config(str(project_root / "platform/alembic.ini")), "head")
    return database_url


@pytest.fixture
async def session_factory(test_database_url):
    engine = create_async_engine(test_database_url, pool_pre_ping=True)
    async with engine.begin() as connection:
        await _truncate_tables(connection)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        async with engine.begin() as connection:
            await _truncate_tables(connection)
        await engine.dispose()


@pytest.fixture
def instrument_classifier():
    return StaticInstrumentClassifier()
