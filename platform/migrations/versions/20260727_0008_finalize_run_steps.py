"""Finalize stale run-step projections for terminal assessment runs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.types import TypeEngine

revision: str = "20260727_0008"
down_revision: str | None = "20260726_0007"
branch_labels: str | None = None
depends_on: str | None = None


def datetime_type() -> TypeEngine:
    return sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def json_type() -> TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    terminal_mappings = (
        ("succeeded", "completed", False),
        ("failed", "failed", True),
        ("cancelled", "cancelled", False),
    )
    for run_status, step_status, copy_error in terminal_mappings:
        error_assignment = (
            """
            error_code = COALESCE(
                run_steps.error_code,
                (SELECT assessment_runs.error_code
                 FROM assessment_runs
                 WHERE assessment_runs.id = run_steps.run_id)
            ),
        """
            if copy_error
            else ""
        )
        op.execute(
            sa.text(
                f"""
                UPDATE run_steps
                SET status = :step_status,
                    {error_assignment}
                    finished_at = COALESCE(
                        run_steps.finished_at,
                        (SELECT assessment_runs.finished_at
                         FROM assessment_runs
                         WHERE assessment_runs.id = run_steps.run_id)
                    )
                WHERE run_steps.status = 'running'
                  AND EXISTS (
                      SELECT 1
                      FROM assessment_runs
                      WHERE assessment_runs.id = run_steps.run_id
                        AND assessment_runs.status = :run_status
                  )
                """
            ).bindparams(step_status=step_status, run_status=run_status)
        )


def downgrade() -> None:
    # The original false-running projection cannot be reconstructed safely.
    pass
