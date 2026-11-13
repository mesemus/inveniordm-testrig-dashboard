import json
import shutil
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import httpx2
import pytest
from boto3.exceptions import S3UploadFailedError
from botocore.config import Config
from botocore.exceptions import BotoCoreError
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from invenio_testrig_dashboard import ingest
from invenio_testrig_dashboard.ingest import store_run
from invenio_testrig_dashboard.models import PackageResult, Run, TestCase, TestResult
from invenio_testrig_dashboard.parsing import parse_run_dir
from invenio_testrig_dashboard.runs import mark_infra_error, short_error
from invenio_testrig_dashboard.status import RunStatus, RunTrigger
from invenio_testrig_dashboard.storage import run_prefix
from tests.reportdata import write_volume_report

FIXTURES = Path(__file__).parent / "fixtures" / "runs"
REAL = FIXTURES / "real-2026-11-13"


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_store_real_fixture(ingest_fixture, session):
    run = ingest_fixture("real-2026-11-13")

    assert run.status is RunStatus.COMPLETED
    assert run.packages_total == 55
    assert run.packages_failed == 1
    assert run.tests_total == 10985
    assert run.tests_failed == 1
    assert count(session, PackageResult) == 55
    assert count(session, TestResult) == 10985


def test_store_ok_fixture_and_s3_content_types(ingest_fixture, session, s3, settings):
    parsed = parse_run_dir(FIXTURES / "ok")
    run = ingest_fixture("ok")

    assert run.status is RunStatus.COMPLETED
    assert run.packages_total == len(parsed.packages)
    assert run.tests_total == sum(len(package.cases) for package in parsed.packages)
    assert run.tests_failed == 0
    assert run.tests_skipped == 0
    assert count(session, PackageResult) == len(parsed.packages)
    assert count(session, TestResult) == run.tests_total

    prefix = run_prefix(run)
    data = s3.head_object(Bucket=settings.s3_bucket, Key=prefix + "data.json")
    html = s3.head_object(Bucket=settings.s3_bucket, Key=prefix + "report.html")
    assert data["ContentType"] == "application/json"
    assert html["ContentType"] == "text/html; charset=utf-8"


def test_long_multibyte_test_name_is_stored_unchanged(ingest_fixture, session):
    parsed = parse_run_dir(FIXTURES / "params")
    long_name = next(
        case.name
        for package in parsed.packages
        for case in package.cases
        if case.name.startswith("test_unicode[")
    )

    run = ingest_fixture("params")

    assert run.status is RunStatus.COMPLETED
    stored = session.scalar(
        select(TestCase.name).where(TestCase.package == "params-pkg", TestCase.name == long_name)
    )
    assert stored == long_name


def test_reingest_replaces_rows_without_duplicates(
    ingest_fixture, session, session_factory, s3, settings
):
    run = ingest_fixture("ok")
    with session_factory() as session:
        before = (
            count(session, PackageResult),
            count(session, TestResult),
            count(session, TestCase),
        )

    store_run(session_factory, s3, settings, run, FIXTURES / "ok", alert=False)

    with session_factory() as session:
        assert (
            count(session, PackageResult),
            count(session, TestResult),
            count(session, TestCase),
        ) == before
        assert session.get(Run, run.id).status is RunStatus.COMPLETED


def test_test_cases_are_shared_between_runs(ingest_fixture, session, session_factory):
    first = ingest_fixture("ok")
    second = ingest_fixture("failing")

    with session_factory() as session:
        shared = session.scalar(
            select(TestCase).where(TestCase.package == "pkg-a", TestCase.name == "test_one")
        )
        new = session.scalar(select(TestCase).where(TestCase.name == "test_bad_one"))

    assert shared.first_seen_run_id == first.id
    assert new.first_seen_run_id == second.id


def test_bad_schema_is_infra_error_but_files_are_uploaded(ingest_fixture, session, s3, settings):
    run = ingest_fixture("bad_schema")

    assert run.status is RunStatus.INFRA_ERROR
    assert "invalid results" in run.error
    assert count(session, PackageResult) == 0
    assert count(session, TestResult) == 0
    s3.head_object(Bucket=settings.s3_bucket, Key=run_prefix(run) + "data.json")


def test_incomplete_run_is_infra_error(ingest_fixture, session):
    run = ingest_fixture("incomplete")

    assert run.status is RunStatus.INFRA_ERROR
    assert "incomplete" in run.error
    assert count(session, PackageResult) == 0
    assert count(session, TestResult) == 0


