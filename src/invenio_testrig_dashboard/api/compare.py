"""Compare two completed runs of the same target (packages and changed tests)."""

import uuid
from typing import Any, Literal, NamedTuple

from fastapi import APIRouter, HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased

from ..models import PackageResult, TestCase, TestResult
from ..status import FAILING, PackageStatus, RunStatus, TestStatus
from .deps import SessionDep, run_or_404
from .pagination import Page, PageNumber, PageSize, count_rows, paginate
from .schemas import CompareCounts, CompareOut, ComparePackage, CompareTestItem, TestStatusName

router = APIRouter(prefix="/api", tags=["compare"])

Kind = Literal["flipped", "new", "gone"]


class _PackageFacts(NamedTuple):
    status: PackageStatus | None
    version: str | None
    git_commit: str | None


EMPTY_FACTS = _PackageFacts(None, None, None)


def _comparable_runs(session, base: uuid.UUID, head: uuid.UUID):
    base_run, head_run = run_or_404(session, base), run_or_404(session, head)
    if base_run.status is not RunStatus.COMPLETED or head_run.status is not RunStatus.COMPLETED:
        raise HTTPException(400, "compare requires completed runs")
    if base_run.target_id != head_run.target_id:
        raise HTTPException(400, "both runs must belong to the same target")
    return base_run, head_run


def _flip_condition(base, head):
    passed = int(TestStatus.PASSED)
    return or_(
        and_(base.status == passed, head.status.in_(FAILING)),
        and_(base.status.in_(FAILING), head.status == passed),
    )


def _packages(session, run_id: int) -> dict[str, _PackageFacts]:
    return {
        row.package: _PackageFacts(row.status, row.version, row.git_commit)
        for row in session.execute(
            select(
                PackageResult.package,
                PackageResult.status,
                PackageResult.version,
                PackageResult.git_commit,
            ).where(PackageResult.run_id == run_id)
        )
    }


def _junit_in_both(session, base_id: int, head_id: int) -> set[str]:
    def packages(run_id: int) -> set[str]:
        return set(
            session.scalars(
                select(PackageResult.package).where(
                    PackageResult.run_id == run_id, PackageResult.has_junit.is_(True)
                )
            )
        )

    return packages(base_id) & packages(head_id)


def _changed_tests_stmt(base_id: int, head_id: int, kind: Kind, junit_both: set[str]) -> Any:
    """The rows of one compare tab (pure; no I/O). Its count is ``count_rows`` over this."""
    base_result, head_result = aliased(TestResult), aliased(TestResult)
    columns = (
        TestCase.id.label("test_case_id"),
        TestCase.package,
        TestCase.classname,
        TestCase.name,
        base_result.status.label("base_status"),
        head_result.status.label("head_status"),
    )

    if kind == "flipped":
        return (
            select(*columns)
            .select_from(base_result)
            .join(
                head_result,
                and_(
                    head_result.run_id == head_id,
                    head_result.test_case_id == base_result.test_case_id,
                ),
            )
            .join(TestCase, TestCase.id == base_result.test_case_id)
            .where(base_result.run_id == base_id, _flip_condition(base_result, head_result))
        )

    if kind == "new":
        return (
            select(*columns)
            .select_from(head_result)
            .join(TestCase, TestCase.id == head_result.test_case_id)
            .outerjoin(
                base_result,
                and_(
                    base_result.run_id == base_id,
                    base_result.test_case_id == head_result.test_case_id,
                ),
            )
            .where(head_result.run_id == head_id, base_result.test_case_id.is_(None))
        )

    return (
        select(*columns)
        .select_from(base_result)
        .join(TestCase, TestCase.id == base_result.test_case_id)
        .outerjoin(
            head_result,
            and_(
                head_result.run_id == head_id,
                head_result.test_case_id == base_result.test_case_id,
            ),
        )
        .where(
            base_result.run_id == base_id,
            head_result.test_case_id.is_(None),
            TestCase.package.in_(junit_both),
        )
    )


@router.get("/compare", response_model=CompareOut)
def compare(base: uuid.UUID, head: uuid.UUID, session: SessionDep) -> CompareOut:
    base_run, head_run = _comparable_runs(session, base, head)

    base_packages = _packages(session, base_run.id)
    head_packages = _packages(session, head_run.id)
    packages = []
    for name in sorted(set(base_packages) | set(head_packages)):
        base_facts = base_packages.get(name, EMPTY_FACTS)
        head_facts = head_packages.get(name, EMPTY_FACTS)
        packages.append(
            ComparePackage(
                package=name,
                base_status=base_facts.status,
                head_status=head_facts.status,
                base_version=base_facts.version,
                head_version=head_facts.version,
                base_commit=base_facts.git_commit,
                head_commit=head_facts.git_commit,
                changed=base_facts != head_facts,
            )
        )

    junit_both = _junit_in_both(session, base_run.id, head_run.id)
    counts = CompareCounts(
        flipped=count_rows(
            session, _changed_tests_stmt(base_run.id, head_run.id, "flipped", junit_both)
        ),
        new=count_rows(session, _changed_tests_stmt(base_run.id, head_run.id, "new", junit_both)),
        gone=count_rows(session, _changed_tests_stmt(base_run.id, head_run.id, "gone", junit_both)),
    )
    return CompareOut(base=base, head=head, packages=packages, counts=counts)


@router.get("/compare/tests", response_model=Page[CompareTestItem])
def compare_tests(
    base: uuid.UUID,
    head: uuid.UUID,
    kind: Kind,
    session: SessionDep,
    page: PageNumber = 0,
    page_size: PageSize = 50,
) -> Page[CompareTestItem]:
    base_run, head_run = _comparable_runs(session, base, head)
    junit_both = _junit_in_both(session, base_run.id, head_run.id) if kind == "gone" else set()
    stmt = _changed_tests_stmt(base_run.id, head_run.id, kind, junit_both).order_by(
        TestCase.package, TestCase.classname, TestCase.name, TestCase.id
    )

    rows, total = paginate(session, stmt, page, page_size)
    return Page(
        items=[
            CompareTestItem(
                test_case_id=row.test_case_id,
                package=row.package,
                classname=row.classname,
                name=row.name,
                base_status=(
                    TestStatusName.of(row.base_status) if row.base_status is not None else None
                ),
                head_status=(
                    TestStatusName.of(row.head_status) if row.head_status is not None else None
                ),
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
