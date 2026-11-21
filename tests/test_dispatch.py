import importlib.metadata
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import tomllib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, select

from invenio_testrig_dashboard import dispatch as dispatch_module
from invenio_testrig_dashboard.dispatch import (
    DISPATCH_LOCK_KEY,
    MAX_DISPATCH_ATTEMPTS,
    MAX_OUTPUT_TAIL_BYTES,
    DispatchSummary,
    ScheduledAttempt,
    build_testrig_command,
    build_testrig_config,
    default_run_testrig,
    dispatch_tick,
    is_due,
    render_testrig_config,
)
from invenio_testrig_dashboard.dispatch import (
    TestrigJob as Job,
)
from invenio_testrig_dashboard.dispatch import (
    TestrigResult as Result,
)
from invenio_testrig_dashboard.models import Run
from invenio_testrig_dashboard.settings import Target as TargetConfig
from invenio_testrig_dashboard.status import RunStatus, RunTrigger

NOW = datetime(2026, 11, 13, 1, 5, tzinfo=UTC)
RUN_UID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class FakeRunner:
    def __init__(self, result: Result | None = None):
        self.result = result or Result(0, "", "")
        self.jobs: list[Job] = []
        self.config_texts: list[str] = []
        self.configs: list[dict] = []

    def __call__(self, job: Job) -> Result:
        text = job.config_path.read_text()
        self.jobs.append(job)
        self.config_texts.append(text)
        self.configs.append(yaml.safe_load(text))
        return self.result


DAILY = "0 1 * * *"
TODAYS_FIRE = datetime(2026, 11, 13, 1, 0, tzinfo=UTC)


def ran(created_at: datetime) -> ScheduledAttempt:
    """A scheduled run that got past dispatch (whatever happened later)."""
    return ScheduledAttempt(created_at, dispatch_failed=False)


def failed(created_at: datetime) -> ScheduledAttempt:
    """A scheduled run whose dispatch failed before any GitHub run existed."""
    return ScheduledAttempt(created_at, dispatch_failed=True)


def test_due_without_previous_scheduled_run():
    assert is_due(DAILY, [], False, NOW) is True


def test_not_due_when_todays_fire_already_has_a_run():
    assert is_due(DAILY, [ran(TODAYS_FIRE)], False, NOW) is False


def test_due_when_the_last_run_was_yesterday():
    assert is_due(DAILY, [ran(datetime(2026, 11, 12, 1, 0, tzinfo=UTC))], False, NOW) is True


def test_not_due_before_todays_fire():
    now = datetime(2026, 11, 13, 0, 55, tzinfo=UTC)
    assert is_due(DAILY, [ran(datetime(2026, 11, 12, 1, 0, tzinfo=UTC))], False, now) is False


def test_active_run_suppresses_a_due_target():
    assert is_due(DAILY, [], True, NOW) is False


def test_due_across_utc_midnight():
    now = datetime(2026, 11, 14, 0, 5, tzinfo=UTC)
    midnight_target = "0 0 * * *"

    assert is_due(midnight_target, [ran(datetime(2026, 11, 13, 0, 0, tzinfo=UTC))], False, now)
    assert not is_due(midnight_target, [ran(datetime(2026, 11, 14, 0, 0, tzinfo=UTC))], False, now)


def test_naive_now_is_rejected_even_with_an_active_run():
    with pytest.raises(ValueError, match="timezone-aware"):
        is_due(DAILY, [], True, datetime(2026, 11, 13, 1, 5))


def test_a_failed_dispatch_is_retried():
    assert is_due(DAILY, [failed(TODAYS_FIRE)], False, NOW) is True


def test_retries_stop_after_max_attempts_per_fire():
    attempts = [
        failed(TODAYS_FIRE + timedelta(minutes=15 * i)) for i in range(MAX_DISPATCH_ATTEMPTS)
    ]

    assert is_due(DAILY, attempts, False, NOW + timedelta(hours=1)) is False
    # The next fire starts over.
    assert is_due(DAILY, attempts, False, NOW + timedelta(days=1)) is True


def test_a_successful_retry_ends_the_fire():
    attempts = [failed(TODAYS_FIRE), ran(TODAYS_FIRE + timedelta(minutes=15))]

    assert is_due(DAILY, attempts, False, NOW + timedelta(minutes=30)) is False


