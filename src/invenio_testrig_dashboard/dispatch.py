"""Find due targets, create runs, and start them through testrig."""

import copy
import functools
import importlib.metadata
import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, NamedTuple

import yaml
from croniter import croniter
from sqlalchemy import Engine, and_, select, update
from sqlalchemy.orm import Session, sessionmaker

from . import alerts
from .alerts import Clock, utc_now
from .db import try_advisory_lock
from .github import MAX_GH_RUN_ID
from .models import Run, Target
from .runs import mark_infra_error, short_error
from .settings import Settings
from .status import RunStatus, RunTrigger

log = logging.getLogger(__name__)

# Arbitrary but stable key; it only has to be unique among our advisory locks.
DISPATCH_LOCK_KEY = 20261115
TIMEOUT_SECONDS = 900
STDERR_TAIL_LINES = 20
MAX_OUTPUT_TAIL_BYTES = 256 * 1024
# A runaway testrig must not fill the pod's ephemeral storage during the 900 s window.
MAX_OUTPUT_BYTES = 50 * 1024 * 1024
POLL_SECONDS = 1.0
TESTRIG_REPOSITORY = "oarepo/invenio-testrig"
# testrig is a locked dependency (pyproject.toml), so dispatch runs the venv's console script and nothing
# is downloaded at dispatch time. Still a separate process: the timeout, the process-group kill
# and the GH_TOKEN-only environment below apply to it.
TESTRIG_EXECUTABLE = str(Path(sys.executable).with_name("invenio-testrig"))
TESTRIG_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
# `.invalid` is a reserved TLD (RFC 2606): the address can never belong to a real person.
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "invenio-testrig-dashboard",
    "GIT_AUTHOR_EMAIL": "invenio-testrig-dashboard@noreply.invalid",
    "GIT_COMMITTER_NAME": "invenio-testrig-dashboard",
    "GIT_COMMITTER_EMAIL": "invenio-testrig-dashboard@noreply.invalid",
}
# Scheduled dispatches per cron fire: a failure at dispatch is retried on the next ticks,
# a persistent one stops after this many (and waits for the next fire).
MAX_DISPATCH_ATTEMPTS = 3

ACTIVE_STATUSES = (RunStatus.REQUESTED, RunStatus.QUEUED, RunStatus.RUNNING)
DISPATCH_BLOCKING_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING)

DEFAULT_CONFIG_HEADER = (
    "# testrig built-in defaults were used (no --config-file);\n"
    "# the seed repository is overridden with --repository.\n"
)

_RUN_ID_LINE = re.compile(r"run-id: (\d{1,19})")
_RUN_URL_LINE = re.compile(r"run-url: (\S+)")


class OutputTooLarge(Exception):
    """testrig wrote more than MAX_OUTPUT_BYTES; it was killed."""

    def __init__(self, limit: int, stderr: str) -> None:
        super().__init__(f"output exceeded {limit // (1024 * 1024)} MB")
        self.stderr = stderr


class TestrigResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class TestrigJob:
    command: list[str]
    config_path: Path
    env: dict[str, str]
    # testrig clones the runner repo into its working directory, so it gets a private,
    # temporary one (never the dashboard's own, which may be read-only in a container).
    cwd: Path


TestrigRunner = Callable[[TestrigJob], TestrigResult]


@dataclass(frozen=True)
class DispatchSummary:
    due_branches: tuple[str, ...] = ()
    created_branches: tuple[str, ...] = ()
    dispatched_uids: tuple[str, ...] = ()
    failed_uids: tuple[str, ...] = ()
    busy: bool = False


class _ActiveRun(NamedTuple):
    uid: uuid.UUID
    status: RunStatus
    dispatched_at: datetime | None
    created_at: datetime
    lost_at: datetime


class TestrigNotPinned(Exception):
    """invenio-testrig is installed from something without a commit (e.g. an editable path)."""


