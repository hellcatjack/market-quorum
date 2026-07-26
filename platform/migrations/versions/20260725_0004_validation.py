"""validation and retention schema

Revision ID: 20260725_0004
Revises: 20260725_0003
Create Date: 2026-07-25 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.types import TypeEngine

revision: str = "20260725_0004"
down_revision: str | None = "20260725_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def json_type() -> TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def datetime_type() -> TypeEngine:
    return sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    bind = op.get_bind()
    empty_json_default = (
        sa.text("'{}'::jsonb")
        if bind.dialect.name == "postgresql"
        else sa.text("('{}')")
    )
    op.add_column(
        "artifacts",
        sa.Column(
            "retention_class",
            sa.String(length=32),
            nullable=False,
            server_default="permanent",
        ),
    )
    op.add_column(
        "artifacts",
        sa.Column("deleted_at", datetime_type(), nullable=True),
    )
    op.add_column(
        "artifacts",
        sa.Column(
            "metadata_json",
            json_type(),
            nullable=False,
            server_default=empty_json_default,
        ),
    )
    op.create_index(
        op.f("ix_artifacts_retention_class"),
        "artifacts",
        ["retention_class"],
        unique=False,
    )
    op.create_index(
        op.f("ix_artifacts_deleted_at"),
        "artifacts",
        ["deleted_at"],
        unique=False,
    )
    op.create_table(
        "validations",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("scheduled_for", datetime_type(), nullable=False),
        sa.Column("observed_at", datetime_type(), nullable=True),
        sa.Column("raw_return", sa.Numeric(20, 10), nullable=True),
        sa.Column("benchmark_return", sa.Numeric(20, 10), nullable=True),
        sa.Column("alpha", sa.Numeric(20, 10), nullable=True),
        sa.Column("max_adverse_excursion", sa.Numeric(20, 10), nullable=True),
        sa.Column("max_favorable_excursion", sa.Numeric(20, 10), nullable=True),
        sa.Column("trigger_results_json", json_type(), nullable=False),
        sa.Column("data_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", datetime_type(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", datetime_type(), nullable=False),
        sa.ForeignKeyConstraint(
            ["data_artifact_id"],
            ["artifacts.id"],
            name=op.f("fk_validations_data_artifact_id_artifacts"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assessment_runs.id"],
            name=op.f("fk_validations_run_id_assessment_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_validations")),
        sa.UniqueConstraint("run_id", "horizon", name=op.f("uq_validations_run_id")),
    )
    op.create_index(op.f("ix_validations_run_id"), "validations", ["run_id"], unique=False)
    op.create_index(op.f("ix_validations_status"), "validations", ["status"], unique=False)
    op.create_index(
        op.f("ix_validations_scheduled_for"),
        "validations",
        ["scheduled_for"],
        unique=False,
    )
    op.create_index(
        op.f("ix_validations_next_attempt_at"),
        "validations",
        ["next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_validations_next_attempt_at"), table_name="validations")
    op.drop_index(op.f("ix_validations_scheduled_for"), table_name="validations")
    op.drop_index(op.f("ix_validations_status"), table_name="validations")
    op.drop_index(op.f("ix_validations_run_id"), table_name="validations")
    op.drop_table("validations")
    op.drop_index(op.f("ix_artifacts_deleted_at"), table_name="artifacts")
    op.drop_index(op.f("ix_artifacts_retention_class"), table_name="artifacts")
    op.drop_column("artifacts", "metadata_json")
    op.drop_column("artifacts", "deleted_at")
    op.drop_column("artifacts", "retention_class")