def test_failures_before_the_last_fire_dont_use_up_its_attempts():
    yesterday = TODAYS_FIRE - timedelta(days=1)
    attempts = [failed(yesterday + timedelta(minutes=15 * i)) for i in range(MAX_DISPATCH_ATTEMPTS)]

    assert is_due(DAILY, attempts, False, NOW) is True


def test_due_targets_are_created_and_dispatched(make_target, engine, session_factory, settings):
    make_target("master")
    make_target("14.x")
    fake = FakeRunner()

    summary = dispatch_tick(engine, session_factory, settings, NOW, fake)

    assert summary.created_branches == ("14.x", "master")
    assert len(summary.dispatched_uids) == 2
    assert summary.failed_uids == ()
    assert len(fake.jobs) == 2
    with session_factory() as session:
        runs = session.scalars(select(Run)).all()
    assert len(runs) == 2
    assert {run.status for run in runs} == {RunStatus.QUEUED}
    assert all(run.testrig_config.startswith("# testrig built-in defaults") for run in runs)
    assert all(run.testrig_config and "seed_repository" in run.testrig_config for run in runs)

    for job in fake.jobs:
        assert job.command[job.command.index("--target") + 1] == settings.runner_repository
        assert "--ignore-uv-lock" in job.command
        assert "--repository" in job.command
        assert "--config-file" not in job.command
        assert job.env["GH_TOKEN"] == settings.github_token.get_secret_value()
        assert settings.github_token.get_secret_value() not in " ".join(job.command)
    git_values = {config["seed_repository"]["git"] for config in fake.configs}
    assert git_values == {
        f"{settings.seed_repository}@master",
        f"{settings.seed_repository}@14.x",
    }
    assert all(text.startswith("# testrig built-in defaults") for text in fake.config_texts)


def test_disabled_target_is_ignored(make_target, engine, session_factory, settings):
    make_target("master", enabled=False)
    fake = FakeRunner()

    summary = dispatch_tick(engine, session_factory, settings, NOW, fake)

    assert summary == DispatchSummary()
    assert fake.jobs == []
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Run)) == 0


def test_failed_dispatch_records_short_storable_error(
    make_target, engine, session_factory, settings
):
    make_target("master")
    lines = [f"line-{index}" for index in range(25)]
    lines[-1] += "\x00\ud800 " + settings.github_token.get_secret_value()
    fake = FakeRunner(Result(1, "", "\n".join(lines)))

    summary = dispatch_tick(engine, session_factory, settings, NOW, fake)

    assert len(summary.failed_uids) == 1
    with session_factory() as session:
        run = session.scalar(select(Run))
    assert run.status is RunStatus.INFRA_ERROR
    assert run.testrig_config.startswith("# testrig built-in defaults")
    assert "testrig exited with 1" in run.error
    assert "line-0" not in run.error
    assert "line-24" in run.error
    assert "***" in run.error
    assert "\x00" not in run.error
    assert "\ud800" not in run.error
    assert len(run.error) <= 2000


def test_one_failed_dispatch_does_not_stop_the_other(
    make_target, engine, session_factory, settings
):
    make_target("master")
    make_target("14.x")

    def fake(job):
        config = yaml.safe_load(job.config_path.read_text())
        if config["seed_repository"]["git"].endswith("@master"):
            return Result(1, "", "boom")
        return Result(0, "", "")

    summary = dispatch_tick(engine, session_factory, settings, NOW, fake)

    assert len(summary.dispatched_uids) == 1
    assert len(summary.failed_uids) == 1
    with session_factory() as session:
        statuses = {run.status for run in session.scalars(select(Run))}
    assert statuses == {RunStatus.QUEUED, RunStatus.INFRA_ERROR}


def test_timeout_marks_run_infra_error(make_target, engine, session_factory, settings):
    make_target("master")

    def fake(job):
        raise subprocess.TimeoutExpired(job.command, 900, output="", stderr="partial\n")

    summary = dispatch_tick(engine, session_factory, settings, NOW, fake)

    assert len(summary.failed_uids) == 1
    with session_factory() as session:
        run = session.scalar(select(Run))
    assert run.status is RunStatus.INFRA_ERROR
    assert "timed out" in run.error
    assert "partial" in run.error


