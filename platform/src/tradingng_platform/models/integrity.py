import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradingng_platform.models.base import Base, Timestamped, UuidPrimaryKey
from tradingng_platform.persistence.types import PORTABLE_DATETIME, PORTABLE_JSON


class RunIntegrityAssessment(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "run_integrity_assessments"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "policy_version",
            "input_fingerprint",
            name="uq_run_integrity_policy_input",
        ),
        Index("ix_run_integrity_policy_status", "policy_version", "status"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_runs.id"), index=True)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    policy_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    audit_mode: Mapped[str] = mapped_column(String(32))
    temporal_scope: Mapped[str] = mapped_column(String(32))
    analysis_date: Mapped[date] = mapped_column(Date)
    checked_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, index=True)
    reason_codes_json: Mapped[list] = mapped_column(PORTABLE_JSON, default=list)
    tool_findings_json: Mapped[list] = mapped_column(PORTABLE_JSON, default=list)
    input_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
