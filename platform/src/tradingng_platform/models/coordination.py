from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tradingng_platform.models.base import Base


class CoordinationLock(Base):
    __tablename__ = "coordination_locks"

    lock_key: Mapped[str] = mapped_column(String(191), primary_key=True)