def test_missing_testrig_executable_marks_run_infra_error(
    make_target, engine, session_factory, settings
):
    make_target("master")

    def fake(job):
        raise FileNotFoundError("invenio-testrig")

    summary = dispatch_tick(engine, session_factory, settings, NOW, fake)

    assert len(summary.failed_uids) == 1
    with session_factory() as session:
        run = session.scalar(select(Run))
    assert run.status is RunStatus.INFRA_ERROR
    assert "invenio-testrig not found" in run.error


def test_github_run_url_is_stored(make_target, engine, session_factory, settings):
    make_target("master")
    url = f"https://github.com/{settings.runner_repository}/actions/runs/123456789"
    fake = FakeRunner(Result(0, f"run-id: 123456789\nrun-url: {url}\n", ""))

    dispatch_tick(engine, session_factory, settings, NOW, fake)

    with session_factory() as session:
        run = session.scalar(select(Run))
    assert run.gh_run_id == 123456789
    assert run.gh_run_url == url


def test_github_run_parser_requires_exactly_one_wellformed_pair(settings):
    url = f"https://github.com/{settings.runner_repository}/actions/runs/123456789"
    valid = f"run-id: 123456789\nrun-url: {url}"

    assert dispatch_module._github_run(valid, settings.runner_repository) == (
        123456789,
        url,
    )
    for output in (
        "no run information here",
        f"prefix run-id: 123456789\nrun-url: {url}",
        f"run-id: 123456789 run-url: {url}",
        f"run-id: 123456789\nrun-id: 123456789\nrun-url: {url}",
        f"run-id: 123456789\nrun-url: {url}\nrun-url: {url}",
        f"run-url: {url}",
        "run-id: 123456789",
    ):
        assert dispatch_module._github_run(output, settings.runner_repository) == (None, None)


def test_github_run_url_from_another_repository_is_ignored(
    make_target, engine, session_factory, settings
):
    make_target("master")
    fake = FakeRunner(
        Result(
            0,
            "run-id: 123456789\nrun-url: https://github.com/someone/else/actions/runs/123456789",
            "",
        )
    )

    dispatch_tick(engine, session_factory, settings, NOW, fake)

    with session_factory() as session:
        run = session.scalar(select(Run))
    assert run.gh_run_id is None
    assert run.gh_run_url is None


def test_mismatched_github_run_id_and_url_are_ignored(
    make_target, engine, session_factory, settings
):
    make_target("master")
    fake = FakeRunner(
        Result(
            0,
            f"run-id: 123456789\nrun-url: https://github.com/{settings.runner_repository}/actions/runs/987654321",
            "",
        )
    )

    dispatch_tick(engine, session_factory, settings, NOW, fake)

    with session_factory() as session:
        run = session.scalar(select(Run))
    assert run.gh_run_id is None
    assert run.gh_run_url is None


def test_out_of_range_github_run_id_is_ignored(make_target, engine, session_factory, settings):
    make_target("master")
    run_id = "9999999999999999999"
    fake = FakeRunner(
        Result(
            0,
            f"run-id: {run_id}\nrun-url: https://github.com/{settings.runner_repository}/actions/runs/{run_id}",
            "",
        )
    )

    dispatch_tick(engine, session_factory, settings, NOW, fake)

    with session_factory() as session:
        run = session.scalar(select(Run))
    assert run.status is RunStatus.QUEUED
    assert run.gh_run_id is None


def test_manual_requested_run_is_dispatched_one_per_target(
    make_target, make_run, engine, session_factory, settings
):
    make_target("master")
    last_fire = datetime(2026, 11, 13, 1, 0, tzinfo=UTC)
    make_run("master", last_fire, trigger=RunTrigger.SCHEDULE, status=RunStatus.COMPLETED)
    first = make_run(
        "master",
        last_fire + timedelta(minutes=2),
        trigger=RunTrigger.MANUAL,
        status=RunStatus.REQUESTED,
    )
    second = make_run(
        "master",
        last_fire + timedelta(minutes=3),
        trigger=RunTrigger.MANUAL,
        status=RunStatus.REQUESTED,
    )
    fake = FakeRunner()

    summary = dispatch_tick(engine, session_factory, settings, NOW, fake)

    assert summary.created_branches == ()
    assert len(fake.jobs) == 1
    with session_factory() as session:
        assert session.get(Run, first.id).status is RunStatus.QUEUED
        assert session.get(Run, second.id).status is RunStatus.REQUESTED


