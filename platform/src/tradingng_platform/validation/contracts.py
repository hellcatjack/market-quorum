import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class ScheduleValidation(BaseModel):
    run_id: uuid.UUID
    horizons: list[Literal[1, 5, 20]] | None = Field(default=None, min_length=1, max_length=3)


class ValidationScheduleResult(BaseModel):
    items: list["ValidationView"]


class ValidationTriggerResults(BaseModel):
    rating: str | None = None
    direction: Literal["bullish", "bearish", "neutral"] | None = None
    direction_correct: bool | None = None
    price_target_hit: bool | None = None
    entry_price: Decimal | None = None
    exit_price: Decimal | None = None
    entry_session: date | None = None
    exit_session: date | None = None


class ValidationView(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    horizon: Literal[1, 5, 20]
    status: Literal[
        "scheduled",
        "running",
        "completed",
        "retry_wait",
        "unavailable",
        "failed",
    ]
    scheduled_for: datetime
    observed_at: datetime | None
    raw_return: Decimal | None
    benchmark_return: Decimal | None
    alpha: Decimal | None
    max_adverse_excursion: Decimal | None
    max_favorable_excursion: Decimal | None
    trigger_results: ValidationTriggerResults = Field(
        default_factory=ValidationTriggerResults,
        validation_alias="trigger_results_json",
    )
    data_artifact_id: uuid.UUID | None = None
    error_code: str | None = None
    calculation_version: Literal["validation.v1"] = "validation.v1"
