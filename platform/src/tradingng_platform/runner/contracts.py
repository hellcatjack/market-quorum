import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class DependencyHealthEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["gateway", "vendor"]
    healthy: bool
    latency_ms: int = Field(ge=0)
    observed_at: datetime
    error_code: str | None = Field(default=None, max_length=64)
    vendor: str | None = Field(default=None, max_length=128)
    category: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def require_vendor_identity(self):
        if self.scope == "vendor" and (not self.vendor or not self.category):
            raise ValueError("vendor and category are required for vendor health events")
        return self


class RunnerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: uuid.UUID
    ticker: str = Field(min_length=1, max_length=32)
    asset_type: str = Field(min_length=1, max_length=32)
    analysis_date: date
    analysts: tuple[str, ...] = Field(min_length=1)
    debate_rounds: int = Field(ge=1, le=5)
    risk_rounds: int = Field(ge=1, le=5)
    language: str = Field(min_length=1, max_length=64)
    gateway_url: AnyHttpUrl
    codex_model: str = Field(min_length=1, max_length=128)
    codex_reasoning_effort: str = Field(min_length=1, max_length=32)
    work_dir: Path
    data_vendors: dict[str, str]
    tool_vendors: dict[str, str]


class RunnerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    type: Literal["stage", "tool", "artifact", "result", "error"]
    name: str = Field(min_length=1, max_length=128)
    payload: dict
    emitted_at: datetime
