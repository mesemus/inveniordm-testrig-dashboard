"""test case key hash

Index test cases by a 32-byte SHA-256 key instead of (package, classname, name): real test
names reach ~10 KB, far beyond what a btree index entry can hold (2,704 bytes).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-13 18:09:38.733943

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same value as models.case_key(): sha256(package NUL classname NUL name), UTF-8.
# tests/test_db.py checks that both stay equal.
KEY_HASH_SQL = (
    "sha256(convert_to(package, 'UTF8') || '\\x00'::bytea"
    " || convert_to(classname, 'UTF8') || '\\x00'::bytea"
    " || convert_to(name, 'UTF8'))"
)


def upgrade() -> None:
    # Add nullable, backfill existing rows, then enforce NOT NULL (autogenerate can't do this).
    op.add_column("test_case", sa.Column("key_hash", sa.LargeBinary(), nullable=True))
    op.execute(f"UPDATE test_case SET key_hash = {KEY_HASH_SQL}")  # noqa: S608 (constant SQL)
    op.alter_column("test_case", "key_hash", nullable=False)
    op.create_check_constraint(
        op.f("ck_test_case_key_hash_length"), "test_case", "octet_length(key_hash) = 32"
    )
    op.drop_constraint(op.f("uq_test_case_package_classname_name"), "test_case", type_="unique")
    op.create_unique_constraint(op.f("uq_test_case_key_hash"), "test_case", ["key_hash"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_test_case_key_hash"), "test_case", type_="unique")
    op.create_unique_constraint(
        op.f("uq_test_case_package_classname_name"), "test_case", ["package", "classname", "name"]
    )
    op.drop_constraint(op.f("ck_test_case_key_hash_length"), "test_case", type_="check")
    op.drop_column("test_case", "key_hash")