def test_manual_run_does_not_count_as_a_scheduled_run(
    make_target, make_run, engine, session_factory, settings
):
    make_target("master")
    make_run(
        "master",
        datetime(2026, 11, 13, 1, 2, tzinfo=UTC),
        trigger=RunTrigger.MANUAL,
        status=RunStatus.COMPLETED,
    )
    fake = FakeRunner()

    summary = dispatch_tick(engine, session_factory, settings, NOW, fake)

    assert summary.created_branches == ("master",)


def test_dry_run_changes_nothing(make_target, engine, session_factory, settings):
    make_target("master")
    fake = FakeRunner()

    summary = dispatch_tick(engine, session_factory, settings, NOW, fake, dry_run=True)

    assert summary.due_branches == ("master",)
    assert summary.created_branches == ()
    assert summary.dispatched_uids == ()
    assert fake.jobs == []
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Run)) == 0


def test_dry_run_lists_existing_requested_runs(
    make_target, make_run, engine, session_factory, settings
):
    make_target("master")
    last_fire = datetime(2026, 11, 13, 1, 0, tzinfo=UTC)
    make_run("master", last_fire, trigger=RunTrigger.SCHEDULE, status=RunStatus.COMPLETED)
    requested = make_run(
        "master",
        last_fire + timedelta(minutes=2),
        trigger=RunTrigger.MANUAL,
        status=RunStatus.REQUESTED,
    )
    fake = FakeRunner()

    summary = dispatch_tick(engine, session_factory, settings, NOW, fake, dry_run=True)

    assert summary.due_branches == ()
    assert summary.dispatched_uids == (str(requested.uid),)
    assert fake.jobs == []


def test_advisory_lock_prevents_a_second_tick(make_target, engine, session_factory, settings):
    make_target("master")
    fake = FakeRunner()

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        assert connection.scalar(select(func.pg_try_advisory_lock(DISPATCH_LOCK_KEY)))
        try:
            summary = dispatch_tick(engine, session_factory, settings, NOW, fake)
        finally:
            connection.execute(select(func.pg_advisory_unlock(DISPATCH_LOCK_KEY)))

    assert summary.busy is True
    assert summary.created_branches == ()
    assert fake.jobs == []
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Run)) == 0


def test_command_uses_repository_override_without_custom_config(settings, tmp_path):
    text = render_testrig_config(settings, "master")
    command = build_testrig_command(settings, tmp_path / "testrig.yaml", "master", RUN_UID)

    assert text.startswith("# testrig built-in defaults")
    assert command[command.index("--repository") + 1] == f"{settings.seed_repository}@master"
    assert command[command.index("--name") + 1] == str(RUN_UID)
    assert "--non-interactive" in command
    assert "--overwrite-testrig-file" in command
    assert "--config-file" not in command


def test_command_appends_the_targets_testrig_args(settings, tmp_path):
    configured = settings.model_copy(
        update={
            "targets": [
                TargetConfig(branch="master", schedule="0 1 * * *"),
                TargetConfig(
                    branch="v14.0", schedule="0 2 * * *", testrig_args=["--patch-mode", "pinned"]
                ),
            ]
        }
    )

    pinned = build_testrig_command(configured, tmp_path / "testrig.yaml", "v14.0", RUN_UID)
    default = build_testrig_command(configured, tmp_path / "testrig.yaml", "master", RUN_UID)

    assert pinned[-2:] == ["--patch-mode", "pinned"]
    assert "--patch-mode" not in default


def test_command_uses_config_file_with_custom_config(settings, tmp_path):
    configured = settings.model_copy(
        update={"testrig_config": {"github": [], "seed_repository": {}}}
    )

    config = build_testrig_config(configured, "master")
    text = render_testrig_config(configured, "master")
    command = build_testrig_command(configured, tmp_path / "testrig.yaml", "master", RUN_UID)

    assert config["seed_repository"]["git"] == f"{configured.seed_repository}@master"
    assert not text.startswith("# testrig built-in defaults")
    assert "--config-file" in command
    assert "--repository" not in command


