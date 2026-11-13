"""run progress

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-14 19:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Cosmetic values only (live progress): no constraints beyond the column types.
    op.add_column("run", sa.Column("progress_total", sa.Integer(), nullable=True))
    op.add_column("run", sa.Column("progress_done", sa.Integer(), nullable=True))
    op.add_column("run", sa.Column("progress_failed", postgresql.ARRAY(sa.Text()), nullable=True))
    op.add_column("run", sa.Column("progress_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("run", "progress_at")
    op.drop_column("run", "progress_failed")
    op.drop_column("run", "progress_done")
    op.drop_column("run", "progress_total")
