import contextlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import delete, func, select
from typer.testing import CliRunner

from invenio_testrig_dashboard import alerts
from invenio_testrig_dashboard import dispatch as dispatch_module
from invenio_testrig_dashboard import github as github_module
from invenio_testrig_dashboard.cli import STUB_COMMANDS, app
from invenio_testrig_dashboard.dispatch import TestrigResult as Result
from invenio_testrig_dashboard.github import GitHub
from invenio_testrig_dashboard.models import PackageResult, Run, Target, TestResult
from invenio_testrig_dashboard.status import RunStatus, RunTrigger

ENV = {
    "ITD_SEED_REPOSITORY": "oarepo/invenio-rdm-seed",
    "ITD_RUNNER_REPOSITORY": "oarepo/invenio-testrig-dashboard-runner",
    "ITD_TESTRIG_REF": "v1.0.0",
    "ITD_S3_BUCKET": "invenio-testrig-dashboard-test",
    "ITD_DATABASE_URL": "postgresql://dbuser:dbpass@localhost:25432/db",
    "ITD_GITHUB_TOKEN": "ghp_verysecret",
    "ITD_S3_ACCESS_KEY": "AKIAVERYSECRET",
    "ITD_S3_SECRET_KEY": "supersecretkey",
}

runner = CliRunner()


@pytest.fixture
def valid_config(monkeypatch):
    # conftest removes exported ITD_* vars and isolates the cwd; supply everything via env.
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


def _text(result) -> str:
    return result.output + result.stderr


def test_help_lists_subcommands():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for name in (
        "migrate-and-sync",
        "parse-dir",
        "ingest-dir",
        "dispatch",
        "ingest",
        "web",
        "export-openapi",
        "backup",
        *STUB_COMMANDS,
        "check-config",
    ):
        assert name in result.output


def test_subcommand_help_does_not_need_config():
    result = runner.invoke(app, ["dispatch", "--help"])

    assert result.exit_code == 0
    assert "dispatch" in result.output


def test_check_config_valid(valid_config):
    result = runner.invoke(app, ["check-config"])

    assert result.exit_code == 0
    assert "seed_repository" in result.output
    assert "ghp_verysecret" not in _text(result)
    assert "**********" in result.output


def test_check_config_missing_field_reports_type(valid_config, monkeypatch):
    monkeypatch.delenv("ITD_SEED_REPOSITORY")

    result = runner.invoke(app, ["check-config"])

    assert result.exit_code == 1
    assert "type=missing" in _text(result)


def test_check_config_keeps_brackets_literal(valid_config, monkeypatch):
    monkeypatch.setenv("ITD_MAINTAINERS", '["[team]"]')

    result = runner.invoke(app, ["check-config"])

    assert result.exit_code == 0
    assert "[team]" in result.output


def test_parse_dir_prints_bracketed_names_from_real_fixture():
    fixture = Path(__file__).parent / "fixtures" / "runs" / "real-2026-11-13"

    result = runner.invoke(app, ["parse-dir", str(fixture), "--tests"])

    assert result.exit_code == 0
    assert "test_get_record[app0]" in _text(result)
    assert "invenio-records-rest" in result.output


def test_parse_dir_reports_invalid_results():
    fixture = Path(__file__).parent / "fixtures" / "runs" / "bad_status"

    result = runner.invoke(app, ["parse-dir", str(fixture)])

    assert result.exit_code == 1
    assert "exploded" in _text(result)


def test_any_command_reports_invalid_config_readably():
    result = runner.invoke(app, ["dispatch"])

    text = _text(result)
    assert result.exit_code == 1
    assert "Invalid configuration" in text
    assert "Traceback" not in text


