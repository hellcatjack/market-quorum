import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradingng_platform.models.base import Base, Timestamped, UuidPrimaryKey
from tradingng_platform.persistence.types import PORTABLE_DATETIME, PORTABLE_JSON


class Webhook(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "webhooks"

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    endpoint: Mapped[str] = mapped_column(String(2048))
    event_types_json: Mapped[list[str]] = mapped_column(PORTABLE_JSON)
    encrypted_secret: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active")


class WebhookDelivery(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("webhook_id", "event_id"),)

    webhook_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhooks.id"), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("run_events.id"), index=True)
    attempt: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    response_code: Mapped[int | None]
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        PORTABLE_DATETIME,
        index=True,
    )
