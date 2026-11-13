"""Postgres backups to S3: their own bucket, retention, the pg_dump/pg_restore round trip."""

import json
import os
import re
import shutil
import stat
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from typer.testing import CliRunner

from invenio_testrig_dashboard import backup as backup_module
from invenio_testrig_dashboard.backup import (
    BackupError,
    backup_key,
    dump,
    key_time,
    pg_environment,
    prune,
    run_backup,
)
from invenio_testrig_dashboard.cli import app
from invenio_testrig_dashboard.models import Run, TestResult
from invenio_testrig_dashboard.settings import Settings
from invenio_testrig_dashboard.storage import make_s3_client

NOW = datetime(2026, 11, 15, 3, 0, 7, tzinfo=UTC)
PASSWORD = "s3cr3t-db-pass"


def _empty(client, bucket: str) -> None:
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for item in page.get("Contents", []):
            client.delete_object(Bucket=bucket, Key=item["Key"])


@pytest.fixture
def backup_s3(settings):
    client = make_s3_client(settings)
    _empty(client, settings.s3_backup_bucket)
    yield client
    _empty(client, settings.s3_backup_bucket)


@pytest.fixture
def fake_pg_dump(tmp_path, monkeypatch):
    """A pg_dump on PATH that records its argv/environment and writes a small dump.

    Behaviour is set per test through FAKE_PG_* variables in the fake's own environment file.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "pg_dump-call.json"
    control = tmp_path / "pg_dump-control.json"
    control.write_text(json.dumps({"version": "18.7", "exit": 0, "stderr": "", "write": True}))
    script = bin_dir / "pg_dump"
    script.write_text(
        f"""#!/usr/bin/env python3
import json, os, sys
control = json.load(open({str(control)!r}))
if sys.argv[1:] == ["--version"]:
    print(f"pg_dump (PostgreSQL) {{control['version']}}")
    sys.exit(0)
env = {{k: v for k, v in os.environ.items() if k.startswith("PG")}}
json.dump({{"argv": sys.argv[1:], "env": env}}, open({str(record)!r}, "w"))
if control["write"]:
    path = sys.argv[sys.argv.index("--file") + 1]
    open(path, "wb").write(b"PGDMP fake dump " * 64)
