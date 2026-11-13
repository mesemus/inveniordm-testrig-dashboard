"""The ingest tick: follow dispatched runs on GitHub and store their finished reports.

One tick (``ingest_tick``):

1. match queued runs to GitHub runs (by the ``gh_run_id`` testrig printed, else by title),
2. poll running runs, and for completed ones download ``final-report`` and ``store_run`` it,
3. summarise.

Per-run failure policy, applied in exactly one place (``_Tick._attempt``):
``GitHubAuthError`` stops the tick; transient errors (network, 5xx, rate limit, S3) leave the run
for the next tick; ``RunUnusable`` and any unexpected exception mark only that run ``infra_error``.
"""

import logging
import tempfile
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from pathlib import Path
from typing import Any, NamedTuple, TypeVar, cast
from uuid import UUID

import httpx2
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from . import alerts
from .archive import extract_archive, find_report_root
from .db import try_advisory_lock
from .github import (
    MAX_DOWNLOAD_BYTES,
    MAX_GH_RUN_ID,
    DownloadTooLarge,
    GitHub,
    GitHubAuthError,
    GitHubRateLimited,
)
from .ingest import store_run
from .models import Run
from .progress import progress_from_jobs
from .runs import RunRef, RunUnusable, mark_infra_error, short_error
from .settings import Settings
from .status import RunStatus

log = logging.getLogger(__name__)

INGEST_LOCK_KEY = 20261116
RESULTS_ARTIFACT = "final-report"

_TRANSIENT_ERRORS = (GitHubRateLimited, httpx2.HTTPError, BotoCoreError, ClientError)
T = TypeVar("T")


class _Outcome(IntEnum):
    MATCHED = 1
    COMPLETED = 2
    INFRA = 3
    LOST = 4


class _QueuedRun(NamedTuple):
    id: int
    uid: UUID
    gh_run_id: int | None
    dispatched_at: datetime | None
    created_at: datetime


class _RunningRun(NamedTuple):
    id: int
    uid: UUID
    gh_run_id: int  # never NULL for running runs (CHECK constraint, migration 0003)
    created_at: datetime


class IngestSummary(NamedTuple):
    active: int = 0
    matched: int = 0
    left: int = 0
    completed: int = 0
    infra_error: int = 0
    lost: int = 0
    busy: bool = False


