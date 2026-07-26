"""retain validation price artifacts permanently

Revision ID: 20260726_0006
Revises: 20260725_0005
Create Date: 2026-07-26 16:27:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.types import TypeEngine

revision: str = "20260726_0006"
down_revision: str | None = "20260725_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VALIDATION_PRICE_KINDS = (
    "validation_1_prices",
    "validation_5_prices",
    "validation_20_prices",
)


def json_type() -> TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def datetime_type() -> TypeEngine:
    return sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    artifacts = sa.table(
        "artifacts",
        sa.column("kind", sa.String(length=64)),
        sa.column("retention_class", sa.String(length=32)),
    )
    op.execute(
        artifacts.update()
        .where(artifacts.c.kind.in_(_VALIDATION_PRICE_KINDS))
        .values(retention_class="permanent")
    )


def downgrade() -> None:
    artifacts = sa.table(
        "artifacts",
        sa.column("kind", sa.String(length=64)),
        sa.column("retention_class", sa.String(length=32)),
    )
    op.execute(
        artifacts.update()
        .where(artifacts.c.kind.in_(_VALIDATION_PRICE_KINDS))
        .values(retention_class="raw_180d")
    )