sys.stderr.write(control["stderr"])
sys.exit(control["exit"])
"""
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    class Fake:
        def set(self, **values):
            current = json.loads(control.read_text())
            control.write_text(json.dumps({**current, **values}))

        def call(self) -> dict:
            return json.loads(record.read_text())

    return Fake()


def _settings_with_password(settings: Settings) -> Settings:
    url = make_url(settings.database_url.get_secret_value()).set(password=PASSWORD)
    return settings.model_copy(
        update={
            "database_url": type(settings.database_url)(url.render_as_string(hide_password=False))
        }
    )


# --- keys -------------------------------------------------------------------------------


def test_backup_key_is_dated_utc():
    local = NOW.astimezone(datetime.now().astimezone().tzinfo)
    assert backup_key(local) == "2026/11/15/invenio-testrig-dashboard-030007.dump"
    assert key_time(backup_key(NOW)) == NOW


@pytest.mark.parametrize(
    "key",
    [
        "notes.txt",
        "2026/11/15/other-030007.dump",
        "2026/13/15/invenio-testrig-dashboard-030007.dump",  # month 13
        "backups/2026/11/15/invenio-testrig-dashboard-030007.dump",
        "2026/11/15/invenio-testrig-dashboard-030007.dump.tmp",
    ],
)
def test_foreign_keys_have_no_time(key):
    assert key_time(key) is None


# --- settings ---------------------------------------------------------------------------


def test_backup_bucket_must_differ_from_the_artifacts_bucket(settings):
    with pytest.raises(ValidationError, match="s3_backup_bucket must differ from s3_bucket"):
        Settings(**{**settings.model_dump(), "s3_backup_bucket": settings.s3_bucket})


def test_retention_must_be_positive(settings):
    with pytest.raises(ValidationError, match="backup_retention_days"):
        Settings(**{**settings.model_dump(), "backup_retention_days": 0})


# --- pg_dump ----------------------------------------------------------------------------


def test_the_password_goes_to_the_environment_not_the_command_line(
    settings, fake_pg_dump, tmp_path, monkeypatch
):
    monkeypatch.setenv("PGSERVICE", "somewhere-else")  # an inherited libpq variable
    settings = _settings_with_password(settings)

    dump(settings, tmp_path / "out.dump")

    call = fake_pg_dump.call()
    assert PASSWORD not in " ".join(call["argv"])
    assert call["argv"][:2] == ["--format=custom", "--no-password"]
    url = make_url(settings.database_url.get_secret_value())
    assert call["env"] == {
        "PGHOST": url.host,
        "PGPORT": str(url.port),
        "PGUSER": url.username,
        "PGPASSWORD": PASSWORD,
        "PGDATABASE": url.database,
    }
    assert (tmp_path / "out.dump").stat().st_size > 0


def test_sslmode_is_passed_to_libpq(settings):
    url = make_url(settings.database_url.get_secret_value()).update_query_dict(
        {"sslmode": "require"}
    )
    settings = settings.model_copy(
        update={
            "database_url": type(settings.database_url)(url.render_as_string(hide_password=False))
        }
    )
    assert pg_environment(settings)["PGSSLMODE"] == "require"


def test_an_old_pg_dump_is_refused(settings, fake_pg_dump, tmp_path):
    fake_pg_dump.set(version="16.4")
    with pytest.raises(BackupError, match="pg_dump 16 is too old"):
        dump(settings, tmp_path / "out.dump")


def test_a_missing_pg_dump_is_a_clear_error(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # no pg_dump anywhere
    with pytest.raises(BackupError, match="pg_dump not found"):
        dump(settings, tmp_path / "out.dump")


def test_a_failing_pg_dump_is_redacted(settings, fake_pg_dump, tmp_path):
    settings = _settings_with_password(settings)
    fake_pg_dump.set(exit=1, write=False, stderr=f'password authentication failed: "{PASSWORD}"\n')

    with pytest.raises(BackupError) as excinfo:
        dump(settings, tmp_path / "out.dump")

    message = str(excinfo.value)
    assert "pg_dump failed (exit 1)" in message
    assert "password authentication failed" in message
    assert PASSWORD not in message


def test_an_empty_dump_is_a_failure(settings, fake_pg_dump, tmp_path):
    fake_pg_dump.set(write=False)
    with pytest.raises(BackupError, match="wrote no dump"):
        dump(settings, tmp_path / "out.dump")


# --- prune (real S3) --------------------------------------------------------------------


def _put(client, bucket: str, key: str) -> None:
    client.put_object(Bucket=bucket, Key=key, Body=b"x")


def _keys(client, bucket: str) -> set[str]:
    response = client.list_objects_v2(Bucket=bucket)
    return {item["Key"] for item in response.get("Contents", [])}


def test_prune_deletes_only_our_expired_dumps(settings, backup_s3):
    bucket = settings.s3_backup_bucket
    expired = backup_key(NOW - timedelta(days=31))
    kept_recent = backup_key(NOW - timedelta(days=29))
    on_the_edge = backup_key(NOW - timedelta(days=30))  # exactly the retention: not older
    foreign = ["notes.txt", "2020/01/01/someone-else-000000.dump"]
    for key in (expired, kept_recent, on_the_edge, *foreign):
        _put(backup_s3, bucket, key)

    deleted = prune(backup_s3, bucket, NOW, retention_days=30, keep="none")

    assert deleted == [expired]
    assert _keys(backup_s3, bucket) == {kept_recent, on_the_edge, *foreign}


def test_prune_never_deletes_the_dump_it_just_wrote(settings, backup_s3):
    # A wrong clock must not delete the only fresh dump.
    bucket = settings.s3_backup_bucket
    new = backup_key(NOW - timedelta(days=400))
    _put(backup_s3, bucket, new)

    assert prune(backup_s3, bucket, NOW, retention_days=30, keep=new) == []
    assert _keys(backup_s3, bucket) == {new}


# --- run_backup (fake pg_dump, real S3) -------------------------------------------------


def test_run_backup_uploads_verifies_and_prunes(settings, fake_pg_dump, backup_s3):
    bucket = settings.s3_backup_bucket
    old = backup_key(NOW - timedelta(days=45))
    _put(backup_s3, bucket, old)

    result = run_backup(settings, backup_s3, now=NOW)

    assert result.key == "2026/11/15/invenio-testrig-dashboard-030007.dump"
    head = backup_s3.head_object(Bucket=bucket, Key=result.key)
    assert head["ContentLength"] == result.size > 0
    assert result.pruned == [old]
    assert result.prune_error is None
    assert _keys(backup_s3, bucket) == {result.key}
    # Nothing in the artifacts bucket.
    assert not _keys(backup_s3, settings.s3_bucket) & {result.key}


def test_a_failed_prune_keeps_the_backup(settings, fake_pg_dump, backup_s3, monkeypatch):
    def broken_prune(*args, **kwargs):
        raise RuntimeError("S3 said no")

    monkeypatch.setattr(backup_module, "prune", broken_prune)

    result = run_backup(settings, backup_s3, now=NOW)

    assert result.prune_error == "RuntimeError: S3 said no"
    assert result.key in _keys(backup_s3, settings.s3_backup_bucket)


def test_a_size_mismatch_fails_the_backup(settings, fake_pg_dump, backup_s3, monkeypatch):
    real_head = backup_s3.head_object

    def short_head(**kwargs):
        return {**real_head(**kwargs), "ContentLength": 1}

    monkeypatch.setattr(backup_s3, "head_object", short_head)

    with pytest.raises(BackupError, match="has 1 bytes"):
        run_backup(settings, backup_s3, now=NOW)


def test_run_backup_without_a_bucket(settings):
    with pytest.raises(BackupError, match="s3_backup_bucket is not set"):
        run_backup(settings.model_copy(update={"s3_backup_bucket": None}), object())


# --- CLI --------------------------------------------------------------------------------

runner = CliRunner()


def _cli_env(monkeypatch, settings: Settings, db_url: str, **extra: str) -> None:
    env = {
        "ITD_SEED_REPOSITORY": settings.seed_repository,
        "ITD_RUNNER_REPOSITORY": settings.runner_repository,
        "ITD_S3_BUCKET": settings.s3_bucket,
        "ITD_S3_ENDPOINT_URL": settings.s3_endpoint_url or "",
        "ITD_S3_ACCESS_KEY": "devaccess",
        "ITD_S3_SECRET_KEY": "devsecret",
        "ITD_DATABASE_URL": db_url,
        "ITD_GITHUB_TOKEN": "test-token",
        **extra,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_cli_backup_needs_the_bucket(settings, db_url, monkeypatch):
    _cli_env(monkeypatch, settings, db_url)

    result = runner.invoke(app, ["backup"])

    assert result.exit_code == 1
    assert "s3_backup_bucket is not set" in result.output + result.stderr


def test_cli_backup(settings, db_url, fake_pg_dump, backup_s3, monkeypatch):
    _cli_env(monkeypatch, settings, db_url, ITD_S3_BACKUP_BUCKET=settings.s3_backup_bucket)

    result = runner.invoke(app, ["backup"])

    assert result.exit_code == 0, result.output + result.stderr
    match = re.search(r"Backup: s3://([^/]+)/(\S+) \(", result.output)
    assert match and match.group(1) == settings.s3_backup_bucket
    assert match.group(2) in _keys(backup_s3, settings.s3_backup_bucket)
    assert "Pruned: 0 dump(s) older than 30 days" in result.output


def test_cli_backup_failure_exits_1_and_reports(settings, db_url, fake_pg_dump, monkeypatch):
    _cli_env(monkeypatch, settings, db_url, ITD_S3_BACKUP_BUCKET=settings.s3_backup_bucket)
    fake_pg_dump.set(exit=1, write=False, stderr="connection refused\n")
    reported = []
    monkeypatch.setattr(
        "invenio_testrig_dashboard.cli.alerts.crash_alert",
        lambda session_factory, settings, name, detail: reported.append((name, detail)),
    )

    result = runner.invoke(app, ["backup"])

    assert result.exit_code == 1
    # The crash reporter posts it to ops Discord.
    assert [name for name, _ in reported] == ["backup"]
    assert "pg_dump failed (exit 1): connection refused" in reported[0][1]


# --- the real round trip ----------------------------------------------------------------


def _real_pg_major(tool: str) -> int:
    path = shutil.which(tool)
    if path is None:
        return 0
    output = subprocess.run(  # noqa: S603 - a fixed tool name from PATH
        [path, "--version"], capture_output=True, text=True, check=False
    ).stdout
    match = re.search(r"(\d+)", output)
    return int(match.group(1)) if match else 0


needs_pg18 = pytest.mark.skipif(
    min(_real_pg_major("pg_dump"), _real_pg_major("pg_restore")) < 18,
    reason="pg_dump/pg_restore 18+ not installed (CI installs postgresql-client-18)",
)


@needs_pg18
def test_backup_and_restore_round_trip(settings, db_url, ingest_fixture, backup_s3, tmp_path):
    ingest_fixture("real-2026-11-13")
    settings = settings.model_copy(update={"database_url": type(settings.database_url)(db_url)})
    source = create_engine(make_url(db_url).set(drivername="postgresql+psycopg"))
    with source.connect() as connection:
        counts = {
            model.__tablename__: connection.scalar(select(func.count()).select_from(model))
            for model in (Run, TestResult)
        }
    source.dispose()
    assert counts["test_result"] > 0

    result = run_backup(settings, backup_s3)
    downloaded = tmp_path / "restore.dump"
    backup_s3.download_file(settings.s3_backup_bucket, result.key, str(downloaded))

    restored_name = f"{make_url(db_url).database}_restored"
    admin = create_engine(
        make_url(db_url).set(database="postgres", drivername="postgresql+psycopg"),
        isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{restored_name}"'))  # noqa: S608
    try:
        restored_url = make_url(db_url).set(database=restored_name)
        restore = subprocess.run(  # noqa: S603 - fixed arguments; credentials via the environment
            [
                shutil.which("pg_restore") or "pg_restore",
                "--no-owner",
                "--dbname",
                restored_name,
                str(downloaded),
            ],
            env={**pg_environment(settings), "PGDATABASE": restored_name},
            capture_output=True,
            text=True,
            check=False,
        )
        assert restore.returncode == 0, restore.stderr
        restored = create_engine(restored_url.set(drivername="postgresql+psycopg"))
        with restored.connect() as connection:
            for table, count in counts.items():
                assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == count  # noqa: S608
        restored.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{restored_name}" WITH (FORCE)'))  # noqa: S608
        admin.dispose()