def test_s3_failure_leaves_run_unchanged(make_run, session, session_factory, settings):
    run = make_run()
    bad_client = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:1",
        region_name=settings.s3_region,
        aws_access_key_id="devaccess",
        aws_secret_access_key="devsecret",
        config=Config(
            s3={"addressing_style": "path"},
            retries={"max_attempts": 0},
            connect_timeout=1,
            read_timeout=1,
        ),
    )

    with pytest.raises((BotoCoreError, S3UploadFailedError)):
        store_run(session_factory, bad_client, settings, run, FIXTURES / "ok", alert=False)

    with session_factory() as session:
        assert session.get(Run, run.id).status is RunStatus.QUEUED
        assert count(session, PackageResult) == 0
        assert count(session, TestResult) == 0


def test_run_prefix_uses_utc_date():
    # 00:30 at UTC+02:00 is 22:30 on the previous UTC day.
    created_at = datetime(2026, 11, 14, 0, 30, tzinfo=timezone(timedelta(hours=2)))
    run = Run(
        uid=uuid.uuid4(),
        target_id=1,
        status=RunStatus.QUEUED,
        trigger=RunTrigger.SCHEDULE,
        testrig_ref="v1",
        created_at=created_at,
    )

    assert run_prefix(run) == f"runs/2026/11/13/{run.uid}/"


def test_mark_infra_error_cleans_redacts_and_truncates_text(make_run, session_factory, settings):
    run = make_run()
    message = "before\x00after \ud800 " + settings.github_token.get_secret_value()

    mark_infra_error(session_factory, run.id, message, settings)

    with session_factory() as session:
        stored = session.get(Run, run.id)
    assert stored.status is RunStatus.INFRA_ERROR
    assert "before" in stored.error
    assert "after" in stored.error
    assert "***" in stored.error
    assert "\x00" not in stored.error
    assert "\ud800" not in stored.error


def test_mark_infra_error_accepts_another_status(make_run, session_factory, settings):
    run = make_run()

    mark_infra_error(session_factory, run.id, "never appeared", settings, status=RunStatus.LOST)

    with session_factory() as session:
        assert session.get(Run, run.id).status is RunStatus.LOST


@pytest.mark.slow
def test_volume_ingest_is_fast(make_run, session, session_factory, s3, settings, tmp_path):
    report = tmp_path / "volume"
    write_volume_report(report)
    run = make_run()

    started = time.perf_counter()
    status = store_run(session_factory, s3, settings, run, report, alert=False)
    elapsed = time.perf_counter() - started

    assert status is RunStatus.COMPLETED
    assert elapsed < 30
    with session_factory() as session:
        assert count(session, PackageResult) == 55
        assert count(session, TestResult) == 50_055  # 50k cases + one <suite> case per package
        assert session.get(Run, run.id).tests_total == 50_055


def list_keys(s3, settings, prefix):
    # RustFS can briefly return an empty listing right after upload; retry a few times.
    for _ in range(10):
        pages = s3.get_paginator("list_objects_v2").paginate(
            Bucket=settings.s3_bucket, Prefix=prefix
        )
        keys = {item["Key"][len(prefix) :] for page in pages for item in page.get("Contents", [])}
        if keys:
            return keys
        time.sleep(0.05)
    return set()


def copy_report_with_name(tmp_path, name):
    report = tmp_path / "report"
    shutil.copytree(FIXTURES / "ok", report)
    data = json.loads((report / "data.json").read_text())
    data["config_name"] = name
    (report / "data.json").write_text(json.dumps(data))
    return report


def test_store_run_accepts_matching_expected_name(
    make_run, session, session_factory, s3, settings, tmp_path
):
    run = make_run()
    report = copy_report_with_name(tmp_path, str(run.uid))

    status = store_run(
        session_factory, s3, settings, run, report, expected_name=str(run.uid), alert=False
    )

    assert status is RunStatus.COMPLETED
    assert count(session, PackageResult) == 2
    assert list_keys(s3, settings, run_prefix(run))


def test_store_run_rejects_a_report_for_another_run(
    make_run, session, session_factory, s3, settings, tmp_path
):
    run = make_run()
    report = copy_report_with_name(tmp_path, "someone-elses-run")

    status = store_run(
        session_factory, s3, settings, run, report, expected_name=str(run.uid), alert=False
    )

    assert status is RunStatus.INFRA_ERROR
    with session_factory() as db:
        stored = db.get(Run, run.id)
    assert "report belongs to another run" in stored.error
    assert count(session, PackageResult) == 0
    assert count(session, TestResult) == 0
    assert list_keys(s3, settings, run_prefix(run)) == set()


