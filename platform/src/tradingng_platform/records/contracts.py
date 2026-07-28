import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from tradingng_platform.assessments.contracts import RunView
from tradingng_platform.domain.instruments import AssetType
from tradingng_platform.domain.runs import RunStatus


class DecisionView(BaseModel):
    run_id: uuid.UUID
    rating: str
    executive_summary: str
    investment_thesis: str
    price_target: Decimal | None
    time_horizon: str | None
    structured: dict


class EvidenceView(BaseModel):
    id: uuid.UUID
    source: str
    tool_name: str
    arguments: dict
    collected_at: datetime
    effective_at: datetime | None
    freshness: str | None
    content_hash: str


class LlmInteractionView(BaseModel):
    sequence: int = Field(ge=1)
    route: str | None
    model_alias: str | None
    physical_model: str | None
    reasoning_effort: str | None
    status: str
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: str | None


class LlmInteractionPage(BaseModel):
    items: list[LlmInteractionView]
    source: Literal["live", "sealed", "none"]
    complete: bool


class ArtifactView(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    kind: str
    media_type: str
    size: int
    sha256: str
    created_at: datetime


@dataclass(frozen=True)
class OpenedArtifact:
    id: uuid.UUID
    path: Path
    media_type: str
    filename: str
    sha256: str


class CreateReview(BaseModel):
    verdict: str = Field(min_length=2, max_length=32)
    comment: str = Field(min_length=1, max_length=10_000)


class ReviewView(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    reviewer: str
    verdict: str
    comment: str
    created_at: datetime


class CreateComment(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)


class CommentView(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    author: str
    body: str
    created_at: datetime


class InstrumentSummaryView(BaseModel):
    ticker: str
    name: str | None = Field(
        default=None,
        description="SEC-verified official instrument name when available.",
    )
    exchange: str | None = None
    asset_types: list[str]
    assessment_count: int
    latest_run_id: uuid.UUID | None
    latest_rating: str | None
    latest_created_at: datetime | None


class InstrumentIdentityView(BaseModel):
    id: uuid.UUID
    ticker: str
    name: str | None = Field(
        description="SEC-verified official instrument name when available.",
    )
    exchange: str | None
    asset_type: str


class InstrumentValidationView(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    horizon: int
    status: str
    scheduled_for: datetime
    matures_at: datetime | None
    exit_session: date | None
    total_return: Decimal | None
    total_alpha: Decimal | None
    direction_correct: bool | None
    price_target_hit: bool | None
    error_code: str | None


class InstrumentValidationStats(BaseModel):
    horizon: int
    completed: int
    direction_observed: int
    direction_correct: int
    accuracy: Decimal | None
    excluded_at_risk: int = 0
    excluded_unknown: int = 0


class InstrumentRunCounts(BaseModel):
    total: int = 0
    queued: int = 0
    active: int = 0
    succeeded: int = 0
    anomalous: int = 0


class InstrumentOverviewItem(BaseModel):
    instrument: InstrumentIdentityView
    latest_run: RunView
    latest_successful_run: RunView | None
    latest_decision: DecisionView | None
    previous_rating: str | None
    preferred_validation: InstrumentValidationView | None
    validation_stats: list[InstrumentValidationStats]
    run_counts: InstrumentRunCounts


class InstrumentOverviewFilters(BaseModel):
    query: str | None = None
    asset_type: AssetType | None = None
    statuses: tuple[RunStatus, ...] = ()
    anomalous_only: bool = False
    created_from: datetime | None = None
    created_to: datetime | None = None
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def validate_range(self):
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_from > self.created_to
        ):
            raise ValueError("created_from must not be after created_to")
        return self


class InstrumentOverviewPage(BaseModel):
    items: list[InstrumentOverviewItem]
    next_cursor: str | None = None
    instrument_count: int
    run_counts: InstrumentRunCounts
    validations_visible: bool = True


class InstrumentHistoryItem(BaseModel):
    run: RunView
    rating: str | None
    executive_summary: str | None
    price_target: Decimal | None
    gateway_model: str | None
    gateway_reasoning_effort: str | None
    gateway_fast_model: str | None = None
    gateway_fast_reasoning_effort: str | None = None
    gateway_slow_model: str | None = None
    gateway_slow_reasoning_effort: str | None = None
    config_snapshot_sha256: str | None
    validation_outcome: str | None = None
    validations: list[InstrumentValidationView] = Field(default_factory=list)
    memory_mode: str = "independent"
    memory_source_count: int = 0
    is_latest_attempt: bool = True
    request_attempt_count: int = 1
