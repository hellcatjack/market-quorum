import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, Field

from tradingng_platform.assessments.contracts import RunView


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
    asset_types: list[str]
    assessment_count: int
    latest_run_id: uuid.UUID | None
    latest_rating: str | None
    latest_created_at: datetime | None


class InstrumentHistoryItem(BaseModel):
    run: RunView
    rating: str | None
    executive_summary: str | None
    price_target: Decimal | None
    gateway_model: str | None
    gateway_reasoning_effort: str | None
    config_snapshot_sha256: str | None
    validation_outcome: str | None = None
