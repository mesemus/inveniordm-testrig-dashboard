"""Test-result notifications (one per run with failed tests) and ops alerts.

The link builders here are the single definition of the frontend routes used in messages
(keep them in sync with frontend/src/App.tsx). ``ops_alert`` deduplicates through the ``alert_sent`` table.

This module must never import ``runs``, ``ingest``, ``ingest_github``, ``dispatch``, or ``cli``:
they import it, so that would be a cycle (``tests/test_alerts.py`` enforces this). Callers
format exceptions themselves (``runs.short_error``) and pass text.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from urllib.parse import quote

from croniter import croniter
from pydantic import SecretStr
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from . import discord
from .github import TOKEN_EXPIRY_WARNING
from .history import completed_runs
from .models import AlertSent, Run, Target, TestCase, TestResult
from .redact import redact
from .settings import Settings
from .status import FAILING, RunStatus, TestStatus

log = logging.getLogger(__name__)

MAX_MESSAGE = 2000
# Leave room for the truncation line and never reach Discord's hard limit.
BUDGET = 1900
MAX_NAME = 120

# The ops-alert catalogue (the design's table, same order). Windows are dedupe windows:
# the same key is posted again once its window has passed.
RUN_FAILED_WINDOW = timedelta(days=365)  # run:<uid>, effectively once per run
GITHUB_AUTH_WINDOW = timedelta(hours=6)  # github-auth
TOKEN_EXPIRY_WINDOW = timedelta(hours=24)  # token-expiry
STALE_TARGET_WINDOW = timedelta(hours=24)  # stale:<branch>
CRASH_WINDOW = timedelta(hours=1)  # crash:<command>

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """The one clock helper (dispatch and alerting); tests inject their own ``Clock``."""
    return datetime.now(UTC)


# -- links (the frontend's routes) ---------------------------------------------------------------


def run_link(public_url: str, uid: uuid.UUID | str) -> str:
    return f"{public_url}/runs/{uid}"


def test_case_link(public_url: str, test_case_id: int, branch: str) -> str:
    # The history page needs the target: the first enabled one may be a different branch.
    return f"{public_url}/test-cases/{test_case_id}?target={quote(branch, safe='')}"


def compare_link(public_url: str, base_uid: uuid.UUID | str, head_uid: uuid.UUID | str) -> str:
    return f"{public_url}/compare?base={base_uid}&head={head_uid}"


# -- the digest -----------------------------------------------------------------------------


class _Line(NamedTuple):
    is_test: bool  # test lines count towards "… and N more"; section markers don't
    text: str


class ChangedTest(NamedTuple):
    id: int
    package: str
    classname: str
    name: str


def _display(text: str) -> str:
    """One-line, backtick-free, bounded text for a Discord message (names are untrusted)."""
    return text.replace("`", "'").replace("\r", " ").replace("\n", " ")[:MAX_NAME]


def _test_line(test: ChangedTest, public_url: str, branch: str) -> str:
    name = f"{test.classname}::{test.name}" if test.classname else test.name
    return (
        f" - `{_display(test.package)}` › `{_display(name)}` — "
        f"[history]({test_case_link(public_url, test.id, branch)})"
    )


def build_digest(
    branch: str,
    run_uid: uuid.UUID,
    new: list[ChangedTest],
    still: list[ChangedTest],
    fixed: list[ChangedTest],
    base_uid: uuid.UUID | None,
    public_url: str,
) -> str:
    """One message per run, truncated to fit Discord's 2000-character limit.

    ``new``/``still``: the run's failed tests, split by whether they also failed in the
    previous completed run (``base_uid``); ``fixed``: failed there, pass now.
    """
    failed = len(new) + len(still)
    if failed:
        header = f"**[{_display(branch)}]** {failed} failed test(s)"
        if base_uid is not None:
            header += f", {len(new)} new"
    else:
        header = f"**[{_display(branch)}]** all tests pass again, {len(fixed)} fixed"
    header += f" — {run_link(public_url, run_uid)}"
    compare = f"[compare]({compare_link(public_url, base_uid, run_uid)})" if base_uid else ""

    entries: list[_Line] = []
    for marker, tests in (("**New:**", new), ("**Still failing:**", still), ("**Fixed:**", fixed)):
        if not tests:
            continue
        entries.append(_Line(False, marker))
        entries.extend(_Line(True, _test_line(test, public_url, branch)) for test in tests)

    total = failed + len(fixed)
    newlines = 2 if compare else 1
    base_budget = BUDGET - len(header) - len(compare) - newlines
    body, added = _fill(entries, base_budget)
    if added < total:
        # Reserve room for the truncation line, so it is never silently dropped.
        reserve = len(f"… and {total} more — {run_link(public_url, run_uid)}") + 1
        body, added = _fill(entries, base_budget - reserve)
        body.append(f"… and {total - added} more — {run_link(public_url, run_uid)}")

    text = "\n".join([header, *body, *([compare] if compare else [])])
    if len(text) <= MAX_MESSAGE:
        return text
    # Pathological (an extremely long public_url): keep the compare link whole.
    room = MAX_MESSAGE - len(compare) - 1
    if not compare:
        return header[:MAX_MESSAGE]
    return header[:room] + "\n" + compare if room > 0 else compare[:MAX_MESSAGE]


def _fill(entries: list[_Line], budget: int) -> tuple[list[str], int]:
    """Add entry lines while they fit; returns the lines and how many test lines were added."""
    body: list[str] = []
    added = 0
    for entry in entries:
        if len(entry.text) + 1 > budget:  # + 1 for its newline
            break
        body.append(entry.text)
        budget -= len(entry.text) + 1
        if entry.is_test:
            added += 1
    return body, added


# -- the per-run rule (since 2026-11-15) ----------------------------------------------------


def _flag(run_id: int, statuses: tuple[int, ...], label: str):
    return func.coalesce(
        func.bool_or(and_(TestResult.run_id == run_id, TestResult.status.in_(statuses))), False
    ).label(label)


def _classified(
    session: Session, r0: int, r1: int | None
) -> tuple[list[ChangedTest], list[ChangedTest], list[ChangedTest]]:
    """One query: the run's failed tests (new / still failing vs. the previous run) and the
    tests that failed in the previous run and pass now."""
    passed = (int(TestStatus.PASSED),)
    failing = tuple(int(status) for status in FAILING)
    f0 = _flag(r0, failing, "f0")
    # No previous run: compare against an id no run can have, so f1 is false.
    f1 = _flag(r1 if r1 is not None else -1, failing, "f1")
    p0 = _flag(r0, passed, "p0")

    rows = session.execute(
        select(TestCase.id, TestCase.package, TestCase.classname, TestCase.name, f0, f1, p0)
        .join(TestResult, TestResult.test_case_id == TestCase.id)
        .where(TestResult.run_id.in_([run for run in (r0, r1) if run is not None]))
        .group_by(TestCase.id)
        .having(or_(f0, and_(f1, p0)))
        .order_by(TestCase.package, TestCase.classname, TestCase.name)
    ).all()

    def tests(predicate) -> list[ChangedTest]:
        return [
            ChangedTest(row.id, row.package, row.classname, row.name)
            for row in rows
            if predicate(row)
        ]

    new = tests(lambda row: row.f0 and not row.f1)
    still = tests(lambda row: row.f0 and row.f1)
    fixed = tests(lambda row: row.p0 and row.f1 and not row.f0)
    return new, still, fixed


def mark_alerts_done(
    session_factory: sessionmaker[Session], run_id: int, when: datetime | None = None
) -> None:
    """Record that this run's digest was handled (posted, nothing to post, or deliberately skipped)."""
    when = when or utc_now()
    with session_factory() as session, session.begin():
        session.execute(
            update(Run)
            .where(Run.id == run_id, Run.alerts_done_at.is_(None))
            .values(alerts_done_at=when)
        )


