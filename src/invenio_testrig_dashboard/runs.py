"""Shared run bookkeeping: identifying a run and recording that it failed.

Used by dispatch, by storing a report (``ingest``), and by the GitHub tick (``ingest_github``),
so none of them depends on another for these helpers.
"""

import logging
import uuid
from datetime import datetime
from typing import NamedTuple

import httpx2
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from . import alerts
from .models import PackageResult, Run, Target, TestResult
from .redact import redact
from .settings import Settings
from .status import RunStatus

MAX_ERROR_LENGTH = 2000

log = logging.getLogger(__name__)


class RunRef(NamedTuple):
    """Just what store_run()/run_prefix() read, so callers needn't load an ORM row."""

    id: int
    uid: uuid.UUID
    created_at: datetime


class RunUnusable(Exception):
    """This run can't produce results (missing/unsafe artifact, wrong report, vanished GitHub run).

    Raise it from any per-run operation; the caller records it once with mark_infra_error.
    """


def short_error(exc: BaseException) -> str:
    """One line for ``run.error``, logs, and alerts.

    SQLAlchemy errors embed the SQL and all parameters, so the driver's message (``exc.orig``)
    is used. httpx2 errors embed the request URL, which for artifact downloads is a *signed*
    storage URL, so they are reduced to the status code or exception type.
    """
    if isinstance(exc, httpx2.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx2.HTTPError):
        return type(exc).__name__
    original = getattr(exc, "orig", None) or exc
    text = str(original).strip()
    first_line = text.splitlines()[0] if text else ""
    return f"{type(original).__name__}: {first_line}"


def _make_storable(text: str) -> str:
    """Remove characters Postgres text cannot store (NUL, lone surrogates)."""
    return text.replace("\x00", "\ufffd").encode("utf-8", errors="replace").decode("utf-8")


def mark_infra_error(
    session_factory: sessionmaker[Session],
    run_id: int,
    message: str,
    settings: Settings,
    status: RunStatus = RunStatus.INFRA_ERROR,
    alert: bool = True,
) -> None:
    """Mark a run failed (``infra_error`` by default, or ``lost``) with a storable, redacted error.

    Drops what an earlier ingest derived from results (rows, counts, finished/ingested times),
    so a rejected run shows no stale results. Keeps what describes the attempt itself
    (GitHub run id/link, started_at): for a failed workflow, the GitHub run is where to debug.

    Also posts the ``run:<uid>`` ops alert (``alerts.run_failed_alert``) unless ``alert`` is
    False (``ingest-dir`` seeding); the alert never raises out of here.
    """
    error = redact(_make_storable(message), settings)[:MAX_ERROR_LENGTH]
    with session_factory() as session, session.begin():
        session.execute(delete(TestResult).where(TestResult.run_id == run_id))
        session.execute(delete(PackageResult).where(PackageResult.run_id == run_id))
        session.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(
                status=status,
                error=error,
                finished_at=None,
                ingested_at=None,
                packages_total=None,
                packages_failed=None,
                tests_total=None,
                tests_failed=None,
                tests_skipped=None,
            )
        )
        run = session.execute(
            select(Run.uid, Target.branch)
            .join(Target, Target.id == Run.target_id)
            .where(Run.id == run_id)
        ).one_or_none()

    if run is not None and alert:
        # One hook for every failure transition: dispatch, store_run, and lost runs all arrive here.
        # Alerting must never escape failure recording, or ingest would record a second error.
        try:
            alerts.run_failed_alert(session_factory, settings, run.uid, run.branch, status, error)
        except Exception:
            log.exception("run %s: the %s ops alert failed", run_id, status.value)