def test_dashboard_secrets_are_not_passed_to_testrig(
    make_target, engine, session_factory, settings, monkeypatch
):
    monkeypatch.setenv("ITD_DATABASE_URL", "postgresql://user:secret@host/db")
    make_target("master")
    fake = FakeRunner()

    dispatch_tick(engine, session_factory, settings, NOW, fake)

    assert "ITD_DATABASE_URL" not in fake.jobs[0].env
    assert fake.jobs[0].env["GH_TOKEN"] == settings.github_token.get_secret_value()


def test_failed_dispatch_records_testrigs_real_message(
    make_target, engine, session_factory, settings
):
    make_target("master")
    fake = FakeRunner(Result(1, "", "Failed to dispatch workflow: boom"))

    dispatch_tick(engine, session_factory, settings, NOW, fake)

    with session_factory() as session:
        run = session.scalar(select(Run))
    assert run.status is RunStatus.INFRA_ERROR
    assert "Failed to dispatch workflow: boom" in run.error


def test_default_run_testrig_captures_output_with_replaced_invalid_utf8(tmp_path):
    job = Job(
        command=[
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'ok\\xff')",
        ],
        config_path=tmp_path / "config.yaml",
        env=dict(os.environ),
        cwd=tmp_path,
    )

    result = default_run_testrig(job)

    assert result.returncode == 0
    assert "ok" in result.stdout
    assert "\ufffd" in result.stdout


def test_default_run_testrig_caps_output(tmp_path):
    job = Job(
        command=[sys.executable, "-c", "import sys; sys.stdout.write('x' * 2_000_000)"],
        config_path=tmp_path / "config.yaml",
        env=dict(os.environ),
        cwd=tmp_path,
    )

    result = default_run_testrig(job)

    assert result.returncode == 0
    assert len(result.stdout) <= MAX_OUTPUT_TAIL_BYTES


def test_default_run_testrig_kills_the_process_group_on_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(dispatch_module, "TIMEOUT_SECONDS", 0.2)
    job = Job(
        command=[sys.executable, "-c", "import time; time.sleep(5)"],
        config_path=tmp_path / "config.yaml",
        env=dict(os.environ),
        cwd=tmp_path,
    )

    started = time.perf_counter()
    with pytest.raises(subprocess.TimeoutExpired):
        default_run_testrig(job)
    assert time.perf_counter() - started < 3


def test_command_pins_the_github_workflow_to_the_installed_testrig(settings, tmp_path):
    configured = settings.model_copy(
        update={"testrig_config": {"github": [], "seed_repository": {}}}
    )
    pinned = dispatch_module.testrig_ref()
    for current in (settings, configured):
        command = build_testrig_command(current, tmp_path / "testrig.yaml", "master", RUN_UID)

        # The venv's own console script: nothing is downloaded at dispatch time.
        assert command[0] == dispatch_module.TESTRIG_EXECUTABLE
        assert "uvx" not in command
        # Without these, the workflow on GitHub runs testrig master.
        assert command[command.index("--testrig-branch") + 1] == pinned
        assert command[command.index("--testrig-repository") + 1] == "oarepo/invenio-testrig"


def test_testrig_ref_is_the_commit_pinned_in_pyproject():
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    pinned = tomllib.loads(pyproject.read_text())["tool"]["uv"]["sources"]["invenio-testrig"]

    assert dispatch_module.testrig_ref() == pinned["rev"]
    assert len(dispatch_module.testrig_ref()) == 40


def test_a_testrig_without_a_commit_is_refused(monkeypatch):
    class EditableDistribution:
        def read_text(self, name):
            return '{"url": "/src/invenio-testrig", "dir_info": {"editable": true}}'

    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: EditableDistribution())
    dispatch_module.testrig_ref.cache_clear()
    try:
        with pytest.raises(dispatch_module.TestrigNotPinned, match="pyproject.toml"):
            dispatch_module.testrig_ref()
    finally:
        dispatch_module.testrig_ref.cache_clear()


