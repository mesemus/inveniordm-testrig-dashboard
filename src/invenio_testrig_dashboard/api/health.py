"""Liveness/readiness probe (the "heartbeat"): 200 + the running version when the database
answers, 503 otherwise. Open without login, so it carries no data beyond the version."""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .. import __version__
from ..runs import short_error
from .deps import SessionDep

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])


class HealthOut(BaseModel):
    status: Literal["ok"]
    # pyproject.toml's version = the image tag (`make image`); shown in the UI footer.
    version: str


@router.get("/health")
@router.get("/health/", include_in_schema=False)
def health(session: SessionDep) -> HealthOut:
    try:
        session.execute(select(1))
    except SQLAlchemyError as exc:
        log.warning("health: database unavailable: %s", short_error(exc))
        raise HTTPException(503, "database unavailable") from exc
    return HealthOut(status="ok", version=__version__)