def alert_completed_run(
    session_factory: sessionmaker[Session],
    settings: Settings,
    run_id: int,
    clock: Clock | None = None,
) -> None:
    """Post the run's notification, once.

    Every completed run with a failed test posts; a run without failed tests posts only when
    tests of the previous completed run were fixed ("all tests pass again"). "Previous" is the
    target's completed run just before this one; infra_error/lost runs don't count.
    Nothing to report (or no webhook configured) still marks ``alerts_done_at``; a failed
    post leaves it NULL, so the next ingest tick retries the message.
    """
    now = (clock or utc_now)()
    text = None
    with session_factory() as session:
        run = session.execute(
            select(Run.target_id, Run.uid, Run.status, Run.alerts_done_at).where(Run.id == run_id)
        ).one_or_none()
        if run is None or run.status is not RunStatus.COMPLETED or run.alerts_done_at is not None:
            return

        ordered = session.execute(completed_runs(run.target_id, Run.id, Run.uid)).all()
        position = next(index for index, row in enumerate(ordered) if row.id == run_id)
        previous = ordered[position + 1] if position + 1 < len(ordered) else None
        new, still, fixed = _classified(session, run_id, previous.id if previous else None)
        if new or still or fixed:
            text = build_digest(
                branch=_branch(session, run.target_id),
                run_uid=run.uid,
                new=new,
                still=still,
                fixed=fixed,
                base_uid=previous.uid if previous else None,
                public_url=settings.public_url,
            )

    if text is None:
        mark_alerts_done(session_factory, run_id, now)
        return
    if discord.post(_webhook(settings.discord_webhook_url), redact(text, settings)):
        mark_alerts_done(session_factory, run_id, now)


def _branch(session: Session, target_id: int) -> str:
    return session.scalar(select(Target.branch).where(Target.id == target_id)) or "?"


def retry_pending(
    session_factory: sessionmaker[Session], settings: Settings, now: datetime
) -> None:
    """Retry the notification of every completed run that never got one (24 h), oldest first."""
    try:
        with session_factory() as session:
            rows = session.execute(
                select(Run.id, Run.target_id)
                .where(
                    Run.status == RunStatus.COMPLETED,
                    Run.alerts_done_at.is_(None),
                    Run.ingested_at >= now - timedelta(hours=24),
                )
                .order_by(Run.created_at, Run.id)
            ).all()
    except Exception:
        log.exception("alerting: could not load runs with pending alerts")
        return

    for row in rows:
        try:
            alert_completed_run(session_factory, settings, row.id, clock=lambda: now)
        except Exception:
            log.exception("run %s: retrying the alert failed", row.id)


