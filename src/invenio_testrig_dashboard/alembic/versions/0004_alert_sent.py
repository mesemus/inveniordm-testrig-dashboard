"""alert_sent

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-14 07:53:44.190065

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_sent",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_alert_sent")),
    )
    op.add_column("run", sa.Column("alerts_done_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("run", "alerts_done_at")
    op.drop_table("alert_sent")
