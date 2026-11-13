"""A target's run history: completed runs in order, and the flaky rule (README).

Imports only ``models``/``status``: ``alerts`` and the API both use it, and ``alerts`` must not
import ``runs``/``ingest*`` (they import ``alerts``), so this stays a leaf module.
"""

from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from .models import Run, TestResult
from .status import FAILING, RunStatus, TestStatus

# The tie-breaker matters: the Discord digest's compare link and the run page's "previous run"
# must pick the same run when two share a created_at.
NEWEST_FIRST = (Run.created_at.desc(), Run.id.desc())
FLAKY_WINDOW = 10
PARTICIPATING = (int(TestStatus.PASSED), *(int(status) for status in FAILING))


def completed_runs(target_id: int, *columns: Any) -> Select[Any]:
    """The target's completed runs, newest first. Callers add ``.limit()`` or extra ``.where()``."""
    return (
        select(*(columns or (Run.id,)))
        .where(Run.target_id == target_id, Run.status == RunStatus.COMPLETED)
        .order_by(*NEWEST_FIRST)
    )


def last_completed_run_ids(
    session: Session, target_id: int, limit: int = FLAKY_WINDOW
) -> list[int]:
    """The target's last ``limit`` completed run ids, oldest first."""
    ids = session.scalars(completed_runs(target_id, Run.id).limit(limit)).all()
    return list(reversed(ids))


def _flips(statuses: list[int]) -> int:
    flips = 0
    previous: bool | None = None
    for status in statuses:
        if status not in PARTICIPATING:
            continue  # skipped/absent neither extends nor breaks a pass/fail sequence
        failing = status in FAILING
        if previous is not None and failing != previous:
            flips += 1
        previous = failing
    return flips


def flaky_case_ids(session: Session, run_ids: list[int], case_ids: list[int]) -> set[int]:
    """Flaky test case ids among ``case_ids``, using only the given runs (<= 100 cases)."""
    if not run_ids or not case_ids:
        return set()
    order = {run_id: index for index, run_id in enumerate(run_ids)}
    rows = session.execute(
        select(TestResult.test_case_id, TestResult.run_id, TestResult.status).where(
            TestResult.run_id.in_(run_ids), TestResult.test_case_id.in_(case_ids)
        )
    ).all()
    by_case: dict[int, list[tuple[int, int]]] = {}
    for row in rows:
        by_case.setdefault(row.test_case_id, []).append((order[row.run_id], row.status))
    return {
        case_id
        for case_id, entries in by_case.items()
        if _flips([status for _, status in sorted(entries)]) >= 2
    }
