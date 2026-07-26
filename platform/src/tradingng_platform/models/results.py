import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradingng_platform.models.base import Base, Timestamped, UuidPrimaryKey
from tradingng_platform.persistence.types import PORTABLE_DATETIME, PORTABLE_JSON


class Decision(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "decisions"
    __table_args__ = (UniqueConstraint("run_id"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_runs.id"))
    rating: Mapped[str] = mapped_column(String(32))
    executive_summary: Mapped[str] = mapped_column(Text)
    investment_thesis: Mapped[str] = mapped_column(Text)
    price_target: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    time_horizon: Mapped[str | None] = mapped_column(String(128))
    structured_json: Mapped[dict] = mapped_column(PORTABLE_JSON)


class EvidenceItem(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "evidence_items"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_runs.id"), index=True)
    source: Mapped[str] = mapped_column(String(128))
    tool_name: Mapped[str] = mapped_column(String(128))
    arguments_json: Mapped[dict] = mapped_column(PORTABLE_JSON)
    collected_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME)
    effective_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    freshness: Mapped[str | None] = mapped_column(String(64))
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
