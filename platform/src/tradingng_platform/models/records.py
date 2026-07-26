import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradingng_platform.models.base import Base, Timestamped, UuidPrimaryKey
from tradingng_platform.persistence.types import PORTABLE_DATETIME, PORTABLE_JSON


class Artifact(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("run_id", "kind", "sha256"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(128))
    size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    redacted: Mapped[bool] = mapped_column(default=True)
    retention_class: Mapped[str] = mapped_column(String(32), default="permanent", index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME, index=True)
    metadata_json: Mapped[dict] = mapped_column(PORTABLE_JSON, default=dict)


class AuditEvent(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "audit_events"

    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[str] = mapped_column(String(64))
    object_type: Mapped[str] = mapped_column(String(64))
    object_id: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    metadata_json: Mapped[dict] = mapped_column(PORTABLE_JSON, default=dict)
