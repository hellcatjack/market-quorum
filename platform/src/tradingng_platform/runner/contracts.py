import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

from tradingng_platform.memory import MemorySnapshot, empty_memory_snapshot


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
    fast_codex_model: str = Field(min_length=1, max_length=128)
    fast_codex_reasoning_effort: str = Field(min_length=1, max_length=32)
    slow_codex_model: str = Field(min_length=1, max_length=128)
    slow_codex_reasoning_effort: str = Field(min_length=1, max_length=32)
    work_dir: Path
    data_vendors: dict[str, str]
    tool_vendors: dict[str, str]
    alpha_vantage_broker_url: AnyHttpUrl | None = Field(
        default="http://127.0.0.1:8020",
        validate_default=True,
    )
    alpha_vantage_broker_request_timeout_seconds: float = Field(
        default=2100,
        ge=30,
        le=7200,
    )
    alpha_vantage_coordination_dir: Path | None = None
    alpha_vantage_requests_per_minute: int = Field(default=75, ge=1, le=10000)
    alpha_vantage_retry_attempts: int = Field(default=6, ge=1, le=20)
    alpha_vantage_retry_base_seconds: float = Field(default=5, gt=0, le=300)
    alpha_vantage_retry_max_seconds: float = Field(default=60, gt=0, le=900)
    sec_user_agent: str = Field(
        default="MarketQuorum/0.1 (+https://ushome.amycat.com)",
        min_length=1,
        max_length=512,
    )
    sec_request_timeout_seconds: float = Field(default=10, ge=1, le=60)
    sec_cache_dir: Path | None = None
    memory: MemorySnapshot = Field(default_factory=empty_memory_snapshot)

    @model_validator(mode="before")
    @classmethod
    def expand_legacy_single_model_route(cls, value):
        if not isinstance(value, dict):
            return value
        expanded = dict(value)
        expanded.setdefault("fast_codex_model", expanded.get("codex_model"))
        expanded.setdefault(
            "fast_codex_reasoning_effort",
            expanded.get("codex_reasoning_effort"),
        )
        expanded.setdefault("slow_codex_model", expanded.get("codex_model"))
        expanded.setdefault(
            "slow_codex_reasoning_effort",
            expanded.get("codex_reasoning_effort"),
        )
        return expanded

    @model_validator(mode="after")
    def validate_alpha_vantage_retry_window(self):
        if self.alpha_vantage_retry_base_seconds > self.alpha_vantage_retry_max_seconds:
            raise ValueError("Alpha Vantage retry base must not exceed its maximum")
        return self


class RunnerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    type: Literal["stage", "tool", "artifact", "result", "error"]
    name: str = Field(min_length=1, max_length=128)
    payload: dict
    emitted_at: datetime
