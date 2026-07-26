import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
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
    calculation_version: Mapped[str] = mapped_column(
        String(32), default="validation.v1", server_default="validation.v1", index=True
    )
    calendar_code: Mapped[str | None] = mapped_column(String(32))
    entry_session: Mapped[date | None] = mapped_column(Date)
    exit_session: Mapped[date | None] = mapped_column(Date)
    matures_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    lease_expires_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME, index=True)
    worker_instance: Mapped[str | None] = mapped_column(String(128))
    price_return: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    benchmark_price_return: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    price_alpha: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    total_return: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    benchmark_total_return: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    total_alpha: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    normalization_version: Mapped[str | None] = mapped_column(String(32))
    provider_adapter_version: Mapped[str | None] = mapped_column(String(64))
    provider_id: Mapped[str | None] = mapped_column(String(32))


class DecisionPriceBasis(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "decision_price_bases"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assessment_runs.id"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    target_price: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    reference_session: Mapped[date | None] = mapped_column(Date)
    reference_close: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    target_multiple: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    currency: Mapped[str | None] = mapped_column(String(16))
    provider_id: Mapped[str | None] = mapped_column(String(32))
    provider_adapter_version: Mapped[str | None] = mapped_column(String(64))
    normalization_version: Mapped[str | None] = mapped_column(String(32))
    collected_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
