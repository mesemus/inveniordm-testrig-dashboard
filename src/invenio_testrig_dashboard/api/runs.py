"""The API router for runs and test results.

The domain helpers (``RunRef``, ``mark_infra_error``, ``short_error``) live in
``invenio_testrig_dashboard.runs``; this module is only the HTTP layer.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, case, or_, select

from ..history import completed_runs, flaky_case_ids, last_completed_run_ids
from ..models import PackageResult, Run, Target, TestCase, TestResult
from ..status import TestStatus
from .artifacts import expected_artifacts
from .deps import SessionDep, run_or_404
from .pagination import FilterText, Page, PageNumber, PageSize, ResultId, paginate
from .schemas import (
    PackageOut,
    RunDetail,
    TestListItem,
    TestResultDetail,
    TestStatusName,
)

router = APIRouter(prefix="/api", tags=["runs"])

STATUS_RANK = case(
    (TestResult.status == int(TestStatus.FAILED), 0),
    (TestResult.status == int(TestStatus.ERROR), 1),
    (TestResult.status == int(TestStatus.PASSED), 2),
    else_=3,  # skipped last
)

SORT_COLUMNS = {
    "status": STATUS_RANK,
    "package": TestCase.package,
    "classname": TestCase.classname,
    "name": TestCase.name,
    "duration": TestResult.duration_s,
}
SortField = Literal["status", "package", "classname", "name", "duration"]
Order = Literal["asc", "desc"]


def _public_github_url(url: str | None) -> str | None:
    return url if url and url.startswith("https://github.com/") else None


@router.get("/runs/{uid}", response_model=RunDetail)
def run_detail(uid: uuid.UUID, session: SessionDep) -> RunDetail:
    run = run_or_404(session, uid)

    branch = session.scalar(select(Target.branch).where(Target.id == run.target_id)) or "?"
    previous = session.scalar(
        completed_runs(run.target_id, Run.uid)
        .where(
            or_(
                Run.created_at < run.created_at,
                and_(Run.created_at == run.created_at, Run.id < run.id),
            )
        )
        .limit(1)
    )

    packages = session.scalars(
        select(PackageResult).where(PackageResult.run_id == run.id).order_by(PackageResult.package)
    ).all()
    return RunDetail(
        uid=run.uid,
        branch=branch,
        status=run.status,
        trigger=run.trigger,
        triggered_by=run.triggered_by,
        testrig_ref=run.testrig_ref,
        testrig_config=run.testrig_config,
        gh_run_id=run.gh_run_id,
        gh_run_url=_public_github_url(run.gh_run_url),
        error=run.error,
        created_at=run.created_at,
        dispatched_at=run.dispatched_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        ingested_at=run.ingested_at,
        packages_total=run.packages_total,
        packages_failed=run.packages_failed,
        tests_total=run.tests_total,
        tests_failed=run.tests_failed,
        tests_skipped=run.tests_skipped,
        progress_total=run.progress_total,
        progress_done=run.progress_done,
        progress_failed=run.progress_failed,
        progress_at=run.progress_at,
        previous_completed_uid=previous,
        packages=[
            PackageOut(
                package=package.package,
                version=package.version,
                git_ref=package.git_ref,
                git_commit=package.git_commit,
                commits_ahead=package.commits_ahead,
                status=package.status,
                has_junit=package.has_junit,
                duration_s=package.duration_s,
                tests_total=package.tests_total,
                tests_failed=package.tests_failed,
                tests_skipped=package.tests_skipped,
                artifacts=expected_artifacts(run.uid, package),
            )
            for package in packages
        ],
    )


@router.get("/runs/{uid}/tests", response_model=Page[TestListItem])
def run_tests(
    uid: uuid.UUID,
    session: SessionDep,
    page: PageNumber = 0,
    page_size: PageSize = 50,
    package: FilterText = None,
    status: Annotated[list[TestStatusName] | None, Query()] = None,
    q: FilterText = None,
    sort: SortField = "status",
    order: Order = "asc",
) -> Page[TestListItem]:
    run = run_or_404(session, uid)

    stmt = (
        select(
            TestCase.id.label("test_case_id"),
            TestCase.package,
            TestCase.classname,
            TestCase.name,
            TestResult.status,
            TestResult.duration_s,
            TestResult.message.is_not(None).label("has_message"),
        )
        .join(TestCase, TestCase.id == TestResult.test_case_id)
        .where(TestResult.run_id == run.id)
    )
    if package is not None:
        stmt = stmt.where(TestCase.package == package)
    if status:
        stmt = stmt.where(TestResult.status.in_([name.code for name in status]))
    if q:
        # autoescape: % and _ in the user's text are literal wildcards.
        stmt = stmt.where(TestCase.name.icontains(q, autoescape=True))

    column = SORT_COLUMNS[sort]
    tie_breakers = (TestCase.package, TestCase.name, TestCase.id)
    stmt = stmt.order_by(
        column.asc() if order == "asc" else column.desc(),
        *(breaker for breaker in tie_breakers if breaker is not column),
    )

    rows, total = paginate(session, stmt, page, page_size)
    case_ids = [row.test_case_id for row in rows]
    flaky: set[int] = set()
    if case_ids:
        flaky = flaky_case_ids(session, last_completed_run_ids(session, run.target_id), case_ids)

    return Page(
        items=[
            TestListItem(
                test_case_id=row.test_case_id,
                package=row.package,
                classname=row.classname,
                name=row.name,
                status=TestStatusName.of(row.status),
                duration_s=row.duration_s,
                has_message=row.has_message,
                flaky=row.test_case_id in flaky,
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/runs/{uid}/tests/{test_case_id}", response_model=TestResultDetail)
def run_test_detail(
    uid: uuid.UUID, test_case_id: ResultId, session: SessionDep
) -> TestResultDetail:
    run = run_or_404(session, uid)
    row = session.execute(
        select(
            TestCase.id.label("test_case_id"),
            TestCase.package,
            TestCase.classname,
            TestCase.name,
            TestResult.status,
            TestResult.duration_s,
            TestResult.message,
        )
        .join(TestCase, TestCase.id == TestResult.test_case_id)
        .where(TestResult.run_id == run.id, TestResult.test_case_id == test_case_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "test result not found")
    return TestResultDetail(
        test_case_id=row.test_case_id,
        package=row.package,
        classname=row.classname,
        name=row.name,
        status=TestStatusName.of(row.status),
        duration_s=row.duration_s,
        message=row.message,
    )
