"""Add the Stage 3 final-output run snapshot.

Revision ID: 20260904_02
Revises: 20260904_01
Create Date: 2026-09-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_02"
down_revision: str | Sequence[str] | None = "20260904_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add one nullable, object-validated JSONB final report."""

    op.add_column(
        "runs",
        sa.Column(
            "final_output",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_runs_final_output_object",
        "runs",
        "final_output IS NULL OR jsonb_typeof(final_output) = 'object'",
    )


def downgrade() -> None:
    """Remove only the Stage 3 final-output snapshot."""

    op.drop_constraint("ck_runs_final_output_object", "runs", type_="check")
    op.drop_column("runs", "final_output")
