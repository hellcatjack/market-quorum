"""portable transaction coordination locks

Revision ID: 20260725_0005
Revises: 20260725_0004
Create Date: 2026-07-25 18:15:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_0005"
down_revision: str | None = "20260725_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GLOBAL_LOCKS = ("global:admission", "global:archive", "global:retention")


def upgrade() -> None:
    tickers = sorted(
        set(op.get_bind().execute(sa.text("SELECT canonical_ticker FROM instruments")).scalars())
    )
    op.create_table(
        "coordination_locks",
        sa.Column("lock_key", sa.String(length=191), nullable=False),
        sa.PrimaryKeyConstraint("lock_key", name=op.f("pk_coordination_locks")),
    )
    lock_table = sa.table("coordination_locks", sa.column("lock_key", sa.String(length=191)))
    op.bulk_insert(
        lock_table,
        [
            {"lock_key": lock_key}
            for lock_key in (*_GLOBAL_LOCKS, *(f"ticker:{ticker}" for ticker in tickers))
        ],
    )
    op.create_index(
        "ix_assessment_runs_claim",
        "assessment_runs",
        ["status", "admitted_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_assessment_runs_claim", table_name="assessment_runs")
    op.drop_table("coordination_locks")
