"""Add point-in-time integrity assessments and clean reassessment links."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.types import TypeEngine

revision: str = "20260727_0010"
down_revision: str | None = "20260727_0009"
branch_labels: str | None = None
depends_on: str | None = None


def datetime_type() -> TypeEngine:
    return sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def json_type() -> TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "assessment_runs",
        sa.Column("clean_reassessment_of_run_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_assessment_runs_clean_reassessment_of_run_id_assessment_runs"),
        "assessment_runs",
        "assessment_runs",
        ["clean_reassessment_of_run_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_assessment_runs_clean_reassessment_of_run_id"),
        "assessment_runs",
        ["clean_reassessment_of_run_id"],
        unique=False,
    )
    op.create_table(
        "run_integrity_assessments",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=True),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("audit_mode", sa.String(length=32), nullable=False),
        sa.Column("temporal_scope", sa.String(length=32), nullable=False),
        sa.Column("analysis_date", sa.Date(), nullable=False),
        sa.Column("checked_at", datetime_type(), nullable=False),
        sa.Column("reason_codes_json", json_type(), nullable=False),
        sa.Column("tool_findings_json", json_type(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", datetime_type(), nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["artifacts.id"],
            name=op.f("fk_run_integrity_assessments_artifact_id_artifacts"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assessment_runs.id"],
            name=op.f("fk_run_integrity_assessments_run_id_assessment_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_integrity_assessments")),
        sa.UniqueConstraint(
            "run_id",
            "policy_version",
            "input_fingerprint",
            name="uq_run_integrity_policy_input",
        ),
    )
    op.create_index(
        op.f("ix_run_integrity_assessments_run_id"),
        "run_integrity_assessments",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_run_integrity_assessments_checked_at"),
        "run_integrity_assessments",
        ["checked_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_run_integrity_assessments_input_fingerprint"),
        "run_integrity_assessments",
        ["input_fingerprint"],
        unique=False,
    )
    op.create_index(
        "ix_run_integrity_policy_status",
        "run_integrity_assessments",
        ["policy_version", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_run_integrity_policy_status", table_name="run_integrity_assessments")
    op.drop_index(
        op.f("ix_run_integrity_assessments_input_fingerprint"),
        table_name="run_integrity_assessments",
    )
    op.drop_index(
        op.f("ix_run_integrity_assessments_checked_at"),
        table_name="run_integrity_assessments",
    )
    op.drop_index(
        op.f("ix_run_integrity_assessments_run_id"),
        table_name="run_integrity_assessments",
    )
    op.drop_table("run_integrity_assessments")
    op.drop_index(
        op.f("ix_assessment_runs_clean_reassessment_of_run_id"),
        table_name="assessment_runs",
    )
    op.drop_constraint(
        op.f("fk_assessment_runs_clean_reassessment_of_run_id_assessment_runs"),
        "assessment_runs",
        type_="foreignkey",
    )
    op.drop_column("assessment_runs", "clean_reassessment_of_run_id")
