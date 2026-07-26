"""collaboration and webhook schema

Revision ID: 20260725_0003
Revises: 20260725_0002
Create Date: 2026-07-25 15:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.types import TypeEngine

revision: str = "20260725_0003"
down_revision: str | None = "20260725_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def json_type() -> TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def datetime_type() -> TypeEngine:
    return sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    op.create_table(
        "comments",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", datetime_type(), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_comments_author_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assessment_runs.id"],
            name=op.f("fk_comments_run_id_assessment_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comments")),
    )
    op.create_index(op.f("ix_comments_run_id"), "comments", ["run_id"], unique=False)
    op.create_table(
        "reviews",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", datetime_type(), nullable=False),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
            name=op.f("fk_reviews_reviewer_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assessment_runs.id"],
            name=op.f("fk_reviews_run_id_assessment_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
    )
    op.create_index(op.f("ix_reviews_run_id"), "reviews", ["run_id"], unique=False)
    op.create_table(
        "webhooks",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint", sa.String(length=2048), nullable=False),
        sa.Column("event_types_json", json_type(), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", datetime_type(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_webhooks_owner_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhooks")),
    )
    op.create_index(op.f("ix_webhooks_owner_id"), "webhooks", ["owner_id"], unique=False)
    op.create_table(
        "webhook_deliveries",
        sa.Column("webhook_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("next_attempt_at", datetime_type(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", datetime_type(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["run_events.id"],
            name=op.f("fk_webhook_deliveries_event_id_run_events"),
        ),
        sa.ForeignKeyConstraint(
            ["webhook_id"],
            ["webhooks.id"],
            name=op.f("fk_webhook_deliveries_webhook_id_webhooks"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_deliveries")),
        sa.UniqueConstraint(
            "webhook_id",
            "event_id",
            name=op.f("uq_webhook_deliveries_webhook_id"),
        ),
    )
    op.create_index(
        op.f("ix_webhook_deliveries_event_id"),
        "webhook_deliveries",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_webhook_deliveries_next_attempt_at"),
        "webhook_deliveries",
        ["next_attempt_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_webhook_deliveries_webhook_id"),
        "webhook_deliveries",
        ["webhook_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_webhook_deliveries_webhook_id"),
        table_name="webhook_deliveries",
    )
    op.drop_index(
        op.f("ix_webhook_deliveries_next_attempt_at"),
        table_name="webhook_deliveries",
    )
    op.drop_index(
        op.f("ix_webhook_deliveries_event_id"),
        table_name="webhook_deliveries",
    )
    op.drop_table("webhook_deliveries")
    op.drop_index(op.f("ix_webhooks_owner_id"), table_name="webhooks")
    op.drop_table("webhooks")
    op.drop_index(op.f("ix_reviews_run_id"), table_name="reviews")
    op.drop_table("reviews")
    op.drop_index(op.f("ix_comments_run_id"), table_name="comments")
    op.drop_table("comments")
