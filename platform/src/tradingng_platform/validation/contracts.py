import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
    price_target_status: Literal[
        "not_set", "basis_pending", "basis_unavailable", "evaluated"
    ] | None = None
    rebased_price_target: Decimal | None = None
    data_quality_status: Literal[
        "matched", "minor_difference", "material_difference", "not_available"
    ] | None = None


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
    calculation_version: Literal["validation.v1", "validation.v2"] = "validation.v1"
    calendar_code: str | None = None
    entry_session: date | None = None
    exit_session: date | None = None
    matures_at: datetime | None = None
    price_return: Decimal | None = None
    benchmark_price_return: Decimal | None = None
    price_alpha: Decimal | None = None
    total_return: Decimal | None = None
    benchmark_total_return: Decimal | None = None
    total_alpha: Decimal | None = None
    normalization_version: str | None = None
    provider_adapter_version: str | None = None
    provider_id: str | None = None

    @model_validator(mode="after")
    def populate_legacy_total_return_aliases(self):
        if self.total_return is None:
            self.total_return = self.raw_return
        if self.benchmark_total_return is None:
            self.benchmark_total_return = self.benchmark_return
        if self.total_alpha is None:
            self.total_alpha = self.alpha
        return self
