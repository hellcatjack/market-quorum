"""Add the persistent fast/slow model routing policy."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.types import TypeEngine

revision: str = "20260727_0009"
down_revision: str | None = "20260727_0008"
branch_labels: str | None = None
depends_on: str | None = None


def datetime_type() -> TypeEngine:
    return sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def json_type() -> TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "model_routing_policy",
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("content_json", json_type(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", datetime_type(), nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_model_routing_policy_updated_by_users"),
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_model_routing_policy")),
    )


def downgrade() -> None:
    op.drop_table("model_routing_policy")
