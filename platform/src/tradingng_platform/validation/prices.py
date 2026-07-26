from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol

import pandas as pd
import yfinance as yf
from pydantic import BaseModel, model_validator


class PriceSeries(BaseModel):
    ticker: str
    currency: str | None
    sessions: list[date]
    open: list[Decimal]
    high: list[Decimal]
    low: list[Decimal]
    close: list[Decimal]
    adjusted_close: list[Decimal]
    source: str
    collected_at: datetime

    @model_validator(mode="after")
    def validate_series(self):
        lengths = {
            len(self.sessions),
            len(self.open),
            len(self.high),
            len(self.low),
            len(self.close),
            len(self.adjusted_close),
        }
        if len(lengths) != 1 or not self.sessions:
            raise ValueError("price arrays must be non-empty and equal length")
        if self.sessions != sorted(set(self.sessions)):
            raise ValueError("price sessions must be monotonic and unique")
        values = (*self.open, *self.high, *self.low, *self.close, *self.adjusted_close)
        if any(not value.is_finite() or value <= 0 for value in values):
            raise ValueError("prices must be finite and positive")
        return self


class PriceProvider(Protocol):
    async def history(self, ticker: str, start: date, end: date) -> PriceSeries: ...


class YFinancePriceProvider:
    async def history(self, ticker: str, start: date, end: date) -> PriceSeries:
        frame = await asyncio.to_thread(
            yf.download,
            ticker,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
            progress=False,
            actions=False,
            threads=False,
        )
        if frame.empty:
            raise ValueError("price provider returned no observations")
        return _frame_to_series(ticker, frame)


def _frame_to_series(ticker: str, frame: pd.DataFrame) -> PriceSeries:
    def column(name: str) -> pd.Series:
        values = frame[name]
        if isinstance(values, pd.DataFrame):
            if values.shape[1] != 1:
                raise ValueError("price provider returned ambiguous ticker columns")
            return values.iloc[:, 0]
        return values

    required = {
        "Open": column("Open"),
        "High": column("High"),
        "Low": column("Low"),
        "Close": column("Close"),
        "Adj Close": column("Adj Close"),
    }
    valid = pd.concat(required, axis=1).dropna()
    if valid.empty:
        raise ValueError("price provider returned no complete observations")
    return PriceSeries(
        ticker=ticker.upper(),
        currency=None,
        sessions=[timestamp.date() for timestamp in pd.to_datetime(valid.index)],
        open=[Decimal(str(value)) for value in valid["Open"]],
        high=[Decimal(str(value)) for value in valid["High"]],
        low=[Decimal(str(value)) for value in valid["Low"]],
        close=[Decimal(str(value)) for value in valid["Close"]],
        adjusted_close=[Decimal(str(value)) for value in valid["Adj Close"]],
        source="yfinance",
        collected_at=datetime.now(timezone.utc),
    )
