import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tradingng_platform.models.base import Base, Timestamped, UuidPrimaryKey


class Review(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "reviews"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_runs.id"), index=True)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    verdict: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(Text)


class Comment(UuidPrimaryKey, Timestamped, Base):
    __tablename__ = "comments"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_runs.id"), index=True)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
