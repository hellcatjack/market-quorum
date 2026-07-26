import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradingng_platform.models.base import Base, Timestamped, UuidPrimaryKey
from tradingng_platform.persistence.types import PORTABLE_DATETIME, PORTABLE_JSON


class Validation(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "validations"
    __table_args__ = (UniqueConstraint("run_id", "horizon"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_runs.id"), index=True)
    horizon: Mapped[int]
    status: Mapped[str] = mapped_column(String(32), default="scheduled", index=True)
    scheduled_for: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, index=True)
    observed_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    raw_return: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    benchmark_return: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    alpha: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    max_adverse_excursion: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    max_favorable_excursion: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    trigger_results_json: Mapped[dict] = mapped_column(PORTABLE_JSON, default=dict)
    data_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
