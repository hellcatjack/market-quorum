import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradingng_platform.models.base import Base, Timestamped, UuidPrimaryKey
from tradingng_platform.persistence.types import PORTABLE_DATETIME, PORTABLE_JSON


class Worker(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "workers"

    instance_name: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="idle")
    capabilities_json: Mapped[dict] = mapped_column(PORTABLE_JSON, default=dict)
    pid: Mapped[int]
    heartbeat_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME)


class WorkerLease(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "worker_leases"
    __table_args__ = (UniqueConstraint("run_id"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_runs.id"))
    worker_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workers.id"))
    child_pid: Mapped[int | None]
    child_pgid: Mapped[int | None]
    lease_expires_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, index=True)
    heartbeat_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME)


class RunStep(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("run_id", "name", "attempt"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_runs.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(default=1)
    started_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    finished_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    error_code: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)


class CircuitBreaker(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "circuit_breakers"

    name: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="closed")
    failure_count: Mapped[int] = mapped_column(default=0)
    opened_until: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    backoff_seconds: Mapped[int] = mapped_column(default=300)
    last_error_code: Mapped[str | None] = mapped_column(String(64))


class SchedulerPolicyRecord(Base):
    __tablename__ = "scheduler_policy"

    key: Mapped[str] = mapped_column(String(32), primary_key=True, default="default")
    content_json: Mapped[dict] = mapped_column(PORTABLE_JSON)
    version: Mapped[int] = mapped_column(default=1)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME)


class GatewayHealthSample(UuidPrimaryKey, Base):
    __tablename__ = "gateway_health_samples"

    sampled_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, index=True)
    healthy: Mapped[bool]
    latency_ms: Mapped[int]
    detail_json: Mapped[dict] = mapped_column(PORTABLE_JSON, default=dict)
    active_completions: Mapped[int]
    model: Mapped[str | None] = mapped_column(String(128))
    reasoning_effort: Mapped[str | None] = mapped_column(String(32))
    snapshot_id: Mapped[str | None] = mapped_column(String(64))


class VendorHealthSample(UuidPrimaryKey, Base):
    __tablename__ = "vendor_health_samples"

    sampled_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, index=True)
    healthy: Mapped[bool]
    latency_ms: Mapped[int]
    detail_json: Mapped[dict] = mapped_column(PORTABLE_JSON, default=dict)
    vendor: Mapped[str] = mapped_column(String(128), index=True)
    category: Mapped[str] = mapped_column(String(64))
