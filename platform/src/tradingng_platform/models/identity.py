import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradingng_platform.models.base import Base, Timestamped, UuidPrimaryKey
from tradingng_platform.persistence.types import PORTABLE_DATETIME, PORTABLE_JSON


class User(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("issuer", "subject"),)

    issuer: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(32), default="active")
    synced_at: Mapped[datetime] = mapped_column(
        PORTABLE_DATETIME,
        default=lambda: datetime.now(timezone.utc),
    )


class Role(UuidPrimaryKey, Base):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(32), unique=True)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"), primary_key=True)


class ApiCredential(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "api_credentials"

    principal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes_json: Mapped[list[str]] = mapped_column(PORTABLE_JSON)
    expires_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    last_used_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    revoked_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
