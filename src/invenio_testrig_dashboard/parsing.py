"""Turn a testrig report directory into ParsedRun (format v0: data.json)."""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NamedTuple

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, field_validator

from .junit import MAX_MESSAGE_LENGTH, JunitError, ParsedCase, read_junit
from .status import FAILING, PackageStatus, TestStatus

log = logging.getLogger(__name__)

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S UTC"
FORMAT_VERSION_V0 = 0
MAX_PACKAGE_NAME_BYTES = 200  # indexed in package_result, so keep it Postgres-indexable
MAX_DATA_JSON_BYTES = 50 * 1024 * 1024
RESERVED_PACKAGE_NAMES = {"e2e", "repo"}
SUITE_CLASSNAME = "<suite>"
SUITE_MESSAGE = "package failed but no test failed, e.g. collection error or a failing step outside pytest, see log"


class InvalidResults(Exception):
    """The run directory does not contain valid, readable testrig output."""


def _storable(value: str) -> str:
    """Reject text Postgres/psycopg can't store: NUL characters and lone surrogates.

    JSON allows both (\\u0000, \\ud800); without this check a crafted data.json parses fine
    and the ingest fails later inside the database transaction.
    """
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("must be valid Unicode (no lone surrogates)") from None
    return value


# Every string read from data.json; XML (JUnit) already rejects both cases.
Text = Annotated[str, AfterValidator(_storable)]


class _Model(BaseModel):
    # data.json has many fields we don't read, and we never echo raw input in errors.
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)


