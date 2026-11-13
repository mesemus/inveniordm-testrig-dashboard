"""Pydantic response models for the read API."""

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from ..status import PackageStatus, RunStatus, RunTrigger, TestStatus


class TestStatusName(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"

    @classmethod
    def of(cls, value: int) -> TestStatusName:
        return cls(TestStatus(value).name.lower())

    @property
    def code(self) -> int:
        return int(TestStatus[self.value.upper()])


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uid: uuid.UUID
    status: RunStatus
    created_at: datetime
    tests_total: int | None
    tests_failed: int | None
    packages_total: int | None
    packages_failed: int | None
    progress_total: int | None
    progress_done: int | None
    progress_failed: list[str] | None
    progress_at: datetime | None


class TargetOut(BaseModel):
    branch: str
    enabled: bool
    schedule: str
    latest_run: RunSummary | None
    latest_completed_run: RunSummary | None


class RunListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uid: uuid.UUID
    status: RunStatus
    trigger: RunTrigger
    triggered_by: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    tests_total: int | None
    tests_failed: int | None
    packages_total: int | None
    packages_failed: int | None
    error: str | None
    progress_total: int | None
    progress_done: int | None
    progress_failed: list[str] | None
    progress_at: datetime | None


class MatrixRun(BaseModel):
    uid: uuid.UUID
    created_at: datetime


class MatrixPackage(BaseModel):
    package: str
    cells: list[PackageStatus | None]


class MatrixOut(BaseModel):
    runs: list[MatrixRun]
    packages: list[MatrixPackage]


class TrendPoint(BaseModel):
    uid: uuid.UUID
    created_at: datetime
    tests_total: int | None
    tests_failed: int | None
    packages_failed: int | None
    duration_s: float | None


class PackageOut(BaseModel):
    package: str
    version: str | None
    git_ref: str | None
    git_commit: str | None
    commits_ahead: int | None
    status: PackageStatus
    has_junit: bool
    duration_s: float | None
    tests_total: int | None
    tests_failed: int | None
    tests_skipped: int | None
    artifacts: dict[str, str]


class MeOut(BaseModel):
    login: str | None
    is_maintainer: bool
    login_enabled: bool


class RerunOut(BaseModel):
    uid: uuid.UUID


class RerunConflict(BaseModel):
    """409 body: the active run that blocks a re-run, and when it will be given up."""

    message: str
    uid: uuid.UUID
    status: RunStatus
    dispatched_at: datetime | None
    lost_at: datetime


class RunDetail(BaseModel):
    uid: uuid.UUID
    branch: str
    status: RunStatus
    trigger: RunTrigger
    triggered_by: str | None
    testrig_ref: str
    testrig_config: str | None
    gh_run_id: int | None
    gh_run_url: str | None
    error: str | None
    created_at: datetime
    dispatched_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    ingested_at: datetime | None
    packages_total: int | None
    packages_failed: int | None
    tests_total: int | None
    tests_failed: int | None
    tests_skipped: int | None
    progress_total: int | None
    progress_done: int | None
    progress_failed: list[str] | None
    progress_at: datetime | None
    previous_completed_uid: uuid.UUID | None
    packages: list[PackageOut]


class TestListItem(BaseModel):
    test_case_id: int
    package: str
    classname: str
    name: str
    status: TestStatusName
    duration_s: float | None
    has_message: bool
    flaky: bool


class TestResultDetail(BaseModel):
    test_case_id: int
    package: str
    classname: str
    name: str
    status: TestStatusName
    duration_s: float | None
    message: str | None


class TestCaseOut(BaseModel):
    id: int
    package: str
    classname: str
    name: str
    first_seen_run_uid: uuid.UUID
    first_seen_at: datetime


class HistoryItem(BaseModel):
    uid: uuid.UUID
    created_at: datetime
    status: TestStatusName | None


class HistoryOut(BaseModel):
    flaky: bool
    items: list[HistoryItem]


class ComparePackage(BaseModel):
    package: str
    base_status: PackageStatus | None
    head_status: PackageStatus | None
    base_version: str | None
    head_version: str | None
    base_commit: str | None
    head_commit: str | None
    changed: bool


class CompareCounts(BaseModel):
    flipped: int
    new: int
    gone: int


class CompareOut(BaseModel):
    base: uuid.UUID
    head: uuid.UUID
    packages: list[ComparePackage]
    counts: CompareCounts


class CompareTestItem(BaseModel):
    test_case_id: int
    package: str
    classname: str
    name: str
    base_status: TestStatusName | None
    head_status: TestStatusName | None