class _ProcessDied(BaseException):
    """Simulates the dispatch process being killed while testrig runs."""


def test_a_crash_during_dispatch_never_dispatches_the_run_twice(
    make_target, engine, session_factory, settings
):
    make_target("master")
    calls = []

    def dying_runner(job):
        calls.append(job)
        raise _ProcessDied

    with pytest.raises(_ProcessDied):
        dispatch_tick(engine, session_factory, settings, NOW, dying_runner, clock=lambda: NOW)
    dispatch_tick(engine, session_factory, settings, NOW, dying_runner, clock=lambda: NOW)

    assert len(calls) == 1
    with session_factory() as session:
        run = session.scalar(select(Run))
    # Claimed before testrig started: queued without a GitHub run, so ingest marks it lost.
    assert run.status is RunStatus.QUEUED
    assert run.dispatched_at == NOW


def test_unexpected_runner_error_fails_only_that_run(
    make_target, engine, session_factory, settings
):
    make_target("master")
    make_target("14.x")

    def runner(job):
        config = yaml.safe_load(job.config_path.read_text())
        if config["seed_repository"]["git"].endswith("@master"):
            raise PermissionError("testrig is not executable")
        return Result(0, "", "")

    summary = dispatch_tick(engine, session_factory, settings, NOW, runner)

    assert len(summary.dispatched_uids) == 1
    assert len(summary.failed_uids) == 1
    with session_factory() as session:
        errors = [run.error for run in session.scalars(select(Run)) if run.error]
    assert errors == ["dispatch failed: PermissionError: testrig is not executable"]


def test_dispatched_at_is_the_time_of_each_dispatch(make_target, engine, session_factory, settings):
    make_target("master")
    make_target("14.x")
    times = iter([NOW + timedelta(minutes=10), NOW + timedelta(minutes=25)])

    dispatch_tick(engine, session_factory, settings, NOW, FakeRunner(), clock=lambda: next(times))

    with session_factory() as session:
        runs = session.scalars(select(Run).order_by(Run.dispatched_at)).all()
    assert [run.dispatched_at for run in runs] == [
        NOW + timedelta(minutes=10),
        NOW + timedelta(minutes=25),
    ]
    assert all(run.created_at == NOW for run in runs)


def test_requested_run_of_a_disabled_target_is_not_dispatched(
    make_target, make_run, engine, session_factory, settings
):
    make_target("master")
    requested = make_run("master", NOW, status=RunStatus.REQUESTED)
    make_target("master", enabled=False)
    fake = FakeRunner()

    summary = dispatch_tick(engine, session_factory, settings, NOW, fake)

    assert fake.jobs == []
    assert summary.failed_uids == (str(requested.uid),)
    with session_factory() as session:
        run = session.get(Run, requested.id)
    assert run.status is RunStatus.INFRA_ERROR
    assert run.error == "target disabled before dispatch"


def test_testrig_never_opens_a_browser(make_target, engine, session_factory, settings):
    make_target("master")
    fake = FakeRunner()

    dispatch_tick(engine, session_factory, settings, NOW, fake)

    assert fake.jobs[0].env["BROWSER"] == "true"


def test_testrig_can_commit_without_a_git_identity(
    make_target, engine, session_factory, settings, tmp_path
):
    """The container user has no ~/.gitconfig; testrig's `git commit` must still work."""
    make_target("master")
    fake = FakeRunner()
    dispatch_tick(engine, session_factory, settings, NOW, fake)
    env = dict(fake.jobs[0].env)
    # Like the image: an empty home and no system config, so only the environment can help.
    env.update(HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    repo = tmp_path / "runner"
    repo.mkdir()
    git_executable = shutil.which("git")
    assert git_executable is not None

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed arguments, test-only
            [git_executable, *args], cwd=repo, env=env, capture_output=True, text=True
        )

    git("init", "-q")
    (repo / "testrig.yml").write_text("on: workflow_dispatch\n")
    git("add", ".")
    commit = git("commit", "-q", "-m", "Update testrig.yml workflow")

    assert commit.returncode == 0, commit.stderr
    assert git("log", "-1", "--format=%an <%ae>").stdout.strip() == (
        "invenio-testrig-dashboard <invenio-testrig-dashboard@noreply.invalid>"
    )


