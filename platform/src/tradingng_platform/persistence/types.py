from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime
from sqlalchemy.dialects import mysql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "mysql":
            return dialect.type_descriptor(mysql.DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("database datetime values must include a timezone")
        normalized = value.astimezone(timezone.utc)
        if dialect.name == "mysql":
            return normalized.replace(tzinfo=None)
        return normalized

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


PORTABLE_JSON = JSON().with_variant(JSONB(), "postgresql")
PORTABLE_DATETIME = UtcDateTime()
