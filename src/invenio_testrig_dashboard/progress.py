"""Live progress of a running run, from GitHub's job list (no I/O, no artifact downloads).

testrig runs one GitHub job per package, and a package job fails when its package fails, so the
job list already says which packages passed, failed, are running or are queued. This module turns
that list into numbers the API and the UI can show.
"""

import re
from typing import NamedTuple

# testrig's job ids (invenio-testrig/.github/workflows/verify-patches.yml). A job name is
# "<caller> / <job>" ("verify-patches / test (pkg)") or "<job> (<matrix value>)". If testrig
# renames a job, test_progress.py fails rather than the UI quietly showing nothing.
_PACKAGE_JOB = re.compile(
    r"(?:^|/ )(test|test_slow|test_repository|test_e2e)(?: \((?P<arg>[^)]*)\))?$"
)
# The package names parsing.py gives these two jobs (RESERVED_PACKAGE_NAMES).
_FIXED_NAMES = {"test_repository": "repo", "test_e2e": "e2e"}
# A conclusion that still means "this package is fine".
_FINE_CONCLUSIONS = {"success", "skipped", "neutral"}
MAX_NAME_LENGTH = 200
MAX_FAILED = 100


class RunProgress(NamedTuple):
    total: int
    """Package jobs (shards count separately)."""
    done: int
    """Finished package jobs, whatever the conclusion."""
    failed: tuple[str, ...]
    """Packages whose job failed, sorted and unique."""


def progress_from_jobs(jobs: list[dict]) -> RunProgress | None:
    """Progress of a running run, or None while no package job exists (``prepare`` runs first).

    The job names come from the runner repo's workflow, so they are untrusted: unusable ones are
    dropped instead of stored.
    """
    total = 0
    done = 0
    failed: set[str] = set()
    for job in jobs:
        name = job.get("name")
        if not isinstance(name, str):
            continue
        match = _PACKAGE_JOB.search(name)
        if match is None:
            continue
        package = _package_name(match.group(1), match.group("arg"))
        if package is None or not _usable(package):
            continue
        total += 1
        if job.get("status") != "completed":
            continue
        done += 1
        if job.get("conclusion") not in _FINE_CONCLUSIONS:
            failed.add(package)
    if total == 0:
        return None
    return RunProgress(total=total, done=done, failed=tuple(sorted(failed))[:MAX_FAILED])


def _package_name(job_id: str, arg: str | None) -> str | None:
    if job_id in _FIXED_NAMES:
        return _FIXED_NAMES[job_id]
    if not arg:
        return None  # "test" without a matrix value is not a package job
    # "test_slow (pkg#2)": shards report their package once.
    return arg.rsplit("#", 1)[0]


def _usable(package: str) -> bool:
    return 0 < len(package) <= MAX_NAME_LENGTH and package.isprintable()
