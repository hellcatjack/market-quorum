import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradingng_platform.models.base import Base, Timestamped, UuidPrimaryKey
from tradingng_platform.persistence.types import PORTABLE_DATETIME, PORTABLE_JSON


class Instrument(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("canonical_ticker", "asset_type"),)

    canonical_ticker: Mapped[str] = mapped_column(String(32), index=True)
    asset_type: Mapped[str] = mapped_column(String(32))
    exchange: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(255))
    metadata_json: Mapped[dict] = mapped_column(PORTABLE_JSON, default=dict)


class AssessmentBatch(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "assessment_batches"
    __table_args__ = (UniqueConstraint("submitted_by", "idempotency_key"),)

    submitted_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    defaults_json: Mapped[dict] = mapped_column(PORTABLE_JSON)


class AssessmentRequest(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "assessment_requests"

    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_batches.id"))
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instruments.id"))
    analysis_date: Mapped[date] = mapped_column(Date)
    requested_config_json: Mapped[dict] = mapped_column(PORTABLE_JSON)


class RunConfigSnapshot(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "run_config_snapshots"

    content_json: Mapped[dict] = mapped_column(PORTABLE_JSON)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    gateway_snapshot_id: Mapped[str | None] = mapped_column(String(128))


class AssessmentRun(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "assessment_runs"
    __table_args__ = (Index("ix_assessment_runs_claim", "status", "admitted_at", "id"),)

    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_requests.id"))
    attempt: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    config_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("run_config_snapshots.id")
    )
    retry_of_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assessment_runs.id"))
    clean_reassessment_of_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assessment_runs.id"),
        index=True,
    )
    version: Mapped[int] = mapped_column(default=1)
    admitted_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    started_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    finished_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text)


class RunEvent(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_runs.id"), index=True)
    sequence: Mapped[int]
    event_type: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(PORTABLE_JSON, default=dict)
