from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class OhlcBasis(str, Enum):
    AS_TRADED = "as_traded"
    SPLIT_NORMALIZED = "split_normalized"
    UNKNOWN = "unknown"


class ProviderPriceSeries(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    provider_symbol: str = Field(min_length=1, max_length=64)
    provider_id: str = Field(min_length=1, max_length=32)
    provider_adapter_version: str = Field(min_length=1, max_length=64)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ohlc_basis: OhlcBasis
    capabilities: frozenset[str]
    currency: str | None = Field(default=None, max_length=16)
    timezone: str | None = Field(default=None, max_length=64)
    sessions: list[date]
    open: list[Decimal]
    high: list[Decimal]
    low: list[Decimal]
    close: list[Decimal]
    adjusted_close: list[Decimal] | None
    cash_distributions: list[Decimal]
    split_coefficient: list[Decimal]
    collected_at: datetime

    @model_validator(mode="after")
    def validate_series(self):
        arrays = (
            self.open,
            self.high,
            self.low,
            self.close,
            self.cash_distributions,
            self.split_coefficient,
        )
        lengths = {len(self.sessions), *(len(values) for values in arrays)}
        if self.adjusted_close is not None:
            lengths.add(len(self.adjusted_close))
        if len(lengths) != 1 or not self.sessions:
            raise ValueError("price and action arrays must be non-empty and equal length")
        if self.sessions != sorted(set(self.sessions)):
            raise ValueError("price sessions must be monotonic and unique")
        price_values = (*self.open, *self.high, *self.low, *self.close)
        if self.adjusted_close is not None:
            price_values += tuple(self.adjusted_close)
        if any(not value.is_finite() or value <= 0 for value in price_values):
            raise ValueError("prices must be finite and positive")
        if any(not value.is_finite() or value < 0 for value in self.cash_distributions):
            raise ValueError("cash distributions must be finite and non-negative")
        if any(not value.is_finite() or value <= 0 for value in self.split_coefficient):
            raise ValueError("split coefficients must be finite and positive")
        for open_, high, low, close in zip(
            self.open, self.high, self.low, self.close, strict=True
        ):
            if high < max(open_, close) or low > min(open_, close) or high < low:
                raise ValueError("OHLC values are inconsistent")
        return self


class CanonicalPriceSeries(BaseModel):
    ticker: str
    provider_symbol: str
    provider_id: str
    provider_adapter_version: str
    request_fingerprint: str
    normalization_version: Literal["prices.v1"] = "prices.v1"
    currency: str | None
    timezone: str | None
    sessions: list[date]
    open: list[Decimal]
    high: list[Decimal]
    low: list[Decimal]
    close: list[Decimal]
    cash_distributions: list[Decimal]
    price_index: list[Decimal]
    total_return_index: list[Decimal]
    data_quality_status: Literal[
        "matched", "minor_difference", "material_difference", "not_available"
    ]
    provider_total_return: Decimal | None
    collected_at: datetime