# -- ops alerts -----------------------------------------------------------------------------


def _webhook(secret: SecretStr | None) -> str | None:
    return secret.get_secret_value() if secret is not None else None


def ops_alert(
    session_factory: sessionmaker[Session],
    settings: Settings,
    key: str,
    text: str,
    every: timedelta,
    clock: Clock | None = None,
) -> None:
    """Post an ops alert unless the same key was posted within ``every``."""
    now = (clock or utc_now)()
    try:
        with session_factory() as session:
            sent_at = session.scalar(select(AlertSent.sent_at).where(AlertSent.key == key))
        if sent_at is not None and sent_at > now - every:
            return
    except Exception:
        # The database may be unreachable (that is often the reason for the alert).
        log.exception("ops alert %s: could not check the dedupe window", key)

    content = redact(f"[ops] {text}", settings)[:MAX_MESSAGE]
    webhook = _webhook(settings.discord_ops_webhook_url) or _webhook(settings.discord_webhook_url)
    if not discord.post(webhook, content):
        return

    try:
        with session_factory() as session, session.begin():
            session.execute(
                pg_insert(AlertSent)
                .values(key=key, sent_at=now)
                .on_conflict_do_update(index_elements=[AlertSent.key], set_={"sent_at": now})
            )
    except Exception:
        log.exception("ops alert %s: could not record that it was sent", key)


# -- the ops-alert catalogue: one function per kind, in the design table's order ------------


def run_failed_alert(
    session_factory: sessionmaker[Session],
    settings: Settings,
    uid: uuid.UUID,
    branch: str,
    status: RunStatus,
    error: str,
) -> None:
    """A run became ``infra_error`` or ``lost`` (sent by ``runs.mark_infra_error``)."""
    ops_alert(
        session_factory,
        settings,
        f"run:{uid}",
        f"run {run_link(settings.public_url, uid)} ({branch}) is {status.value}: {error}",
        RUN_FAILED_WINDOW,
    )


def github_auth_alert(
    session_factory: sessionmaker[Session], settings: Settings, clock: Clock | None = None
) -> None:
    ops_alert(
        session_factory,
        settings,
        "github-auth",
        "GitHub authentication failed (token invalid, expired, or revoked)",
        GITHUB_AUTH_WINDOW,
        clock=clock,
    )


def check_token_expiry(
    session_factory: sessionmaker[Session],
    settings: Settings,
    expires_at: datetime | None,
    clock: Clock | None = None,
) -> None:
    now = (clock or utc_now)()
    if expires_at is not None and expires_at - now < TOKEN_EXPIRY_WARNING:
        ops_alert(
            session_factory,
            settings,
            "token-expiry",
            f"GitHub token expires at {expires_at.isoformat()}",
            TOKEN_EXPIRY_WINDOW,
            clock=clock,
        )


def check_stale_targets(
    session_factory: sessionmaker[Session], settings: Settings, clock: Clock | None = None
) -> None:
    """Alert for enabled targets with no completed run for two schedule intervals.

    Called by every real dispatch tick (not ingest, so a broken ingest is still reported).
    """
    now = (clock or utc_now)()
    with session_factory() as session:
        targets = session.execute(
            select(Target.id, Target.branch, Target.schedule, Target.created_at)
            .where(Target.enabled.is_(True))
            .order_by(Target.branch)
        ).all()
        last_completed = dict(
            session.execute(
                select(Run.target_id, func.max(Run.created_at))
                .where(Run.status == RunStatus.COMPLETED)
                .group_by(Run.target_id)
            ).all()
        )

    for target in targets:
        # Deliberately created_at (like the streak order), not finished_at: a run queued
        # before an outage still counts once it completes.
        reference = last_completed.get(target.id) or target.created_at
        try:
            interval = _schedule_interval(target.schedule, now)
        except Exception:
            log.exception("stale check: invalid schedule for %s", target.branch)
            continue
        if now - reference > 2 * interval:
            ops_alert(
                session_factory,
                settings,
                f"stale:{target.branch}",
                f"target {target.branch} has no completed run since {reference.isoformat()}",
                STALE_TARGET_WINDOW,
                clock=clock,
            )


def _schedule_interval(schedule: str, now: datetime) -> timedelta:
    """The time between two consecutive cron fires."""
    first = croniter(schedule, now).get_next(datetime)
    return croniter(schedule, first).get_next(datetime) - first


def crash_alert(
    session_factory: sessionmaker[Session], settings: Settings, command: str, details: str
) -> None:
    """An unattended command crashed; ``details`` is the caller's short error + traceback tail."""
    ops_alert(
        session_factory, settings, f"crash:{command}", f"{command} crashed: {details}", CRASH_WINDOW
    )