def test_symlinks_are_never_uploaded(tmp_path, make_run, session, session_factory, s3, settings):
    # Artifacts are public: a link to a file outside the report must not publish that file.
    report = tmp_path / "report"
    shutil.copytree(FIXTURES / "ok", report)
    outside = tmp_path / "secret.txt"
    outside.write_text("TOP-SECRET")
    (report / "outside.log").symlink_to(outside)
    (report / "inside.json").symlink_to(report / "data.json")
    (report / "linked_dir").symlink_to(tmp_path, target_is_directory=True)
    run = make_run()

    assert store_run(session_factory, s3, settings, run, report, alert=False) is RunStatus.COMPLETED

    keys = list_keys(s3, settings, run_prefix(run))
    assert "data.json" in keys
    assert not {"outside.log", "inside.json"} & keys
    assert not any(key.startswith("linked_dir/") for key in keys)


def test_text_files_get_utf8_charset(ingest_fixture, s3, settings):
    run = ingest_fixture("real-2026-11-13")

    key = run_prefix(run) + "artifacts/invenio-records-rest/original_log.log"
    assert s3.head_object(Bucket=settings.s3_bucket, Key=key)["ContentType"] == (
        "text/plain; charset=utf-8"
    )


def test_invalid_reingest_clears_previous_results(
    ingest_fixture, session, session_factory, s3, settings
):
    run = ingest_fixture("ok")
    with session_factory() as db, db.begin():
        db.execute(
            update(Run)
            .where(Run.id == run.id)
            .values(gh_run_id=42, gh_run_url="https://github.com/org/runner/actions/runs/42")
        )

    status = store_run(session_factory, s3, settings, run, FIXTURES / "bad_schema", alert=False)

    assert status is RunStatus.INFRA_ERROR
    with session_factory() as session:
        stored = session.get(Run, run.id)
        assert stored.status is RunStatus.INFRA_ERROR
        assert stored.packages_total is None
        assert stored.tests_total is None
        assert stored.finished_at is None
        assert stored.ingested_at is None
        # The attempt itself is kept: the GitHub run is where a failed run gets debugged.
        assert stored.started_at == run.started_at
        assert stored.gh_run_id == 42
        assert stored.gh_run_url == "https://github.com/org/runner/actions/runs/42"
        assert count(session, PackageResult) == 0
        assert count(session, TestResult) == 0


def test_db_failure_reraises_original_error_and_records_infra_error(
    make_run, session, session_factory, s3, settings, monkeypatch
):
    secret = settings.github_token.get_secret_value()
    original = IntegrityError(
        "INSERT INTO package_result ...", {"p": "x" * 10_000}, Exception(f"boom {secret}\nDETAIL")
    )

    def failing_write(session, run_id, parsed):
        raise original

    monkeypatch.setattr(ingest, "_write_rows", failing_write)
    run = make_run()

    with pytest.raises(IntegrityError) as excinfo:
        store_run(session_factory, s3, settings, run, FIXTURES / "ok", alert=False)

    assert excinfo.value is original  # the original exception, not a follow-up error
    with session_factory() as session:
        stored = session.get(Run, run.id)
        assert stored.status is RunStatus.INFRA_ERROR
        assert stored.error == "Exception: boom ***"  # driver message only, first line, redacted
        assert count(session, PackageResult) == 0


def test_short_error_never_contains_a_signed_url():
    request = httpx2.Request("GET", "https://storage.example/artifact.zip?sig=SECRET")
    status_error = httpx2.HTTPStatusError(
        "Client error '404 Not Found' for url 'https://storage.example/artifact.zip?sig=SECRET'",
        request=request,
        response=httpx2.Response(404, request=request),
    )
    transport_error = httpx2.ConnectError("connect failed: https://storage.example/?sig=SECRET")

    assert short_error(status_error) == "HTTP 404"
    assert short_error(transport_error) == "ConnectError"
    assert short_error(ValueError("first line\nsecond")) == "ValueError: first line"


def test_a_stored_run_leaves_planner_statistics(ingest_fixture, session):
    """A fresh bulk load must not wait for autovacuum: a missing ANALYZE once cost 6+ minutes."""
    ingest_fixture("ok")

    analyzed = dict(
        session.execute(
            text(
                "SELECT relname, last_analyze IS NOT NULL FROM pg_stat_user_tables"
                " WHERE relname IN ('test_result', 'test_case')"
            )
        ).all()
    )
    assert analyzed == {"test_result": True, "test_case": True}


def test_the_analyze_statement_names_the_model_tables():
    assert str(ingest.ANALYZE_RESULTS) == (
        f"ANALYZE {TestResult.__tablename__}, {TestCase.__tablename__}"
    )


def test_a_failing_analyze_does_not_fail_the_ingest(ingest_fixture, monkeypatch, caplog):
    monkeypatch.setattr(ingest, "ANALYZE_RESULTS", text("ANALYZE no_such_table"))

    run = ingest_fixture("ok")

    assert run.status is RunStatus.COMPLETED
    assert "ANALYZE after the bulk load failed" in caplog.text
