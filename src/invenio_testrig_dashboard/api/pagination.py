"""Query/path parameter types (bounded, NUL-free) and the pagination helper."""

from typing import Annotated, Any

from fastapi import Path, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

# page * page_size becomes the OFFSET, so the bound also caps deep-offset cost.
PageSize = Annotated[int, Query(ge=1, le=100)]
PageNumber = Annotated[int, Query(ge=0, le=10_000)]
# Ids from URLs must fit a Postgres bigint; otherwise psycopg raises and the API 500s.
ResultId = Annotated[int, Path(gt=0, le=2**63 - 1)]

# User text that reaches Postgres: no NUL (psycopg rejects it, which used to be a 500).
NO_NUL = r"^[^\x00]*$"
Branch = Annotated[str, Path(max_length=200, pattern=NO_NUL)]
TargetParam = Annotated[str, Query(max_length=200, pattern=NO_NUL)]
FilterText = Annotated[str | None, Query(max_length=200, pattern=NO_NUL)]


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int


def count_rows[StmtT: Select](session: Session, stmt: StmtT) -> int:
    """The number of rows ``stmt`` would return (same builder as the page, one count query)."""
    return session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0


def paginate[StmtT: Select](
    session: Session, stmt: StmtT, page: int, page_size: int
) -> tuple[list[Any], int]:
    """One helper for every list endpoint: the total for a clean query, then one page."""
    total = count_rows(session, stmt)
    rows = session.execute(stmt.limit(page_size).offset(page * page_size)).all()
    return list(rows), total