def test_testrig_can_push_over_https_without_a_credential_config(
    make_target, engine, session_factory, settings, tmp_path
):
    """`git push` after `gh repo clone` must find the token: git asks gh's credential helper."""
    make_target("master")
    fake = FakeRunner()
    dispatch_tick(engine, session_factory, settings, NOW, fake)
    env = dict(fake.jobs[0].env)
    # Like the image: no git config files at all. A stand-in `gh` answers like
    # `gh auth git-credential get` does, from the GH_TOKEN dispatch passes.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        "#!/bin/sh\n"
        '[ "$1 $2 $3" = "auth git-credential get" ] || exit 1\n'
        "printf 'username=x-access-token\\npassword=%s\\n' \"$GH_TOKEN\"\n"
    )
    fake_gh.chmod(0o755)
    env.update(
        HOME=str(tmp_path),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        PATH=f"{bin_dir}{os.pathsep}{env['PATH']}",
    )
    git_executable = shutil.which("git")
    assert git_executable is not None

    filled = subprocess.run(  # noqa: S603 - fixed arguments, test-only
        [git_executable, "credential", "fill"],
        input="protocol=https\nhost=github.com\npath=org/runner.git\n\n",
        env=env,
        capture_output=True,
        text=True,
    )

    assert filled.returncode == 0, filled.stderr
    assert f"password={settings.github_token.get_secret_value()}" in filled.stdout.splitlines()


def test_git_config_from_the_environment_is_appended_not_replaced(monkeypatch, settings):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "http.proxy")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "http://proxy.example:3128")

    env = dispatch_module._testrig_env(settings)

    assert env["GIT_CONFIG_COUNT"] == "3"
    assert (env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]) == (
        "http.proxy",
        "http://proxy.example:3128",
    )
    assert env["GIT_CONFIG_VALUE_2"] == "!gh auth git-credential"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_an_operator_git_identity_wins(make_target, engine, session_factory, settings, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Ops Bot")
    make_target("master")
    fake = FakeRunner()

    dispatch_tick(engine, session_factory, settings, NOW, fake)

    assert fake.jobs[0].env["GIT_AUTHOR_NAME"] == "Ops Bot"
    assert fake.jobs[0].env["GIT_COMMITTER_NAME"] == "invenio-testrig-dashboard"


def test_default_run_testrig_kills_runaway_output(monkeypatch, tmp_path):
    monkeypatch.setattr(dispatch_module, "MAX_OUTPUT_BYTES", 1_000_000)
    monkeypatch.setattr(dispatch_module, "POLL_SECONDS", 0.05)
    job = Job(
        command=[
            sys.executable,
            "-c",
            "import sys\nwhile True: sys.stdout.write('x' * 65536); sys.stdout.flush()",
        ],
        config_path=tmp_path / "config.yaml",
        env=dict(os.environ),
        cwd=tmp_path,
    )

    started = time.perf_counter()
    with pytest.raises(dispatch_module.OutputTooLarge, match="output exceeded"):
        default_run_testrig(job)
    assert time.perf_counter() - started < 5


def test_runaway_output_marks_the_run_infra_error(make_target, engine, session_factory, settings):
    make_target("master")

    def runner(job):
        raise dispatch_module.OutputTooLarge(50 * 1024 * 1024, "last words")

    dispatch_tick(engine, session_factory, settings, NOW, runner)

    with session_factory() as session:
        run = session.scalar(select(Run))
    assert run.status is RunStatus.INFRA_ERROR
    assert run.error == "testrig output exceeded 50 MB: last words"


def test_failed_scheduled_dispatch_is_retried_on_the_next_ticks(
    make_target, engine, session_factory, settings
):
    make_target("master")
    calls = []

    def failing(job):
        calls.append(job)
        return Result(1, "", "github is down")

    for tick in range(MAX_DISPATCH_ATTEMPTS + 2):  # every 15 min, within the same fire
        dispatch_tick(
            engine, session_factory, settings, NOW + timedelta(minutes=15 * tick), failing
        )

    assert len(calls) == MAX_DISPATCH_ATTEMPTS
    with session_factory() as session:
        statuses = [run.status for run in session.scalars(select(Run))]
    assert statuses == [RunStatus.INFRA_ERROR] * MAX_DISPATCH_ATTEMPTS

    # A retry that works ends it; nothing more for this fire.
    dispatch_tick(engine, session_factory, settings, NOW + timedelta(days=1), FakeRunner())
    dispatch_tick(
        engine, session_factory, settings, NOW + timedelta(days=1, minutes=15), FakeRunner()
    )
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Run)) == MAX_DISPATCH_ATTEMPTS + 1


