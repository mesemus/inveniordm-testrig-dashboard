from enum import IntEnum, StrEnum


class RunStatus(StrEnum):
    REQUESTED = "requested"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    INFRA_ERROR = "infra_error"
    LOST = "lost"


class RunTrigger(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"


class PackageStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    NOT_RUN = "not_run"


class TestStatus(IntEnum):
    PASSED = 0
    FAILED = 1
    ERROR = 2
    SKIPPED = 3


# Used with `.in_(FAILING)`; it is a plain tuple, not an enum member.
FAILING = (TestStatus.FAILED, TestStatus.ERROR)
