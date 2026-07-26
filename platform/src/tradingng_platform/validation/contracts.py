import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class ScheduleValidation(BaseModel):
    run_id: uuid.UUID
    horizons: list[Literal[1, 5, 20]] | None = Field(default=None, min_length=1, max_length=3)


class ValidationScheduleResult(BaseModel):
    items: list["ValidationView"]


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