def ingest_tick(
    engine: Engine,
    session_factory: sessionmaker[Session],
    s3: Any,
    settings: Settings,
    github: GitHub,
    now: datetime,
) -> IngestSummary:
    """Match queued runs, poll running ones, and store finished artifacts.

    Also owns ingest's ops alerts: ``github-auth`` (then re-raises ``GitHubAuthError``) and
    ``token-expiry`` (checked at the end of every tick, whatever happened).
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    with try_advisory_lock(engine, INGEST_LOCK_KEY) as locked:
        if not locked:
            # At a 2-minute cadence a skipped tick is expected (a big final-report can
            # outlast one interval), not a problem.
            log.info("ingest: another tick is running, skipping")
            return IngestSummary(busy=True)
        try:
            return _Tick(session_factory, s3, settings, github, now).run()
        except GitHubAuthError:
            _never_raise(alerts.github_auth_alert, session_factory, settings, clock=lambda: now)
            raise
        finally:
            _never_raise(
                alerts.check_token_expiry,
                session_factory,
                settings,
                github.token_expires_at,
                clock=lambda: now,
            )


def _never_raise(alert: Callable[..., None], *args: Any, **kwargs: Any) -> None:
    """Alerting must never replace the error (or result) the tick is returning."""
    try:
        alert(*args, **kwargs)
    except Exception:
        log.exception("ingest: %s failed", alert.__name__)


class _Tick:
    """One ingest tick; holds the context every per-run operation needs."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        s3: Any,
        settings: Settings,
        github: GitHub,
        now: datetime,
    ) -> None:
        self.session_factory = session_factory
        self.s3 = s3
        self.settings = settings
        self.github = github
        self.now = now
        self.counts: Counter[_Outcome] = Counter()

    def run(self) -> IngestSummary:
        queued = self._queued_runs()
        running_before = self._running_runs()
        active = len(queued) + len(running_before)
        if active:
            self._match_queued(queued)
            # Re-read: runs matched in this tick are polled right away.
            for run in self._running_runs():
                self._attempt(run.id, run.uid, self._poll, run)

        # A digest lost to a Discord outage or a crash is retried at the end of every tick.
        alerts.retry_pending(self.session_factory, self.settings, self.now)

        return IngestSummary(
            active=active,
            matched=self.counts[_Outcome.MATCHED],
            left=self._count_active(),
            completed=self.counts[_Outcome.COMPLETED],
            infra_error=self.counts[_Outcome.INFRA],
            lost=self.counts[_Outcome.LOST],
        )

    # -- the per-run failure policy --------------------------------------------------------

    def _attempt(self, run_id: int, uid: UUID, operation: Callable[..., Any], *args: Any) -> None:
        """Run one per-run operation, applying the failure policy (see the module docstring)."""
        try:
            outcome = operation(*args)
        except GitHubAuthError:
            raise
        except _TRANSIENT_ERRORS as exc:
            log.warning("run %s: transient error, retrying next tick: %s", uid, short_error(exc))
            return
        except RunUnusable as exc:
            log.warning("run %s: %s", uid, exc)
            self._fail(run_id, str(exc))
            return
        except Exception as exc:
            log.exception("run %s: ingest failed", uid)
            self._fail(run_id, short_error(exc))
            return
        if outcome is not None:
            self.counts[outcome] += 1

    def _fail(self, run_id: int, message: str, status: RunStatus = RunStatus.INFRA_ERROR) -> None:
        mark_infra_error(self.session_factory, run_id, message, self.settings, status=status)
        self.counts[_Outcome.LOST if status is RunStatus.LOST else _Outcome.INFRA] += 1

    def _github_call(self, call: Callable[[], T]) -> T:
        """A GitHub call about one run; a 404 means the run is gone, which nothing can fix."""
        try:
            return call()
        except httpx2.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise RunUnusable("GitHub run no longer exists") from exc
            raise

    # -- phase 1: queued runs --------------------------------------------------------------

    def _match_queued(self, queued: list[_QueuedRun]) -> None:
        unmatched = [run for run in queued if run.gh_run_id is None]
        listed = self._list_dispatch_runs(unmatched) if unmatched else []
        for run in queued:
            if run.gh_run_id is not None:
                self._attempt(run.id, run.uid, self._match_known, run, run.gh_run_id)
            elif listed is not None:
                self._attempt(run.id, run.uid, self._match_by_title, run, listed)
            # listed is None: GitHub was unreachable; don't declare runs lost because of that.

    def _list_dispatch_runs(self, unmatched: list[_QueuedRun]) -> list[dict] | None:
        oldest = min(run.dispatched_at or run.created_at for run in unmatched)
        try:
            return self.github.list_dispatch_runs(oldest - timedelta(minutes=10))
        except _TRANSIENT_ERRORS as exc:
            log.warning("ingest: could not list dispatch runs: %s", short_error(exc))
            return None

    def _match_known(self, run: _QueuedRun, gh_run_id: int) -> _Outcome:
        data = self._github_call(lambda: self.github.get_run(gh_run_id))
        self._mark_running(run.id, gh_run_id, data)
        return _Outcome.MATCHED

    def _match_by_title(self, run: _QueuedRun, listed: list[dict]) -> _Outcome | None:
        match = _title_match(listed, run.uid)
        if match is not None:
            self._mark_running(run.id, int(match["id"]), match)
            return _Outcome.MATCHED
        age = self.now - (run.dispatched_at or run.created_at)
        if age > timedelta(hours=self.settings.lost_after_hours):
            self._fail(run.id, "no GitHub run found", status=RunStatus.LOST)
        return None

    def _mark_running(self, run_id: int, gh_run_id: int, github_run: dict) -> None:
        values: dict[str, Any] = {
            "status": RunStatus.RUNNING,
            "gh_run_id": gh_run_id,
            # Built, never taken from the API (html_url would be one more untrusted value).
            "gh_run_url": f"https://github.com/{self.settings.runner_repository}/actions/runs/{gh_run_id}",
        }
        started_at = _parse_gh_time(github_run.get("run_started_at"))
        if started_at is not None:
            values["started_at"] = started_at
        with self.session_factory() as session, session.begin():
            session.execute(update(Run).where(Run.id == run_id).values(**values))

    # -- phase 2: running runs -------------------------------------------------------------

    def _poll(self, run: _RunningRun) -> _Outcome | None:
        data = self._github_call(lambda: self.github.get_run(run.gh_run_id))
        if data.get("status") != "completed":
            self._record_progress(run)
            return None
        # Process artifacts whatever the conclusion: "failure" can just mean tests failed.
        conclusion = str(data.get("conclusion") or "unknown")[:100]
        return self._store_report(run, conclusion)

    def _record_progress(self, run: _RunningRun) -> None:
        """Store the job-list progress of a running run.

        Progress is cosmetic: a failure here is logged and never fails or delays the run (only
        ``GitHubAuthError`` is re-raised, which is the tick's own policy). No artifact is
        downloaded; the job list is enough.
        """
        try:
            jobs = self._github_call(lambda: self.github.list_jobs(run.gh_run_id))
            progress = progress_from_jobs(jobs)
        except GitHubAuthError:
            raise
        except Exception as exc:
            log.warning("run %s: progress not updated: %s", run.uid, short_error(exc))
            return
        if progress is None:
            return  # testrig's prepare job has not created the matrix yet
        with self.session_factory() as session, session.begin():
            session.execute(
                update(Run)
                # Never touch a run that finished while we were reading the job list.
                .where(Run.id == run.id, Run.status == RunStatus.RUNNING)
                .values(
                    progress_total=progress.total,
                    progress_done=progress.done,
                    progress_failed=list(progress.failed),
                    progress_at=func.now(),
                )
            )

    def _store_report(self, run: _RunningRun, conclusion: str) -> _Outcome:
        no_results = f"no results artifacts (workflow conclusion: {conclusion})"
        artifacts = self._github_call(lambda: self.github.list_artifacts(run.gh_run_id))
        candidates = [
            artifact
            for artifact in artifacts
            if artifact.get("name") == RESULTS_ARTIFACT and not artifact.get("expired")
        ]
        if not candidates:
            raise RunUnusable(no_results)
        # A "Re-run all jobs" creates a second, newer final-report; artifact ids increase.
        final = max(candidates, key=_artifact_id)
        size = final.get("size_in_bytes")
        if isinstance(size, int) and size > MAX_DOWNLOAD_BYTES:
            raise RunUnusable(f"{RESULTS_ARTIFACT} is too large ({size} bytes)")

        with tempfile.TemporaryDirectory(prefix="itd-ingest-") as tmp:
            archive = Path(tmp) / "artifact.zip"
            extracted = Path(tmp) / "extracted"
            extracted.mkdir()
            try:
                self.github.download_artifact(final, archive)
            except DownloadTooLarge as exc:
                raise RunUnusable(str(exc)) from exc
            except httpx2.HTTPStatusError as exc:
                if exc.response.status_code in (404, 410):  # expired between listing and download
                    raise RunUnusable(no_results) from exc
                raise
            extract_archive(archive, extracted)
            root = find_report_root(extracted)
            if root is None:
                raise RunUnusable("no data.json in artifact")
            status = store_run(
                self.session_factory,
                self.s3,
                self.settings,
                RunRef(run.id, run.uid, run.created_at),
                root,
                expected_name=str(run.uid),
                alert=True,
            )
        # store_run records its own infra_error (and the reason), so only count it here.
        return _Outcome.COMPLETED if status is RunStatus.COMPLETED else _Outcome.INFRA

    # -- reads ------------------------------------------------------------------------------

    def _queued_runs(self) -> list[_QueuedRun]:
        with self.session_factory() as session:
            rows = session.execute(
                select(Run.id, Run.uid, Run.gh_run_id, Run.dispatched_at, Run.created_at)
                .where(Run.status == RunStatus.QUEUED)
                .order_by(Run.id)
            )
            return [_QueuedRun(*row) for row in rows]

    def _running_runs(self) -> list[_RunningRun]:
        with self.session_factory() as session:
            rows = session.execute(
                select(Run.id, Run.uid, Run.gh_run_id, Run.created_at)
                .where(Run.status == RunStatus.RUNNING)
                .order_by(Run.id)
            )
            # cast: the CHECK constraint (migration 0003) guarantees gh_run_id for running runs.
            return [
                _RunningRun(row.id, row.uid, cast(int, row.gh_run_id), row.created_at)
                for row in rows
            ]

    def _count_active(self) -> int:
        with self.session_factory() as session:
            count = session.scalar(
                select(func.count())
                .select_from(Run)
                .where(Run.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]))
            )
            return count or 0


def _title_match(listed: list[dict], uid: UUID) -> dict | None:
    """The listed GitHub run titled exactly ``uid`` (testrig's run-name), if any.

    Two runs can share our uid only if something dispatched twice; then follow the newest
    (GitHub run ids increase).
    """
    matches = [
        item
        for item in listed
        if item.get("display_title") == str(uid)
        and isinstance(item.get("id"), int)
        and 0 < item["id"] <= MAX_GH_RUN_ID
    ]
    return max(matches, key=lambda item: item["id"]) if matches else None


def _artifact_id(artifact: dict) -> int:
    value = artifact.get("id")
    return value if isinstance(value, int) else 0


def _parse_gh_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
