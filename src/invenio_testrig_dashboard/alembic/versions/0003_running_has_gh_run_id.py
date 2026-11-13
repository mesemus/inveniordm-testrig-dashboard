"""running runs always have a GitHub run id

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13 21:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Written by hand: autogenerate doesn't detect table-level CHECK constraints.
    op.create_check_constraint(
        op.f("ck_run_running_has_gh_run_id"),
        "run",
        "status <> 'running' OR gh_run_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_run_running_has_gh_run_id"), "run", type_="check")
