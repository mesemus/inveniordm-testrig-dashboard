"""Request-scoped dependencies for the API.

The app puts the engine, session factory, and S3 clients on ``app.state`` in its lifespan,
so creating the app never reads environment variables itself (tests pass their own settings).
"""

from collections.abc import Iterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Run, Target
from ..settings import Settings


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_s3(request: Request) -> Any:
    return request.app.state.s3


def get_s3_presign(request: Request) -> Any:
    return request.app.state.s3_presign


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
S3Dep = Annotated[Any, Depends(get_s3)]
S3PresignDep = Annotated[Any, Depends(get_s3_presign)]


def run_or_404(session: Session, uid: UUID) -> Run:
    run = session.scalar(select(Run).where(Run.uid == uid))
    if run is None:
        raise HTTPException(404, "run not found")
    return run


def target_or_404(session: Session, branch: str) -> Target:
    target = session.scalar(select(Target).where(Target.branch == branch))
    if target is None:
        raise HTTPException(404, "target not found")
    return target
