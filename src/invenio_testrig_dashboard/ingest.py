"""Store a parsed testrig report in Postgres and S3 (``store_run``)."""

import logging
import time
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

from sqlalchemy import (
    REAL,
    Column,
    LargeBinary,
    MetaData,
    SmallInteger,
    Table,
    Text,
    delete,
    func,
    insert,
    literal,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from . import alerts
from .db import copy_rows
from .models import PackageResult, Run, TestCase, TestResult, case_key
from .parsing import InvalidResults, ParsedRun, parse_run_dir
from .runs import RunRef, mark_infra_error, short_error
from .settings import Settings
from .status import FAILING, PackageStatus, RunStatus, TestStatus
from .storage import run_prefix, upload_dir

# Planner statistics for the tables a run bulk-loads (an allowed text() exception: a fixed
# statement, no values; the names are pinned to the models by a test).
ANALYZE_RESULTS = text("ANALYZE test_result, test_case")

log = logging.getLogger(__name__)

FAILING_STATUSES = {int(status) for status in FAILING}
SKIPPED_STATUS = int(TestStatus.SKIPPED)

# Not in models.py: this table is not part of the schema, and Alembic must not see it.
TMP_CASES = Table(
    "tmp_cases",
    MetaData(),
    Column("key_hash", LargeBinary),
    Column("package", Text),
    Column("classname", Text),
    Column("name", Text),
    Column("status", SmallInteger),
    Column("duration_s", REAL),
    Column("message", Text),
    prefixes=["TEMPORARY"],
    postgresql_on_commit="DROP",
)


class _Summary(NamedTuple):
    packages_total: int
    packages_failed: int
    tests_total: int
    tests_failed: int


def store_run(
    session_factory: sessionmaker[Session],
    s3: Any,
    settings: Settings,
    run: Run | RunRef,
    run_dir: str | Path,
    expected_name: str | None = None,
    *,
    alert: bool,
) -> RunStatus:
    """Validate, upload, and store a run; returns its final status.

    ``expected_name`` is the run uid; a report whose ``config_name`` differs belongs to
    another run and is rejected before anything is uploaded.

    ``alert`` (required, so every caller decides): post the streak digest and the run-failed
    ops alert. Ingest passes True; ``ingest-dir`` (seeding) passes False unless asked.
    """
    started = time.perf_counter()
    local_dir = Path(run_dir)

    try:
        parsed = parse_run_dir(local_dir)
    except InvalidResults as exc:
        # The files still help debugging, so upload them before recording the failure.
        upload_dir(s3, settings.s3_bucket, local_dir, run_prefix(run))
        mark_infra_error(session_factory, run.id, f"invalid results: {exc}", settings, alert=alert)
        log.warning("run %s: invalid results: %s", run.uid, exc)
        return RunStatus.INFRA_ERROR

    if expected_name is not None and parsed.name != expected_name:
        mark_infra_error(
            session_factory,
            run.id,
            f"report belongs to another run: config_name={str(parsed.name)[:200]!r}",
            settings,
            alert=alert,
        )
        log.warning("run %s: report config_name %r does not match", run.uid, parsed.name)
        return RunStatus.INFRA_ERROR

    upload_dir(s3, settings.s3_bucket, local_dir, run_prefix(run))

    if not parsed.completed:
        mark_infra_error(
            session_factory, run.id, "testrig reported an incomplete run", settings, alert=alert
        )
        log.warning("run %s: incomplete run", run.uid)
        return RunStatus.INFRA_ERROR

    try:
        with session_factory() as session, session.begin():
            summary = _write_rows(session, run.id, parsed)
    except Exception as exc:
        log.exception("run %s: storing rows failed", run.uid)
        try:
            mark_infra_error(session_factory, run.id, short_error(exc), settings, alert=alert)
        except Exception:
            log.exception("run %s: could not record infra_error", run.uid)
        raise

    _refresh_planner_statistics(session_factory, run.uid)

    # Alerting runs after the commit, and must never make ingest fail.
    try:
        if alert:
            alerts.alert_completed_run(session_factory, settings, run.id)
        else:
            # Deliberately not alerting (seeding) is a final decision too: without this,
            # the next ingest tick's retry_pending would post the digest after all.
            alerts.mark_alerts_done(session_factory, run.id)
    except Exception:
        log.exception("run %s: alerting failed", run.uid)
    log.info(
        "run %s stored: %d packages, %d tests, %d failed in %.1fs",
        run.uid,
        summary.packages_total,
        summary.tests_total,
        summary.tests_failed,
        time.perf_counter() - started,
    )
    return RunStatus.COMPLETED


def _refresh_planner_statistics(session_factory: sessionmaker[Session], uid: UUID) -> None:
    """ANALYZE the bulk-loaded tables, so the run's pages are planned with real statistics.

    A first ingest copies thousands of rows into near-empty tables, and the UI may query them
    before autovacuum's ANALYZE runs. Without statistics the planner picked a plan that ran for
    6 minutes on 50,055 rows (2026-11-14). Never fails the ingest: the rows are stored,
    and autovacuum catches up anyway.
    ponytail: runs on every ingest; ANALYZE samples a fixed number of rows, so its cost stays
    roughly constant as the tables grow. Skip it for small loads if it ever shows up in timings.
    """
    try:
        with session_factory() as session, session.begin():
            session.execute(ANALYZE_RESULTS)
    except Exception as exc:
        log.warning("run %s: ANALYZE after the bulk load failed: %s", uid, short_error(exc))


def _write_rows(session: Session, run_id: int, parsed: ParsedRun) -> _Summary:
    session.execute(delete(TestResult).where(TestResult.run_id == run_id))
    session.execute(delete(PackageResult).where(PackageResult.run_id == run_id))

    package_rows = []
    case_rows = []
    for package in parsed.packages:
        failed = sum(1 for case in package.cases if case.status in FAILING)
        skipped = sum(1 for case in package.cases if case.status is TestStatus.SKIPPED)
        package_rows.append(
            {
                "run_id": run_id,
                "package": package.name,
                "artifact_dir": package.artifact_dir,
                "side": package.side,
                "version": package.version,
                "git_ref": package.git_ref,
                "git_commit": package.git_commit,
                "commits_ahead": package.commits_ahead,
                "status": package.status,
                "has_junit": package.has_junit,
                "duration_s": package.duration_s,
                "tests_total": len(package.cases),
                "tests_failed": failed,
                "tests_skipped": skipped,
            }
        )
        for case in package.cases:
            case_rows.append(
                (
                    case_key(package.name, case.classname, case.name),
                    package.name,
                    case.classname,
                    case.name,
                    int(case.status),
                    case.duration_s,
                    case.message,
                )
            )

    session.execute(insert(PackageResult), package_rows)

    TMP_CASES.create(session.connection())
    copy_rows(session, TMP_CASES, case_rows)

    session.execute(
        pg_insert(TestCase)
        .from_select(
            ["key_hash", "package", "classname", "name", "first_seen_run_id"],
            select(
                TMP_CASES.c.key_hash,
                TMP_CASES.c.package,
                TMP_CASES.c.classname,
                TMP_CASES.c.name,
                literal(run_id),
            ).distinct(),
        )
        .on_conflict_do_nothing(index_elements=[TestCase.key_hash])
    )

    session.execute(
        insert(TestResult).from_select(
            ["run_id", "test_case_id", "status", "duration_s", "message"],
            select(
                literal(run_id),
                TestCase.id,
                TMP_CASES.c.status,
                TMP_CASES.c.duration_s,
                TMP_CASES.c.message,
            ).join(TestCase, TestCase.key_hash == TMP_CASES.c.key_hash),
        )
    )

    summary = _Summary(
        packages_total=len(parsed.packages),
        packages_failed=sum(
            1
            for package in parsed.packages
            if package.status in (PackageStatus.FAILED, PackageStatus.ERROR)
        ),
        tests_total=len(case_rows),
        tests_failed=sum(1 for row in case_rows if row[4] in FAILING_STATUSES),
    )
    session.execute(
        update(Run)
        .where(Run.id == run_id)
        .values(
            status=RunStatus.COMPLETED,
            started_at=parsed.started_at,
            finished_at=parsed.finished_at,
            ingested_at=func.now(),
            error=None,
            packages_total=summary.packages_total,
            packages_failed=summary.packages_failed,
            tests_total=summary.tests_total,
            tests_failed=summary.tests_failed,
            tests_skipped=sum(1 for row in case_rows if row[4] == SKIPPED_STATUS),
        )
    )
    return summary