@functools.cache
def testrig_ref() -> str:
    """The commit of the installed testrig.

    The local CLI and the GitHub workflow must run the same testrig, so the version comes from
    the package metadata (PEP 610 `direct_url.json`) instead of a config key. Cached: it cannot
    change while the process runs. Tests monkeypatch it with `monkeypatch.setattr`.
    """
    try:
        raw = importlib.metadata.distribution("invenio-testrig").read_text("direct_url.json")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - installed by uv sync
        raw = None
    info = json.loads(raw) if raw else {}
    vcs = info.get("vcs_info")
    commit = vcs.get("commit_id") if isinstance(vcs, dict) else None
    if not isinstance(commit, str) or not TESTRIG_COMMIT_RE.fullmatch(commit):
        raise TestrigNotPinned(
            "invenio-testrig is not installed from a git commit; pin it in pyproject.toml "
            "([tool.uv.sources]) and run `uv lock`"
        )
    return commit


class RunRequestRejected(Exception):
    """A manual run can't be requested: unknown or disabled target, or an active run exists."""

    def __init__(
        self, message: str, *, kind: str = "unknown", active: _ActiveRun | None = None
    ) -> None:
        super().__init__(message)
        self.kind = kind  # "unknown" | "disabled" | "active"
        self.active = active


def request_run(
    session_factory: sessionmaker[Session],
    settings: Settings,
    branch: str,
    triggered_by: str,
    now: datetime,
    dry_run: bool = False,
) -> uuid.UUID | None:
    """Request a manual run of ``branch``; the next dispatch tick starts it.

    The one way to ask for an unscheduled run (``itd dispatch <target>`` now, the re-run
    button). Manual runs ignore the schedule and don't count toward the
    scheduled-attempt cap. One active run per target still applies. The target row is
    locked, so two concurrent requests can't both pass the check.
    """
    with session_factory() as session, session.begin():
        target = session.execute(
            select(Target.id, Target.enabled).where(Target.branch == branch).with_for_update()
        ).one_or_none()
        if target is None:
            raise RunRequestRejected(
                f"unknown target {branch!r} (run migrate-and-sync?)", kind="unknown"
            )
        if not target.enabled:
            raise RunRequestRejected(f"target {branch!r} is disabled", kind="disabled")
        active = session.execute(
            select(Run.uid, Run.status, Run.dispatched_at, Run.created_at)
            .where(Run.target_id == target.id, Run.status.in_(ACTIVE_STATUSES))
            .limit(1)
        ).one_or_none()
        if active is not None:
            # Say why, and when a stuck queued run will be given up (it blocks re-runs until then).
            reference = active.dispatched_at or active.created_at
            lost_at = reference + timedelta(hours=settings.lost_after_hours)
            raise RunRequestRejected(
                f"target {branch!r} already has an active run {active.uid}",
                kind="active",
                active=_ActiveRun(
                    active.uid, active.status, active.dispatched_at, active.created_at, lost_at
                ),
            )
        if dry_run:
            return None
        uid = uuid.uuid4()
        session.add(
            Run(
                uid=uid,
                target_id=target.id,
                status=RunStatus.REQUESTED,
                trigger=RunTrigger.MANUAL,
                triggered_by=triggered_by,
                testrig_ref=testrig_ref(),
                created_at=now,
            )
        )
    return uid


class _RequestedRun(NamedTuple):
    id: int
    uid: uuid.UUID
    target_id: int


class _TargetInfo(NamedTuple):
    branch: str
    enabled: bool


class ScheduledAttempt(NamedTuple):
    """A scheduled run of a target, as far as deciding "is it due again?" goes."""

    created_at: datetime
    # infra_error before any GitHub run existed (testrig failed at dispatch): nothing ran,
    # so it doesn't count as this fire's run, but it does count as an attempt.
    dispatch_failed: bool