def test_duplicate_branch_error_hides_secrets(valid_config, tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(
        "targets:\n"
        "  - {branch: master, schedule: '0 1 * * *'}\n"
        "  - {branch: master, schedule: '0 2 * * *'}\n"
    )
    monkeypatch.setenv("ITD_S3_SECRET_KEY", "s3TOPSECRET")

    result = runner.invoke(app, ["dispatch"])

    text = _text(result)
    assert result.exit_code == 1
    assert "duplicate target branch" in text
    assert "s3TOPSECRET" not in text


def test_malformed_yaml_is_reported_without_traceback(valid_config, tmp_path):
    (tmp_path / "config.yaml").write_text("- not\n- a mapping\n")

    result = runner.invoke(app, ["check-config"])

    text = _text(result)
    assert result.exit_code == 1
    assert "must contain a mapping" in text
    assert "Traceback" not in text


def test_migrate_and_sync_twice(db_url, engine, monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ITD_DATABASE_URL", db_url)
    monkeypatch.setenv("ITD_TARGETS", '[{"branch": "cli-test", "schedule": "0 1 * * *"}]')

    try:
        first = runner.invoke(app, ["migrate-and-sync"])
        second = runner.invoke(app, ["migrate-and-sync"])
    finally:
        # The command commits, so the session fixture's rollback can't undo it.
        with engine.begin() as connection:
            connection.execute(delete(Target))

    assert first.exit_code == 0, first.output
    assert "1 added" in first.output
    assert second.exit_code == 0, second.output
    assert re.search(r"Migrations: (\d+) -> \1\b", second.output), second.output
    assert "0 added, 0 updated, 0 disabled" in second.output


def test_ingest_dir_cli_real_fixture(db_url, session, session_factory, s3, monkeypatch):
    with session_factory() as db, db.begin():
        db.add(Target(branch="master", schedule="0 1 * * *", enabled=True))

    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ITD_DATABASE_URL", db_url)
    monkeypatch.setenv("ITD_S3_ENDPOINT_URL", "http://localhost:29000")
    monkeypatch.setenv("ITD_S3_ACCESS_KEY", "devaccess")
    monkeypatch.setenv("ITD_S3_SECRET_KEY", "devsecret")
    fixture = Path(__file__).parent / "fixtures" / "runs" / "real-2026-11-13"

    result = runner.invoke(app, ["ingest-dir", str(fixture), "--target", "master"])

    assert result.exit_code == 0, result.output
    assert "completed" in result.output
    assert session.scalar(select(func.count()).select_from(PackageResult)) == 55
    assert session.scalar(select(func.count()).select_from(TestResult)) == 10985


def test_dispatch_dry_run_cli(db_url, session, session_factory, make_target, monkeypatch):
    make_target("master")
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ITD_DATABASE_URL", db_url)
    called = []
    monkeypatch.setattr(dispatch_module, "default_run_testrig", called.append)

    result = runner.invoke(app, ["dispatch", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "due: master" in result.output
    assert called == []
    assert session.scalar(select(func.count()).select_from(Run)) == 0


def test_dispatch_cli_dispatches_a_run(db_url, session, session_factory, make_target, monkeypatch):
    make_target("master")
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ITD_DATABASE_URL", db_url)
    monkeypatch.setattr(dispatch_module, "default_run_testrig", lambda job: Result(0, "", ""))

    result = runner.invoke(app, ["dispatch"])

    assert result.exit_code == 0, result.output
    assert "dispatched: " in result.output
    runs = session.scalars(select(Run)).all()
    assert len(runs) == 1
    assert runs[0].status is RunStatus.QUEUED


def test_dispatch_cli_exits_zero_even_when_a_run_fails(
    db_url, session, session_factory, make_target, monkeypatch
):
    make_target("master")
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ITD_DATABASE_URL", db_url)
    monkeypatch.setattr(dispatch_module, "default_run_testrig", lambda job: Result(1, "", "boom"))

    result = runner.invoke(app, ["dispatch"])

    assert result.exit_code == 0, result.output
    assert "failed: " in result.output
    assert session.scalar(select(Run)).status is RunStatus.INFRA_ERROR


def test_dispatch_cli_reports_a_running_tick(
    db_url, session, session_factory, engine, make_target, monkeypatch
):
    make_target("master")
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ITD_DATABASE_URL", db_url)
    called = []
    monkeypatch.setattr(dispatch_module, "default_run_testrig", called.append)

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        assert connection.scalar(
            select(func.pg_try_advisory_lock(dispatch_module.DISPATCH_LOCK_KEY))
        )
        try:
            result = runner.invoke(app, ["dispatch"])
        finally:
            connection.execute(select(func.pg_advisory_unlock(dispatch_module.DISPATCH_LOCK_KEY)))

    assert result.exit_code == 0, result.output
    assert "another dispatch is running" in _text(result)
    assert called == []
    assert session.scalar(select(func.count()).select_from(Run)) == 0


def _ingest_env(monkeypatch, db_url):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ITD_DATABASE_URL", db_url)
    monkeypatch.setenv("ITD_S3_ENDPOINT_URL", "http://localhost:29000")
    monkeypatch.setenv("ITD_S3_ACCESS_KEY", "devaccess")
    monkeypatch.setenv("ITD_S3_SECRET_KEY", "devsecret")


def test_ingest_dir_exits_nonzero_on_infra_error(db_url, session, session_factory, s3, monkeypatch):
    with session_factory() as db, db.begin():
        db.add(Target(branch="master", schedule="0 1 * * *", enabled=True))
    _ingest_env(monkeypatch, db_url)
    fixture = Path(__file__).parent / "fixtures" / "runs" / "bad_schema"

    result = runner.invoke(app, ["ingest-dir", str(fixture), "--target", "master"])

    assert result.exit_code == 1
    assert "infra_error" in result.output


def test_ingest_dir_rejects_uid_of_another_target(
    db_url, session, session_factory, s3, monkeypatch
):
    with session_factory() as db, db.begin():
        db.add_all(
            [
                Target(branch="master", schedule="0 1 * * *", enabled=True),
                Target(branch="14.x", schedule="0 1 * * *", enabled=True),
            ]
        )
    _ingest_env(monkeypatch, db_url)
    fixture = Path(__file__).parent / "fixtures" / "runs" / "ok"
    first = runner.invoke(app, ["ingest-dir", str(fixture), "--target", "master"])
    uid = first.output.split()[1].rstrip(":")

    result = runner.invoke(app, ["ingest-dir", str(fixture), "--target", "14.x", "--uid", uid])

    assert first.exit_code == 0, first.output
    assert result.exit_code == 1
    assert "belongs to another target" in _text(result)


def test_ingest_cli_polls_a_queued_run(
    db_url, session, session_factory, settings, make_run, monkeypatch
):
    run = make_run(
        status=RunStatus.QUEUED,
        gh_run_id=1,
        dispatched_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    _ingest_env(monkeypatch, db_url)

    def handler(request):
        return httpx2.Response(
            200,
            json={"id": 1, "status": "in_progress", "run_started_at": None},
        )

    github_client = GitHub(
        "token", settings.runner_repository, transport=httpx2.MockTransport(handler)
    )
    monkeypatch.setattr(github_module, "make_github_client", lambda settings: github_client)

    result = runner.invoke(app, ["ingest"])

    assert result.exit_code == 0, result.output
    assert "Active: 1" in result.output
    assert session.get(Run, run.id).status is RunStatus.RUNNING


def test_ingest_cli_exits_zero_when_a_run_fails(
    db_url, session, session_factory, settings, make_run, monkeypatch
):
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    _ingest_env(monkeypatch, db_url)

    def handler(request):
        if request.url.path.endswith("/artifacts"):
            return httpx2.Response(200, json={"total_count": 0, "artifacts": []})
        return httpx2.Response(
            200,
            json={"id": 1, "status": "completed", "conclusion": "cancelled"},
        )

    github_client = GitHub(
        "token", settings.runner_repository, transport=httpx2.MockTransport(handler)
    )
    monkeypatch.setattr(github_module, "make_github_client", lambda settings: github_client)

    result = runner.invoke(app, ["ingest"])

    assert result.exit_code == 0, result.output
    assert "infra_error: 1" in result.output
    assert session.get(Run, run.id).status is RunStatus.INFRA_ERROR


def test_crash_wrapper_posts_once_and_exits_one(db_url, session, monkeypatch):
    _ingest_env(monkeypatch, db_url)
    sent: list[str] = []
    monkeypatch.setattr(
        alerts.discord,
        "post",
        lambda webhook_url, content, transport=None: sent.append(content) or True,
    )

    def crash(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(dispatch_module, "dispatch_tick", crash)

    first = runner.invoke(app, ["dispatch"])
    second = runner.invoke(app, ["dispatch"])

    assert first.exit_code == 1
    assert second.exit_code == 1
    assert len(sent) == 1
    assert "dispatch crashed" in sent[0]
    assert "RuntimeError" in sent[0]


def test_ingest_auth_error_exits_1(db_url, session, monkeypatch):
    # The tick posts the github-auth alert (tests/test_ingest_github.py); the command maps it to exit 1.
    _ingest_env(monkeypatch, db_url)

    def auth_error(*args, **kwargs):
        raise github_module.GitHubAuthError("rejected")

    monkeypatch.setattr("invenio_testrig_dashboard.cli.ingest_tick", auth_error)

    result = runner.invoke(app, ["ingest"])

    assert result.exit_code == 1
    assert "GitHub authentication failed" in _text(result)


def test_ingest_dir_does_not_alert_by_default(db_url, session, session_factory, s3, monkeypatch):
    with session_factory() as db, db.begin():
        db.add(Target(branch="master", schedule="0 1 * * *", enabled=True))
        db.add(Target(branch="14.x", schedule="0 1 * * *", enabled=True))
    _ingest_env(monkeypatch, db_url)
    sent: list[str] = []
    monkeypatch.setattr(
        alerts.discord, "post", lambda url, content, transport=None: sent.append(content) or True
    )
    fixture = Path(__file__).parent / "fixtures" / "runs" / "failing"

    for _ in range(2):  # two failing runs: each would notify, but not without --alert
        assert runner.invoke(app, ["ingest-dir", str(fixture), "--target", "master"]).exit_code == 0
    assert sent == []

    # With --alert, every failing run posts: new failures, then still failing.
    for _ in range(2):
        result = runner.invoke(app, ["ingest-dir", str(fixture), "--target", "14.x", "--alert"])
        assert result.exit_code == 0, result.output
    assert len(sent) == 2
    assert "New:" in sent[0] and "Still failing:" in sent[1]


def test_check_config_warns_without_a_discord_webhook(valid_config):
    result = runner.invoke(app, ["check-config"])

    assert result.exit_code == 0
    assert "discord_webhook_url is not set" in _text(result)


OK = Result(0, "", "")


def _dispatch_env(monkeypatch, db_url, result=OK):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ITD_DATABASE_URL", db_url)
    jobs = []
    monkeypatch.setattr(
        dispatch_module, "default_run_testrig", lambda job: jobs.append(job) or result
    )
    return jobs


def test_dispatch_target_ignores_the_used_up_scheduled_attempts(
    db_url, session, make_target, make_run, monkeypatch
):
    make_target("master")
    for _ in range(dispatch_module.MAX_DISPATCH_ATTEMPTS):  # today's scheduled attempts, all failed
        make_run(
            "master", datetime.now(UTC), status=RunStatus.INFRA_ERROR, trigger=RunTrigger.SCHEDULE
        )
    jobs = _dispatch_env(monkeypatch, db_url)

    assert runner.invoke(app, ["dispatch"]).exit_code == 0
    assert jobs == []  # the plain tick respects the cap

    result = runner.invoke(app, ["dispatch", "master"])

    assert result.exit_code == 0, result.output
    assert "requested: " in result.output and "dispatched: " in result.output
    assert len(jobs) == 1
    run = session.scalar(select(Run).where(Run.trigger == RunTrigger.MANUAL))
    assert run.status is RunStatus.QUEUED
    assert run.triggered_by == "cli"


def test_dispatch_target_leaves_other_targets_alone(db_url, session, make_target, monkeypatch):
    make_target("master")
    make_target("other")  # new, so due
    jobs = _dispatch_env(monkeypatch, db_url)

    result = runner.invoke(app, ["dispatch", "master"])

    assert result.exit_code == 0, result.output
    assert len(jobs) == 1
    branches = session.scalars(select(Target.branch).join(Run, Run.target_id == Target.id)).all()
    assert branches == ["master"]


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("unknown", "unknown target 'master'"),
        ("disabled", "target 'master' is disabled"),
        ("active", "already has an active run"),
    ],
)
def test_dispatch_target_is_rejected(
    db_url, session, make_target, make_run, monkeypatch, setup, message
):
    if setup == "disabled":
        make_target("master", enabled=False)
    elif setup == "active":
        make_target("master")
        make_run("master", status=RunStatus.QUEUED)
    jobs = _dispatch_env(monkeypatch, db_url)

    result = runner.invoke(app, ["dispatch", "master"])

    assert result.exit_code == 1
    assert message in _text(result)
    assert jobs == []


def test_dispatch_target_dry_run_changes_nothing(db_url, session, make_target, monkeypatch):
    make_target("master")
    jobs = _dispatch_env(monkeypatch, db_url)

    result = runner.invoke(app, ["dispatch", "master", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "would request a manual run of master" in result.output
    assert jobs == []
    assert session.scalar(select(func.count()).select_from(Run)) == 0


def test_dispatch_target_exits_1_when_its_dispatch_fails(db_url, session, make_target, monkeypatch):
    make_target("master")
    _dispatch_env(monkeypatch, db_url, result=Result(1, "", "boom"))

    result = runner.invoke(app, ["dispatch", "master"])

    assert result.exit_code == 1
    assert "failed: " in result.output


def test_check_config_lists_targets_in_their_own_table(valid_config, monkeypatch):
    monkeypatch.setenv(
        "ITD_TARGETS",
        '[{"branch": "master", "schedule": "0 1 * * *"},'
        ' {"branch": "seed-trial", "schedule": "0 1 * * *", "enabled": false}]',
    )

    result = runner.invoke(app, ["check-config"], env={"COLUMNS": "120"})

    assert result.exit_code == 0, result.output
    assert "2 (see Targets below)" in result.output
    lines = result.output.splitlines()
    master = next(line for line in lines if "│ master" in line)
    assert "0 1 * * *" in master and "yes" in master  # one line per target
    trial = next(line for line in lines if "seed-trial" in line)
    assert "no" in trial and "-" in trial  # disabled: no next fire


def test_export_openapi_cli(valid_config, tmp_path):
    output = tmp_path / "schema.json"

    result = runner.invoke(app, ["export-openapi", "--output", str(output)])

    assert result.exit_code == 0, result.output
    schema = json.loads(output.read_text())
    assert "/api/health" in schema["paths"]
    assert "/api/runs/{uid}/tests" in schema["paths"]


def test_web_cli_starts_uvicorn(valid_config, monkeypatch):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    result = runner.invoke(app, ["web", "--host", "127.0.0.1", "--port", "1234"])

    assert result.exit_code == 0, result.output
    assert calls
    assert calls[0][1]["host"] == "127.0.0.1"
    assert calls[0][1]["port"] == 1234


def test_migrate_and_sync_gives_up_when_the_database_never_answers(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    # Port 1 on localhost: nothing listens, so every attempt is refused at once.
    monkeypatch.setenv("ITD_DATABASE_URL", "postgresql://u:hunter2-secret@127.0.0.1:1/db")
    monkeypatch.setattr("invenio_testrig_dashboard.db.time.sleep", lambda _: None)

    result = runner.invoke(app, ["migrate-and-sync", "--wait-for-db", "0.2"])

    text = _text(result)
    assert result.exit_code == 1
    assert "database not reachable after 0.2 s" in text
    assert "hunter2-secret" not in text
    assert "Traceback" not in text


def test_wait_for_database_retries_until_the_database_answers(monkeypatch):
    from sqlalchemy.exc import OperationalError

    from invenio_testrig_dashboard import db as db_module

    attempts = []

    class FlakyEngine:
        def connect(self):
            attempts.append(1)
            if len(attempts) < 3:
                raise OperationalError("SELECT 1", {}, Exception("connection refused"))
            return contextlib.nullcontext()

    monkeypatch.setattr(db_module.time, "sleep", lambda _: None)

    db_module.wait_for_database(FlakyEngine(), timeout=60)

    assert len(attempts) == 3


def test_version_option_needs_no_config():
    from invenio_testrig_dashboard import __version__

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_a_silent_database_cannot_stall_the_wait(settings, monkeypatch, caplog):
    """A connection that never gets an answer must time out, so the wait retries."""
    import logging
    import socket
    import threading
    import time

    from pydantic import SecretStr

    from invenio_testrig_dashboard import db as db_module

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(16)  # accepts TCP, never speaks the Postgres protocol
    port = server.getsockname()[1]
    stop = threading.Event()
    accepted = []

    def accept_and_ignore():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                accepted.append(server.accept()[0])
            except OSError:
                pass

    thread = threading.Thread(target=accept_and_ignore, daemon=True)
    thread.start()
    monkeypatch.setattr(db_module, "CONNECT_TIMEOUT_SECONDS", 1)  # libpq rounds it up to 2 s
    silent = settings.model_copy(
        update={"database_url": SecretStr(f"postgresql://user:dead-password@127.0.0.1:{port}/db")}
    )
    engine = db_module.make_engine(silent)
    try:
        started = time.monotonic()
        with caplog.at_level(logging.INFO), pytest.raises(db_module.DatabaseUnavailable):
            db_module.wait_for_database(engine, timeout=7, interval=0.5)
        elapsed = time.monotonic() - started
    finally:
        engine.dispose()
        stop.set()
        thread.join()
        server.close()
        for connection in accepted:
            connection.close()

    assert elapsed < 12  # without connect_timeout this blocks until the server goes away
    waits = [r for r in caplog.records if r.getMessage().startswith("waiting for the database")]
    assert len(waits) >= 2  # it really retried
