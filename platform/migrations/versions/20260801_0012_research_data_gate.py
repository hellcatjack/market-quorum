"""Add per-assessment StockLean data readiness gates."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.types import TypeEngine

revision: str = "20260801_0012"
down_revision: str | None = "20260729_0011"
branch_labels: str | None = None
depends_on: str | None = None


def datetime_type() -> TypeEngine:
    return sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def json_type() -> TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "assessment_data_requirements",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("provider_request_id", sa.String(length=64), nullable=False),
        sa.Column("external_request_key", sa.String(length=128), nullable=False),
        sa.Column("required_products_json", json_type(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress_json", json_type(), nullable=False),
        sa.Column("manifest_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("next_poll_at", datetime_type(), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", datetime_type(), nullable=True),
        sa.Column("last_progress_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", datetime_type(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assessment_runs.id"],
            name=op.f("fk_assessment_data_requirements_run_id_assessment_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_data_requirements")),
        sa.UniqueConstraint("run_id", name="uq_assessment_data_requirements_run_id"),
    )
    op.create_index(
        op.f("ix_assessment_data_requirements_run_id"),
        "assessment_data_requirements",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assessment_data_requirements_status"),
        "assessment_data_requirements",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assessment_data_requirements_next_poll_at"),
        "assessment_data_requirements",
        ["next_poll_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assessment_data_requirements_manifest_snapshot_id"),
        "assessment_data_requirements",
        ["manifest_snapshot_id"],
        unique=False,
    )
    op.create_index(
        "ix_assessment_data_requirements_claim",
        "assessment_data_requirements",
        ["status", "next_poll_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assessment_data_requirements_claim",
        table_name="assessment_data_requirements",
    )
    op.drop_index(
        op.f("ix_assessment_data_requirements_manifest_snapshot_id"),
        table_name="assessment_data_requirements",
    )
    op.drop_index(
        op.f("ix_assessment_data_requirements_next_poll_at"),
        table_name="assessment_data_requirements",
    )
    op.drop_index(
        op.f("ix_assessment_data_requirements_status"),
        table_name="assessment_data_requirements",
    )
    op.drop_index(
        op.f("ix_assessment_data_requirements_run_id"),
        table_name="assessment_data_requirements",
    )
    op.drop_table("assessment_data_requirements")
