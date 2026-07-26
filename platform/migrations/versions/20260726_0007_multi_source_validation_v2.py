"""multi-source validation v2 persistence

Revision ID: 20260726_0007
Revises: 20260726_0006
Create Date: 2026-07-26 21:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.types import TypeEngine

revision: str = "20260726_0007"
down_revision: str | None = "20260726_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def datetime_type() -> TypeEngine:
    return sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def json_type() -> TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "validations",
        sa.Column(
            "calculation_version",
            sa.String(length=32),
            nullable=False,
            server_default="validation.v1",
        ),
    )
    for name, length in (
        ("calendar_code", 32),
        ("worker_instance", 128),
        ("normalization_version", 32),
        ("provider_adapter_version", 64),
        ("provider_id", 32),
    ):
        op.add_column("validations", sa.Column(name, sa.String(length=length), nullable=True))
    for name in ("entry_session", "exit_session"):
        op.add_column("validations", sa.Column(name, sa.Date(), nullable=True))
    for name in ("matures_at", "claimed_at", "lease_expires_at"):
        op.add_column("validations", sa.Column(name, datetime_type(), nullable=True))
    for name in (
        "price_return",
        "benchmark_price_return",
        "price_alpha",
        "total_return",
        "benchmark_total_return",
        "total_alpha",
    ):
        op.add_column("validations", sa.Column(name, sa.Numeric(20, 10), nullable=True))
    op.create_index(
        op.f("ix_validations_calculation_version"),
        "validations",
        ["calculation_version"],
        unique=False,
    )
    op.create_index(
        op.f("ix_validations_matures_at"),
        "validations",
        ["matures_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_validations_lease_expires_at"),
        "validations",
        ["lease_expires_at"],
        unique=False,
    )

    op.create_table(
        "decision_price_bases",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("target_price", sa.Numeric(20, 6), nullable=False),
        sa.Column("reference_session", sa.Date(), nullable=True),
        sa.Column("reference_close", sa.Numeric(20, 10), nullable=True),
        sa.Column("target_multiple", sa.Numeric(20, 10), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("provider_id", sa.String(length=32), nullable=True),
        sa.Column("provider_adapter_version", sa.String(length=64), nullable=True),
        sa.Column("normalization_version", sa.String(length=32), nullable=True),
        sa.Column("collected_at", datetime_type(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", datetime_type(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("claimed_at", datetime_type(), nullable=True),
        sa.Column("lease_expires_at", datetime_type(), nullable=True),
        sa.Column("worker_instance", sa.String(length=128), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", datetime_type(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assessment_runs.id"],
            name=op.f("fk_decision_price_bases_run_id_assessment_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decision_price_bases")),
        sa.UniqueConstraint("run_id", name=op.f("uq_decision_price_bases_run_id")),
    )
    op.create_index(
        op.f("ix_decision_price_bases_run_id"),
        "decision_price_bases",
        ["run_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_decision_price_bases_status"),
        "decision_price_bases",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_decision_price_bases_next_attempt_at"),
        "decision_price_bases",
        ["next_attempt_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_decision_price_bases_lease_expires_at"),
        "decision_price_bases",
        ["lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_decision_price_bases_lease_expires_at"),
        table_name="decision_price_bases",
    )
    op.drop_index(
        op.f("ix_decision_price_bases_next_attempt_at"),
        table_name="decision_price_bases",
    )
    op.drop_index(op.f("ix_decision_price_bases_status"), table_name="decision_price_bases")
    op.drop_index(op.f("ix_decision_price_bases_run_id"), table_name="decision_price_bases")
    op.drop_table("decision_price_bases")
    op.drop_index(op.f("ix_validations_lease_expires_at"), table_name="validations")
    op.drop_index(op.f("ix_validations_matures_at"), table_name="validations")
    op.drop_index(op.f("ix_validations_calculation_version"), table_name="validations")
    for name in (
        "total_alpha",
        "benchmark_total_return",
        "total_return",
        "price_alpha",
        "benchmark_price_return",
        "price_return",
        "lease_expires_at",
        "claimed_at",
        "matures_at",
        "exit_session",
        "entry_session",
        "provider_id",
        "provider_adapter_version",
        "normalization_version",
        "worker_instance",
        "calendar_code",
        "calculation_version",
    ):
        op.drop_column("validations", name)