@pytest.mark.parametrize(
    ("status", "gh_run_id"),
    [
        (RunStatus.INFRA_ERROR, 123),  # the workflow ran, but its results were unusable
        (RunStatus.LOST, None),  # dispatch may have started a workflow nobody found
        (RunStatus.COMPLETED, 123),
    ],
)
def test_runs_that_got_past_dispatch_are_not_retried(
    make_target, make_run, engine, session_factory, settings, status, gh_run_id
):
    make_target("master")
    make_run(
        "master",
        datetime(2026, 11, 13, 1, 0, tzinfo=UTC),
        status=status,
        trigger=RunTrigger.SCHEDULE,
        gh_run_id=gh_run_id,
    )
    fake = FakeRunner()

    summary = dispatch_tick(engine, session_factory, settings, NOW, fake)

    assert summary.created_branches == ()
    assert fake.jobs == []


def test_testrig_runs_in_a_private_temp_dir_that_is_removed_afterwards(
    make_target, engine, session_factory, settings
):
    make_target("master")
    seen = {}

    def runner(job):
        # testrig clones the runner repo into its cwd: simulate that.
        (job.cwd / ".tmp_invenio_testrig_client_update").mkdir()
        seen["cwd"] = job.cwd
        seen["config_inside_cwd"] = job.config_path.parent == job.cwd
        seen["not_the_dashboards_cwd"] = job.cwd.resolve() != Path.cwd().resolve()
        return Result(0, "", "")

    dispatch_tick(engine, session_factory, settings, NOW, runner)

    assert seen["config_inside_cwd"] and seen["not_the_dashboards_cwd"]
    assert not seen["cwd"].exists()  # removed, clone included
    assert not (Path.cwd() / ".tmp_invenio_testrig_client_update").exists()


def test_the_temp_dir_is_removed_when_dispatch_is_interrupted(
    make_target, engine, session_factory, settings
):
    make_target("master")
    seen = {}

    def interrupted(job):
        seen["cwd"] = job.cwd
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        dispatch_tick(engine, session_factory, settings, NOW, interrupted)

    assert not seen["cwd"].exists()


def test_default_run_testrig_uses_the_job_cwd(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    job = Job(
        command=[sys.executable, "-c", "open('marker', 'w').write('x')"],
        config_path=work / "config.yaml",
        env=dict(os.environ),
        cwd=work,
    )

    assert default_run_testrig(job).returncode == 0
    assert (work / "marker").exists()


def test_interrupting_dispatch_kills_testrig(tmp_path):
    """testrig runs in its own session, so Ctrl+C never reaches it: dispatch must kill it."""
    pid_file = tmp_path / "pid"
    job = Job(
        command=[
            sys.executable,
            "-c",
            "import os, time; open('pid', 'w').write(str(os.getpid())); time.sleep(30)",
        ],
        config_path=tmp_path / "config.yaml",
        env=dict(os.environ),
        cwd=tmp_path,
    )
    timer = threading.Timer(
        2.0, os.kill, (os.getpid(), signal.SIGINT)
    )  # after the child wrote its pid
    timer.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            default_run_testrig(job)
    finally:
        timer.cancel()

    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # gone, not orphaned


def test_sigterm_becomes_a_normal_exit():
    from invenio_testrig_dashboard.cli import _exit_on_sigterm

    with pytest.raises(SystemExit) as excinfo:
        _exit_on_sigterm(signal.SIGTERM, None)
    assert excinfo.value.code == 128 + signal.SIGTERM


def test_command_never_lets_testrig_create_the_runner_repository(settings, tmp_path):
    command = build_testrig_command(settings, tmp_path / "testrig.yaml", "master", uuid.uuid4())

    assert "--no-create-repository" in command
