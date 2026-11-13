"""Test case detail and its cross-run history."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from ..history import FLAKY_WINDOW, flaky_case_ids, last_completed_run_ids
from ..models import Run, TestCase, TestResult
from .deps import SessionDep, target_or_404
from .pagination import ResultId, TargetParam
from .schemas import HistoryItem, HistoryOut, TestCaseOut, TestStatusName

router = APIRouter(prefix="/api", tags=["test-cases"])


@router.get("/test-cases/{case_id}", response_model=TestCaseOut)
def test_case_detail(case_id: ResultId, session: SessionDep) -> TestCaseOut:
    row = session.execute(
        select(
            TestCase.id,
            TestCase.package,
            TestCase.classname,
            TestCase.name,
            Run.uid.label("first_seen_run_uid"),
            Run.created_at.label("first_seen_at"),
        )
        .join(Run, Run.id == TestCase.first_seen_run_id)
        .where(TestCase.id == case_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "test case not found")
    return TestCaseOut(**row._mapping)


@router.get("/test-cases/{case_id}/history", response_model=HistoryOut)
def test_case_history(
    case_id: ResultId,
    target: TargetParam,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> HistoryOut:
    if session.get(TestCase, case_id) is None:
        raise HTTPException(404, "test case not found")
    target_id = target_or_404(session, target).id

    window = last_completed_run_ids(
        session, target_id, limit=max(limit, FLAKY_WINDOW)
    )  # oldest first; enough for both the page and the flaky window
    run_ids = window[-limit:]
    runs = {
        row.id: row
        for row in session.execute(
            select(Run.id, Run.uid, Run.created_at).where(Run.id.in_(run_ids))
        )
    }
    statuses = {
        row.run_id: row.status
        for row in session.execute(
            select(TestResult.run_id, TestResult.status).where(
                TestResult.test_case_id == case_id, TestResult.run_id.in_(run_ids)
            )
        )
    }
    flaky = bool(flaky_case_ids(session, window[-FLAKY_WINDOW:], [case_id]))
    return HistoryOut(
        flaky=flaky,
        items=[
            HistoryItem(
                uid=runs[run_id].uid,
                created_at=runs[run_id].created_at,
                status=(TestStatusName.of(statuses[run_id]) if run_id in statuses else None),
            )
            for run_id in run_ids
            if run_id in runs
        ],
    )
