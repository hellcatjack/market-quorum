import base64
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from tradingng_platform.domain.instruments import AssetType, canonicalize_ticker
from tradingng_platform.domain.runs import RunStatus


class Depth(str, Enum):
    SHALLOW = "shallow"
    MEDIUM = "medium"
    DEEP = "deep"


class MemoryMode(str, Enum):
    INDEPENDENT = "independent"
    HISTORICAL = "historical"


class AssessmentItem(BaseModel):
    ticker: str
    asset_type: AssetType | None = None
    analysis_date: date

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return canonicalize_ticker(value)


class SubmitAssessments(BaseModel):
    items: list[AssessmentItem] = Field(min_length=1, max_length=100)
    analysts: tuple[str, ...] = ("market", "social", "news", "fundamentals")
    depth: Depth = Depth.DEEP
    memory_mode: MemoryMode = MemoryMode.INDEPENDENT
    language: str = "Chinese"
    idempotency_key: str = Field(min_length=8, max_length=128)


class AdmissionSummaryView(BaseModel):
    running: int
    max_running: int
    queued: int
    oldest_queued_seconds: int | None
    admission: Literal["immediate", "queued", "paused"]
    reason: Literal["capacity_available", "capacity_busy", "temporarily_paused"]
    waiting_for_data: int = 0
    oldest_waiting_seconds: int | None = None


class RunView(BaseModel):
    id: uuid.UUID
    request_id: uuid.UUID
    ticker: str
    instrument_name: str | None = Field(
        default=None,
        description="SEC-verified official instrument name when available.",
    )
    exchange: str | None = None
    asset_type: str
    analysis_date: date
    status: RunStatus
    attempt: int
    created_at: datetime


class MemorySourceView(BaseModel):
    source_run_id: uuid.UUID
    validation_id: uuid.UUID
    analysis_date: date
    exit_session: date
    horizon: int
    rating: str
    raw_return: Decimal
    alpha: Decimal
    direction_correct: bool | None = None
    price_target_hit: bool | None = None
    content_sha256: str


class RunMemoryView(BaseModel):
    mode: MemoryMode = MemoryMode.INDEPENDENT
    snapshot_sha256: str | None = None
    sources: tuple[MemorySourceView, ...] = ()


class DataRequirementView(BaseModel):
    status: str
    required_products: tuple[str, ...] = ()
    progress: dict = Field(default_factory=dict)
    manifest_snapshot_id: str | None = None
    manifest_sha256: str | None = None
    next_poll_at: datetime | None = None


class RunDetailView(RunView):
    config_snapshot_sha256: str | None = None
    gateway_snapshot_id: str | None = None
    gateway_model: str | None = None
    gateway_reasoning_effort: str | None = None
    gateway_fast_model: str | None = None
    gateway_fast_reasoning_effort: str | None = None
    gateway_slow_model: str | None = None
    gateway_slow_reasoning_effort: str | None = None
    model_routing_snapshot_id: str | None = None
    root_commit: str | None = None
    tradingagents_commit: str | None = None
    prompt_schema_version: str | None = None
    request_config: dict = Field(default_factory=dict)
    resolved_config: dict = Field(default_factory=dict)
    data_vendors: dict[str, str] = Field(default_factory=dict)
    tool_vendors: dict[str, str] = Field(default_factory=dict)
    memory: RunMemoryView = Field(default_factory=RunMemoryView)
    data_requirement: DataRequirementView | None = None


class RunListFilters(BaseModel):
    ticker: str | None = None
    status: tuple[RunStatus, ...] = ()
    submitted_by: uuid.UUID | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=200)

    @field_validator("ticker")
    @classmethod
    def normalize_optional_ticker(cls, value: str | None) -> str | None:
        return canonicalize_ticker(value) if value else None

    @model_validator(mode="after")
    def validate_range(self):
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_from > self.created_to
        ):
            raise ValueError("created_from must not be after created_to")
        return self


class RunPage(BaseModel):
    items: list[RunView]
    next_cursor: str | None = None


class RunStepView(BaseModel):
    name: str
    status: str
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    summary: str | None


class RunEventView(BaseModel):
    sequence: int
    event_type: str
    payload: dict
    created_at: datetime


class RunEventPage(BaseModel):
    items: list[RunEventView]
    next_after: int | None = None


class ComparisonRequest(BaseModel):
    run_ids: list[uuid.UUID] = Field(min_length=2, max_length=10)

    @field_validator("run_ids")
    @classmethod
    def unique_runs(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(value)) != len(value):
            raise ValueError("comparison run IDs must be unique")
        return value


class ComparisonView(BaseModel):
    runs: list[RunView]
    ratings: dict[uuid.UUID, str | None]
    changed_sections: dict[str, list[uuid.UUID]]


def encode_run_cursor(created_at: datetime, run_id: uuid.UUID) -> str:
    raw = json.dumps(
        [created_at.isoformat(), str(run_id)],
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_run_cursor(value: str) -> tuple[datetime, uuid.UUID]:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(value + padding)
        created_at_value, run_id_value = json.loads(decoded)
        created_at = datetime.fromisoformat(created_at_value)
        if created_at.tzinfo is None:
            raise ValueError("cursor timestamp must include timezone")
        return created_at, uuid.UUID(run_id_value)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("invalid assessment cursor") from error
