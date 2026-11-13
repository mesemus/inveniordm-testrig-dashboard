"""Targets overview, per-target runs, matrix, and trend."""

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..history import NEWEST_FIRST, completed_runs
from ..models import PackageResult, Run, Target
from ..status import RunStatus
from .deps import SessionDep, target_or_404
from .pagination import Branch, Page, PageNumber, PageSize, paginate
from .schemas import (
    MatrixOut,
    MatrixPackage,
    MatrixRun,
    RunListItem,
    RunSummary,
    TargetOut,
    TrendPoint,
)

router = APIRouter(prefix="/api", tags=["targets"])


def _latest_runs(session, completed_only: bool) -> dict[int, RunSummary]:
    rn = func.row_number().over(partition_by=Run.target_id, order_by=list(NEWEST_FIRST)).label("rn")
    stmt = select(
        Run.target_id,
        Run.uid,
        Run.status,
        Run.created_at,
        Run.tests_total,
        Run.tests_failed,
        Run.packages_total,
        Run.packages_failed,
        Run.progress_total,
        Run.progress_done,
        Run.progress_failed,
        Run.progress_at,
        rn,
    )
    if completed_only:
        stmt = stmt.where(Run.status == RunStatus.COMPLETED)
    ranked = stmt.subquery()
    rows = session.execute(select(ranked).where(ranked.c.rn == 1)).all()
    # Two of these run per /api/targets request (all runs, then completed runs);
    # fine at a few thousand runs.
    return {row.target_id: RunSummary.model_validate(row) for row in rows}


@router.get("/targets", response_model=list[TargetOut])
def list_targets(session: SessionDep) -> list[TargetOut]:
    targets = session.execute(
        select(Target.id, Target.branch, Target.enabled, Target.schedule).order_by(Target.branch)
    ).all()
    latest = _latest_runs(session, completed_only=False)
    completed = _latest_runs(session, completed_only=True)
    return [
        TargetOut(
            branch=target.branch,
            enabled=target.enabled,
            schedule=target.schedule,
            latest_run=latest.get(target.id),
            latest_completed_run=completed.get(target.id),
        )
        for target in targets
    ]


@router.get("/targets/{branch}/runs", response_model=Page[RunListItem])
def target_runs(
    branch: Branch,
    session: SessionDep,
    page: PageNumber = 0,
    page_size: PageSize = 50,
    status: Annotated[list[RunStatus] | None, Query()] = None,
) -> Page[RunListItem]:
    target = target_or_404(session, branch)
    stmt = select(
        Run.uid,
        Run.status,
        Run.trigger,
        Run.triggered_by,
        Run.created_at,
        Run.started_at,
        Run.finished_at,
        Run.tests_total,
        Run.tests_failed,
        Run.packages_total,
        Run.packages_failed,
        Run.error,
        Run.progress_total,
        Run.progress_done,
        Run.progress_failed,
        Run.progress_at,
    ).where(Run.target_id == target.id)
    if status:
        stmt = stmt.where(Run.status.in_(status))
    stmt = stmt.order_by(*NEWEST_FIRST)

    rows, total = paginate(session, stmt, page, page_size)
    return Page(
        items=[RunListItem.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/targets/{branch}/matrix", response_model=MatrixOut)
def target_matrix(
    branch: Branch,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> MatrixOut:
    target = target_or_404(session, branch)
    runs = session.execute(
        completed_runs(target.id, Run.id, Run.uid, Run.created_at).limit(limit)
    ).all()
    runs = list(reversed(runs))  # oldest -> newest for the UI

    cells: dict[tuple[int, str], object] = {}
    if runs:
        for row in session.execute(
            select(PackageResult.run_id, PackageResult.package, PackageResult.status).where(
                PackageResult.run_id.in_([run.id for run in runs])
            )
        ):
            cells[(row.run_id, row.package)] = row.status

    packages = sorted({package for _, package in cells})
    return MatrixOut(
        runs=[MatrixRun(uid=run.uid, created_at=run.created_at) for run in runs],
        packages=[
            MatrixPackage(
                package=package,
                cells=[cells.get((run.id, package)) for run in runs],
            )
            for package in packages
        ],
    )


@router.get("/targets/{branch}/trend", response_model=list[TrendPoint])
def target_trend(
    branch: Branch,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=365)] = 90,
) -> list[TrendPoint]:
    target = target_or_404(session, branch)
    duration = func.extract("epoch", Run.finished_at - Run.started_at).label("duration_s")
    rows = session.execute(
        completed_runs(
            target.id,
            Run.uid,
            Run.created_at,
            Run.tests_total,
            Run.tests_failed,
            Run.packages_failed,
            duration,
        ).limit(limit)
    ).all()
    return [
        TrendPoint(
            uid=row.uid,
            created_at=row.created_at,
            tests_total=row.tests_total,
            tests_failed=row.tests_failed,
            packages_failed=row.packages_failed,
            duration_s=float(row.duration_s) if row.duration_s is not None else None,
        )
        for row in reversed(rows)
    ]