def is_due(
    schedule: str,
    attempts: Sequence[ScheduledAttempt],
    has_active_run: bool,
    now: datetime,
) -> bool:
    """True when the target's last cron fire still needs a scheduled run.

    A run since the last fire satisfies it, unless it failed at dispatch: then the next
    tick retries, up to MAX_DISPATCH_ATTEMPTS per fire (a persistent failure stops there
    instead of failing, and alerting, every tick). An active run suppresses a new one; the
    target stays due, so a later tick picks it up.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if has_active_run:
        return False
    last_fire = croniter(schedule, now).get_prev(datetime)
    since_fire = [attempt for attempt in attempts if attempt.created_at >= last_fire]
    if any(not attempt.dispatch_failed for attempt in since_fire):
        return False
    return len(since_fire) < MAX_DISPATCH_ATTEMPTS


def build_testrig_config(settings: Settings, branch: str) -> dict[str, Any]:
    """The effective testrig config for one run: base config with the seed overridden."""
    base = settings.testrig_config if settings.testrig_config is not None else {}
    config = copy.deepcopy(base)
    seed = config.get("seed_repository")
    if not isinstance(seed, dict):
        seed = {}
        config["seed_repository"] = seed
    seed["git"] = f"{settings.seed_repository}@{branch}"
    return config


def render_testrig_config(settings: Settings, branch: str) -> str:
    """The YAML stored on the run. The header records the default-config mode honestly."""
    text = yaml.safe_dump(build_testrig_config(settings, branch), sort_keys=False)
    return DEFAULT_CONFIG_HEADER + text if settings.testrig_config is None else text


def build_testrig_command(
    settings: Settings, config_path: Path, branch: str, run_uid: uuid.UUID
) -> list[str]:
    command = [
        TESTRIG_EXECUTABLE,
        "github",
        "--target",
        settings.runner_repository,
        "--name",
        str(run_uid),
        "--non-interactive",
        # The token is tied to one existing runner repository: a missing (or inaccessible)
        # repository must fail the dispatch, never make testrig create a new public one.
        "--no-create-repository",
        # testrig bakes its repo/ref into the runner workflow; force the pinned one.
        "--overwrite-testrig-file",
        # Pin the workflow too: without these, the GitHub workflow runs testrig master,
        # whatever version the local CLI is.
        "--testrig-repository",
        TESTRIG_REPOSITORY,
        "--testrig-branch",
        testrig_ref(),
        "--ignore-uv-lock",
    ]
    if settings.testrig_config is not None:
        command += ["--config-file", str(config_path)]
    else:
        # testrig does not merge a partial config with its defaults. --repository
        # overrides the seed on top of the built-in default config instead.
        command += ["--repository", f"{settings.seed_repository}@{branch}"]
    return command


def default_run_testrig(job: TestrigJob) -> TestrigResult:
    """Run testrig with a time and output-size limit, killing its whole process group on either.

    testrig runs in its own session (so a timeout can kill it *and* its ``gh``/``git``
    children), which also means a terminal's Ctrl+C never reaches it. So any interruption of
    this process (KeyboardInterrupt, SystemExit from SIGTERM) kills the group too: no testrig
    keeps running after dispatch stopped, inside a temporary directory that's being deleted.
    """
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(  # noqa: S603 (list form, no shell; command is built from settings)
            job.command,
            stdout=stdout_file,
            stderr=stderr_file,
            env=job.env,
            cwd=job.cwd,
            start_new_session=True,
        )
        try:
            return _wait_for_testrig(process, stdout_file, stderr_file)
        except BaseException:
            _kill_group(process)
            raise


def _wait_for_testrig(
    process: subprocess.Popen[bytes], stdout_file: BinaryIO, stderr_file: BinaryIO
) -> TestrigResult:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while True:
        try:
            process.wait(timeout=POLL_SECONDS)
            break
        except subprocess.TimeoutExpired:
            pass
        # Raising is enough: default_run_testrig kills the process group on any exception.
        if _size(stdout_file) + _size(stderr_file) > MAX_OUTPUT_BYTES:
            raise OutputTooLarge(MAX_OUTPUT_BYTES, _read_output_tail(stderr_file))
        if time.monotonic() > deadline:
            raise subprocess.TimeoutExpired(
                process.args,
                TIMEOUT_SECONDS,
                output=_read_output_tail(stdout_file),
                stderr=_read_output_tail(stderr_file),
            )

    returncode = process.returncode
    if returncode is None:
        raise RuntimeError("testrig process did not terminate")
    return TestrigResult(
        returncode,
        _read_output_tail(stdout_file),
        _read_output_tail(stderr_file),
    )


def _size(file: BinaryIO) -> int:
    return os.fstat(file.fileno()).st_size


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _read_output_tail(file: BinaryIO) -> str:
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(max(0, size - MAX_OUTPUT_TAIL_BYTES))
    return file.read().decode("utf-8", errors="replace")


def _testrig_env(settings: Settings) -> dict[str, str]:
    # testrig/gh only need the GitHub token; don't hand them the dashboard's other secrets.
    env = {key: value for key, value in os.environ.items() if not key.startswith("ITD_")}
    env["GH_TOKEN"] = settings.github_token.get_secret_value()
    # testrig's `github` command opens the run in a browser (webbrowser.open) until its
    # non-interactive mode exists (testrig#3); `true` makes that a no-op everywhere.
    env["BROWSER"] = "true"
    # testrig commits the updated workflow file to the runner repo (--overwrite-testrig-file). A
    # container user has no git identity, so `git commit` fails with "Author identity unknown"
    # (2026-11-14, dispatch inside the image). A fixed bot identity also makes the dashboard's
    # commits recognisable; an operator can still override it with the usual GIT_* variables.
    for key, value in GIT_IDENTITY.items():
        env.setdefault(key, value)
    # testrig clones with `gh repo clone`, which passes the token to that one git command only,
    # then pushes with plain `git push`. Over HTTPS (the default without a gh config, e.g. in the
    # image) git had no credentials and tried to prompt: "could not read Username" (2026-11-14).
    # Register gh's credential helper for github.com, as `gh auth setup-git` would, but through
    # git's GIT_CONFIG_* environment: no config file, and the token stays in GH_TOKEN only.
    _add_git_config(
        env,
        [
            # An empty value first resets inherited helpers (e.g. a keychain) for github.com.
            ("credential.https://github.com.helper", ""),
            ("credential.https://github.com.helper", "!gh auth git-credential"),
        ],
    )
    # Never wait for a terminal that isn't there: fail with a clear message instead.
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def _add_git_config(env: dict[str, str], pairs: list[tuple[str, str]]) -> None:
    """Append git config entries via GIT_CONFIG_COUNT/KEY_n/VALUE_n, after any an operator set."""
    start = int(env.get("GIT_CONFIG_COUNT") or 0)
    for offset, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{start + offset}"] = key
        env[f"GIT_CONFIG_VALUE_{start + offset}"] = value
    env["GIT_CONFIG_COUNT"] = str(start + len(pairs))


def dispatch_tick(
    engine: Engine,
    session_factory: sessionmaker[Session],
    settings: Settings,
    now: datetime,
    run_testrig: TestrigRunner | None = None,
    dry_run: bool = False,
    clock: Clock | None = None,
    only_branch: str | None = None,
) -> DispatchSummary:
    """One dispatch tick, serialized by an advisory lock held on its own connection.

    ``now`` decides what is due; ``clock`` timestamps each dispatch when it happens
    (a tick can take a while, so they differ). ``only_branch`` limits the tick to one
    target (``itd dispatch <target>``): other targets are neither created nor dispatched.
    """
    runner = run_testrig or default_run_testrig
    with try_advisory_lock(engine, DISPATCH_LOCK_KEY) as locked:
        if not locked:
            log.warning("dispatch: another tick is running, skipping")
            return DispatchSummary(busy=True)
        return _tick(session_factory, settings, now, runner, dry_run, clock or utc_now, only_branch)


def _tick(
    session_factory: sessionmaker[Session],
    settings: Settings,
    now: datetime,
    run_testrig: TestrigRunner,
    dry_run: bool,
    clock: Clock,
    only_branch: str | None = None,
) -> DispatchSummary:
    with session_factory() as session:
        targets = _targets(session)
        due = _due_targets(session, targets, now)
        requested = _requested_runs(session)
        blocked = _blocked_targets(session)

    if only_branch is not None:
        due = [target_id for target_id in due if targets[target_id].branch == only_branch]
        requested = [run for run in requested if targets[run.target_id].branch == only_branch]

    if dry_run:
        return DispatchSummary(
            due_branches=tuple(targets[target_id].branch for target_id in due),
            dispatched_uids=tuple(
                str(run.uid)
                for run in _candidates(requested, blocked)
                if targets[run.target_id].enabled
            ),
        )

    created = tuple(targets[target_id].branch for target_id in due)
    _create_scheduled_runs(session_factory, settings, due, now)

    with session_factory() as session:
        requested = _requested_runs(session)
        blocked = _blocked_targets(session)
    if only_branch is not None:
        requested = [run for run in requested if targets[run.target_id].branch == only_branch]

    dispatched: list[str] = []
    failed: list[str] = []
    for run in _candidates(requested, blocked):
        target = targets.get(run.target_id)
        if target is None:
            log.warning("run %s: target %s no longer exists", run.uid, run.target_id)
            continue
        if not target.enabled:
            # Disabling a target means "stop testing it", also for runs requested earlier.
            mark_infra_error(session_factory, run.id, "target disabled before dispatch", settings)
            failed.append(str(run.uid))
            continue
        status = _dispatch_run(session_factory, settings, run, target.branch, clock, run_testrig)
        if status is RunStatus.QUEUED:
            dispatched.append(str(run.uid))
        elif status is RunStatus.INFRA_ERROR:
            failed.append(str(run.uid))

    # In dispatch, not ingest, so a broken ingest still gets reported.
    try:
        alerts.check_stale_targets(session_factory, settings, clock)
    except Exception:
        log.exception("dispatch: stale-target check failed")

    return DispatchSummary(
        due_branches=created,
        created_branches=created,
        dispatched_uids=tuple(dispatched),
        failed_uids=tuple(failed),
    )


def _targets(session: Session) -> dict[int, _TargetInfo]:
    rows = session.execute(select(Target.id, Target.branch, Target.enabled))
    return {row.id: _TargetInfo(row.branch, row.enabled) for row in rows}


def _due_targets(session: Session, targets: dict[int, _TargetInfo], now: datetime) -> list[int]:
    """Enabled targets whose schedule is due, ordered by branch."""
    schedules = dict(
        session.execute(select(Target.id, Target.schedule).where(Target.enabled.is_(True))).all()
    )
    active_targets = set(
        session.scalars(select(Run.target_id).where(Run.status.in_(ACTIVE_STATUSES)).distinct())
    )
    due = [
        target_id
        for target_id, schedule in schedules.items()
        if is_due(
            schedule,
            _recent_scheduled_attempts(session, target_id),
            target_id in active_targets,
            now,
        )
    ]
    return sorted(due, key=lambda target_id: targets[target_id].branch)


def _recent_scheduled_attempts(session: Session, target_id: int) -> list[ScheduledAttempt]:
    """The latest scheduled runs of a target; enough to see every attempt since its last fire.

    More than MAX_DISPATCH_ATTEMPTS runs since a fire can't exist (is_due stops at that many),
    so the newest MAX_DISPATCH_ATTEMPTS + 1 always include them all plus one older run.
    """
    rows = session.execute(
        select(
            Run.created_at,
            and_(Run.status == RunStatus.INFRA_ERROR, Run.gh_run_id.is_(None)),
        )
        .where(Run.target_id == target_id, Run.trigger == RunTrigger.SCHEDULE)
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(MAX_DISPATCH_ATTEMPTS + 1)
    )
    return [ScheduledAttempt(created_at, bool(failed)) for created_at, failed in rows]


def _create_scheduled_runs(
    session_factory: sessionmaker[Session], settings: Settings, due: list[int], now: datetime
) -> None:
    if not due:
        return
    with session_factory() as session, session.begin():
        for target_id in due:
            session.add(
                Run(
                    uid=uuid.uuid4(),
                    target_id=target_id,
                    status=RunStatus.REQUESTED,
                    trigger=RunTrigger.SCHEDULE,
                    testrig_ref=testrig_ref(),
                    created_at=now,
                )
            )


def _requested_runs(session: Session) -> list[_RequestedRun]:
    rows = session.execute(
        select(Run.id, Run.uid, Run.target_id)
        .where(Run.status == RunStatus.REQUESTED)
        .order_by(Run.created_at, Run.id)
    )
    return [_RequestedRun(row.id, row.uid, row.target_id) for row in rows]


def _blocked_targets(session: Session) -> set[int]:
    return set(
        session.scalars(
            select(Run.target_id).where(Run.status.in_(DISPATCH_BLOCKING_STATUSES)).distinct()
        )
    )


def _candidates(requested: list[_RequestedRun], blocked_targets: set[int]) -> list[_RequestedRun]:
    """Oldest requested run per target, skipping targets that are already queued/running."""
    candidates: list[_RequestedRun] = []
    seen: set[int] = set()
    for run in requested:
        if run.target_id in blocked_targets or run.target_id in seen:
            continue
        seen.add(run.target_id)
        candidates.append(run)
    return candidates


def _claim(
    session_factory: sessionmaker[Session], run_id: int, config_yaml: str, dispatched_at: datetime
) -> bool:
    """requested -> queued *before* testrig starts the workflow (at-most-once dispatch).

    If this process dies while testrig runs, the run stays queued without a GitHub run and
    ingest turns it into ``lost``; it is never dispatched a second time.
    """
    with session_factory() as session, session.begin():
        claimed = session.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == RunStatus.REQUESTED)
            .values(
                status=RunStatus.QUEUED,
                dispatched_at=dispatched_at,
                testrig_config=config_yaml,
                error=None,
            )
            .returning(Run.id)
        ).scalar_one_or_none()
        return claimed is not None


def _dispatch_run(
    session_factory: sessionmaker[Session],
    settings: Settings,
    run: _RequestedRun,
    branch: str,
    clock: Clock,
    run_testrig: TestrigRunner,
) -> RunStatus | None:
    """Claim and dispatch one run; returns its status, or None if someone else claimed it."""
    config_yaml = render_testrig_config(settings, branch)
    if not _claim(session_factory, run.id, config_yaml, clock()):
        log.warning("run %s: no longer requested, skipping", run.uid)
        return None

    def fail(message: str) -> RunStatus:
        mark_infra_error(session_factory, run.id, message, settings)
        return RunStatus.INFRA_ERROR

    with tempfile.TemporaryDirectory(prefix="itd-dispatch-") as tmp:
        config_path = Path(tmp) / "testrig.yaml"
        config_path.write_text(config_yaml, encoding="utf-8")
        job = TestrigJob(
            command=build_testrig_command(settings, config_path, branch, run.uid),
            config_path=config_path,
            env=_testrig_env(settings),
            cwd=Path(tmp),  # removed with everything testrig cloned when this block ends
        )
        try:
            result = run_testrig(job)
        except FileNotFoundError as exc:
            return fail(f"invenio-testrig not found: {exc}")
        except subprocess.TimeoutExpired as exc:
            return fail(f"testrig timed out after {exc.timeout}s: {_stderr_tail(exc.stderr)}")
        except OutputTooLarge as exc:
            return fail(f"testrig {exc}: {_stderr_tail(exc.stderr)}")
        except Exception as exc:
            # Anything else (PermissionError, a runner bug, ...) fails this run only.
            log.exception("run %s: dispatching failed", run.uid)
            return fail(f"dispatch failed: {short_error(exc)}")

    if result.returncode != 0:
        return fail(f"testrig exited with {result.returncode}: {_stderr_tail(result.stderr)}")

    gh_run_id, gh_run_url = _github_run(
        result.stdout + "\n" + result.stderr, settings.runner_repository
    )
    if gh_run_id is not None:
        with session_factory() as session, session.begin():
            session.execute(
                update(Run)
                .where(Run.id == run.id)
                .values(gh_run_id=gh_run_id, gh_run_url=gh_run_url)
            )
    log.info("run %s (%s) dispatched", run.uid, branch)
    return RunStatus.QUEUED


def _github_run(output: str, runner_repository: str) -> tuple[int | None, str | None]:
    """Parse testrig's exact ``run-id:``/``run-url:`` lines; anything else is unknown."""
    run_ids: list[str] = []
    run_urls: list[str] = []
    for line in output.splitlines():
        id_match = _RUN_ID_LINE.fullmatch(line)
        url_match = _RUN_URL_LINE.fullmatch(line)
        if id_match:
            run_ids.append(id_match.group(1))
        if url_match:
            run_urls.append(url_match.group(1))
    if len(run_ids) != 1 or len(run_urls) != 1:
        return None, None
    run_id = int(run_ids[0])
    expected_url = f"https://github.com/{runner_repository}/actions/runs/{run_id}"
    if not 0 < run_id <= MAX_GH_RUN_ID or run_urls[0] != expected_url:
        return None, None
    return run_id, expected_url


def _stderr_tail(output: str | bytes | None, lines: int = STDERR_TAIL_LINES) -> str:
    if output is None:
        return ""
    text = (
        output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
    ).strip()
    return "\n".join(text.splitlines()[-lines:])