class Reference(_Model):
    package: Text = Field(min_length=1)
    branch: Text | None = None
    commit: Text | None = None
    actual_version: Text | None = None
    commits_from_version: list[Text] = Field(default_factory=list)

    @field_validator("package")
    @classmethod
    def _package_byte_length(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_PACKAGE_NAME_BYTES:
            raise ValueError(f"must be at most {MAX_PACKAGE_NAME_BYTES} UTF-8 bytes")
        return value


class PackageInfo(_Model):
    reference: Reference
    unpatched_reference: Reference | None = None


class Side(_Model):
    status: Text
    package: PackageInfo


class PackageEntry(_Model):
    artefact_dir: Text = Field(min_length=1)
    has_original_junit: bool
    has_patched_junit: bool
    original: Side
    patched: Side


class DataJson(_Model):
    started_at: Text
    last_updated: Text
    completed: bool
    python_version: Text
    config_name: Text | None = None
    packages: list[PackageEntry]
    repository_e2e: PackageEntry | None = None
    repository: PackageEntry | None = None


@dataclass(frozen=True)
class ParsedPackage:
    """One package; ``artifact_dir`` is relative to the report root."""

    name: str
    version: str | None
    git_ref: str | None
    git_commit: str | None
    commits_ahead: int | None
    status: PackageStatus
    duration_s: float | None
    has_junit: bool
    cases: tuple[ParsedCase, ...]
    artifact_dir: str
    side: str


@dataclass(frozen=True)
class ParsedRun:
    format_version: int
    python_version: str
    name: str | None
    started_at: datetime
    finished_at: datetime
    completed: bool
    packages: tuple[ParsedPackage, ...]


class _ChosenSide(NamedTuple):
    side: Side
    prefix: str
    has_junit: bool


class _JunitData(NamedTuple):
    cases: tuple[ParsedCase, ...]
    duration_s: float | None
    has_junit: bool
    unreadable: str | None


_STATUS_MAP = {
    "success": PackageStatus.PASSED,
    "failed": PackageStatus.FAILED,
    "skipped": PackageStatus.NOT_RUN,
    # "pending" is handled in _package_status: its meaning depends on whether the run completed.
}
# testrig keeps a side "pending" when its job wrote no status file: the job crashed, was cancelled
# or timed out. Runs 44dc430b and 99fb4188 (2026-11-14): the e2e job failed on GitHub (its S3 setup
# aborted before writing a status), but the completed report still said "pending" on both sides.
NO_RESULT_MESSAGE = (
    "no result: the job produced no status file (it crashed, was cancelled or timed out); "
    "see the job on GitHub"
)
# Statuses meaning "this side did not run" (used to choose between original and patched).
_DID_NOT_RUN = {"skipped", "pending"}


def _timestamp(value: str, field: str) -> datetime:
    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError as exc:
        raise InvalidResults(f"{field}: invalid timestamp {value[:100]!r}") from exc


def _validation_message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"])
    return f"{location}: {error['msg']}" if location else str(error["msg"])


def _package_status(value: str, completed: bool, where: str) -> PackageStatus:
    """Map testrig's status; anything unknown makes the report invalid.

    "pending" in an unfinished report is a package that hasn't run yet. In a completed report it
    is a package that should have run but left no result, which is an error, never "not run".
    """
    if value == "pending":
        return PackageStatus.ERROR if completed else PackageStatus.NOT_RUN
    try:
        return _STATUS_MAP[value]
    except KeyError:
        raise InvalidResults(f"{where}: unknown status {value[:100]!r}") from None


def _side(entry: PackageEntry) -> _ChosenSide:
    """Use the patched side unless it was skipped; e2e runs patched, normal packages original.

    A "pending" (no result) patched side falls back to the original only when the original has a
    real result. Otherwise the pending side is kept, so a crashed e2e job is reported as such
    instead of hiding behind its "skipped" original side.
    """
    patched = entry.patched.status
    if patched == "skipped" or (patched == "pending" and entry.original.status not in _DID_NOT_RUN):
        return _ChosenSide(entry.original, "original", entry.has_original_junit)
    return _ChosenSide(entry.patched, "patched", entry.has_patched_junit)


def _resolve_dir(artefact_dir: str, root: Path) -> tuple[Path, str]:
    """Return the resolved directory and its canonical path relative to the report root."""
    resolved_root = root.resolve()
    directory = (resolved_root / artefact_dir).resolve()
    if not directory.is_relative_to(resolved_root):
        raise InvalidResults(f"artefact_dir escapes the report directory: {artefact_dir[:100]!r}")
    return directory, directory.relative_to(resolved_root).as_posix()


def _suite_case(status: TestStatus, message: str | None) -> ParsedCase:
    return ParsedCase(
        classname=SUITE_CLASSNAME,
        name=SUITE_CLASSNAME,
        status=status,
        duration_s=None,
        message=message,
    )


def _with_suite_cases(
    status: PackageStatus,
    cases: tuple[ParsedCase, ...],
    unreadable_reason: str | None,
    junit_name: str,
    error_message: str = SUITE_MESSAGE,
) -> tuple[PackageStatus, tuple[ParsedCase, ...]]:
    failing = any(case.status in FAILING for case in cases)

    # Rule 4: an unreadable JUnit means the suite errored, even if the package "passed".
    if unreadable_reason is not None and status == PackageStatus.PASSED:
        status = PackageStatus.ERROR

    # Rules 1 and 5: failed/errored package without a failing test -> suite-level error case.
    if status in (PackageStatus.FAILED, PackageStatus.ERROR) and not failing:
        message = (
            f"{junit_name} unreadable: {unreadable_reason}"
            if unreadable_reason is not None
            else error_message
        )
        return status, (*cases, _suite_case(TestStatus.ERROR, message[:MAX_MESSAGE_LENGTH]))

    # Rule 2: a passing package needs a passing suite case, so a fixed suite can be detected.
    if status == PackageStatus.PASSED:
        return status, (*cases, _suite_case(TestStatus.PASSED, None))

    # Rule 3: not_run/skipped packages get no pseudo case.
    return status, cases


def _read_junit_data(directory: Path, prefix: str, has_junit: bool, where: str) -> _JunitData:
    if not has_junit:
        return _JunitData((), None, False, None)

    path = directory / f"{prefix}_junit.xml"
    if not path.is_file():
        log.warning("%s: %s is missing", where, path.name)
        return _JunitData((), None, False, None)

    # A symlink inside the report could point outside it; only read files inside the root.
    if not path.resolve().is_relative_to(directory):
        reason = "path escapes the report directory"
        log.warning("%s: %s %s", where, path.name, reason)
        return _JunitData((), None, False, reason)

    try:
        result = read_junit(path)
    except JunitError as exc:
        log.warning("%s: %s", where, exc)
        return _JunitData((), None, False, str(exc))
    return _JunitData(result.cases, result.duration_s, True, None)


def _build_package(
    entry: PackageEntry, root: Path, completed: bool, name: str | None = None
) -> ParsedPackage:
    chosen = _side(entry)
    reference = chosen.side.package.reference
    package_name = name or reference.package
    if name is None and package_name in RESERVED_PACKAGE_NAMES:
        raise InvalidResults(f"package name {package_name!r} is reserved for the top-level entry")
    where = f"{package_name[:100]} ({chosen.prefix})"
    status = _package_status(chosen.side.status, completed, where)
    no_result = completed and chosen.side.status == "pending"

    directory, artifact_dir = _resolve_dir(entry.artefact_dir, root)
    junit_name = f"{chosen.prefix}_junit.xml"
    junit = _read_junit_data(directory, chosen.prefix, chosen.has_junit, where)
    status, cases = _with_suite_cases(
        status,
        junit.cases,
        junit.unreadable,
        junit_name,
        NO_RESULT_MESSAGE if no_result else SUITE_MESSAGE,
    )

    unpatched = chosen.side.package.unpatched_reference
    return ParsedPackage(
        name=package_name,
        version=reference.actual_version,
        git_ref=reference.branch,
        git_commit=(unpatched.commit if unpatched and unpatched.commit else reference.commit),
        commits_ahead=len(unpatched.commits_from_version) if unpatched is not None else None,
        status=status,
        duration_s=junit.duration_s,
        has_junit=junit.has_junit,
        cases=cases,
        artifact_dir=artifact_dir,
        side=chosen.prefix,
    )


def parse_run_dir(path: str | Path) -> ParsedRun:
    """Parse a testrig report directory.

    Returns a validated ParsedRun, or raises InvalidResults with a readable message.
    """
    root = Path(path)
    if not root.is_dir():
        raise InvalidResults(f"report directory not found: {root}")
    if (root / "results.json").exists():
        # Format v1 (results.json + vendored schema) arrives when testrig releases it.
        raise InvalidResults("results.json (format v1) is not supported yet")

    data_path = root / "data.json"
    if not data_path.is_file():
        raise InvalidResults("no results.json or data.json")

    try:
        size = data_path.stat().st_size
    except OSError as exc:
        raise InvalidResults(f"cannot read data.json: {exc}") from exc
    if size > MAX_DATA_JSON_BYTES:
        raise InvalidResults(f"data.json too large ({size} bytes, limit {MAX_DATA_JSON_BYTES})")

    try:
        raw = json.loads(data_path.read_bytes())
    except OSError as exc:
        raise InvalidResults(f"cannot read data.json: {exc}") from exc
    except (ValueError, RecursionError) as exc:
        raise InvalidResults(f"data.json is not valid JSON: {exc}") from exc

    try:
        data = DataJson.model_validate(raw)
    except ValidationError as exc:
        raise InvalidResults(_validation_message(exc)) from exc

    if data.completed and not data.packages:
        raise InvalidResults("completed run lists no packages")

    started_at = _timestamp(data.started_at, "started_at")
    finished_at = _timestamp(data.last_updated, "last_updated")

    packages = [_build_package(entry, root, data.completed) for entry in data.packages]
    if data.repository_e2e is not None:
        packages.append(_build_package(data.repository_e2e, root, data.completed, name="e2e"))
    if data.repository is not None:
        packages.append(_build_package(data.repository, root, data.completed, name="repo"))

    seen: set[str] = set()
    for package in packages:
        if package.name in seen:
            raise InvalidResults(f"duplicate package name {package.name[:100]!r}")
        seen.add(package.name)

    return ParsedRun(
        format_version=FORMAT_VERSION_V0,
        python_version=data.python_version,
        name=data.config_name,
        started_at=started_at,
        finished_at=finished_at,
        completed=data.completed,
        packages=tuple(packages),
    )
