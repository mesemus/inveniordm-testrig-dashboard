import json
import re
import shutil
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import func, select, update

from invenio_testrig_dashboard import alerts, ingest_github
from invenio_testrig_dashboard import archive as archive_module
from invenio_testrig_dashboard.github import GitHub, GitHubAuthError
from invenio_testrig_dashboard.ingest_github import INGEST_LOCK_KEY, ingest_tick
from invenio_testrig_dashboard.models import PackageResult, Run, TestResult
from invenio_testrig_dashboard.runs import RunUnusable
from invenio_testrig_dashboard.status import RunStatus
from invenio_testrig_dashboard.status import TestStatus as TS
from invenio_testrig_dashboard.storage import run_prefix

FIXTURES = Path(__file__).parent / "fixtures" / "runs"
GITHUB_FIXTURES = Path(__file__).parent / "fixtures" / "github"
REPO = "oarepo/invenio-testrig-dashboard-runner"
NOW = datetime(2026, 11, 13, 2, 0, tzinfo=UTC)
STARTED = "2026-11-13T01:00:00Z"
JOBS_IN_PROGRESS = json.loads(
    (GITHUB_FIXTURES / "run-43623789406-jobs-in-progress.json").read_text()
)["jobs"]


def report_with_name(tmp_path, name, source=FIXTURES / "ok", directory: str | None = None) -> Path:
    report = tmp_path / (directory or f"report-{name}")
    shutil.copytree(source, report)
    data = json.loads((report / "data.json").read_text())
    data["config_name"] = name
    (report / "data.json").write_text(json.dumps(data))
    return report


def zip_report(report: Path, tmp_path: Path, name: str = "artifact") -> bytes:
    archive = shutil.make_archive(str(tmp_path / name), "zip", root_dir=report)
    return Path(archive).read_bytes()


def artifact_payload(artifact_id: int, content: bytes) -> list[dict]:
    """A ``list_artifacts`` result with one ``final-report`` artifact."""
    return [
        {
            "id": artifact_id,
            "name": "final-report",
            "size_in_bytes": len(content),
            "expired": False,
            "archive_download_url": (
                f"https://api.github.com/repos/{REPO}/actions/artifacts/{artifact_id}/zip"
            ),
        }
    ]


def make_handler(
    *,
    run_status: str = "in_progress",
    conclusion: str | None = None,
    listed: list[dict] | None = None,
    artifacts: list[dict] | None = None,
    jobs: list[dict] | None = None,
    content: bytes = b"",
    run_payloads: dict[int, dict] | None = None,
    artifacts_by_run: dict[int, list[dict]] | None = None,
    content_by_artifact: dict[int, bytes] | None = None,
    failure=None,
):
    """A mocked GitHub API. Returns ``(calls, handler)``; ``calls`` records "METHOD /path".

    Two ways to configure it:

    - **One run** (most tests): ``run_status``/``conclusion`` answer every ``get_run``,
      ``artifacts`` every ``list_artifacts``, and ``content`` every artifact download.
    - **Several runs**: ``run_payloads``, ``artifacts_by_run`` (keyed by GitHub run id), and
      ``content_by_artifact`` (keyed by artifact id) take precedence over the single-run values.

    ``listed`` is the ``list_dispatch_runs`` result. ``failure(request)`` may return a response
    to inject an error; returning None falls through to the normal answer.
    """
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(f"{request.method} {request.url.path}")
        if failure is not None:
            response = failure(request)
            if response is not None:
                return response
        path = request.url.path
        if path.endswith("/zip"):
            if content_by_artifact is not None:
                artifact_id = int(path.split("/artifacts/")[1].split("/")[0])
                return httpx2.Response(200, content=content_by_artifact[artifact_id])
            return httpx2.Response(200, content=content)
        match = re.fullmatch(rf"/repos/{re.escape(REPO)}/actions/runs/(\d+)", path)
        if match:
            run_id = int(match.group(1))
            if run_payloads and run_id in run_payloads:
                return httpx2.Response(200, json=run_payloads[run_id])
            return httpx2.Response(
                200,
                json={
                    "id": run_id,
                    "status": run_status,
                    "conclusion": conclusion,
                    "run_started_at": STARTED,
                },
            )
        artifacts_match = re.fullmatch(
            rf"/repos/{re.escape(REPO)}/actions/runs/(\d+)/artifacts", path
        )
        if artifacts_match:
            run_id = int(artifacts_match.group(1))
            selected = (
                artifacts_by_run.get(run_id, [])
                if artifacts_by_run is not None
                else artifacts or []
            )
            return httpx2.Response(200, json={"total_count": len(selected), "artifacts": selected})
        jobs_match = re.fullmatch(rf"/repos/{re.escape(REPO)}/actions/runs/(\d+)/jobs", path)
        if jobs_match:
            return httpx2.Response(200, json={"total_count": len(jobs or []), "jobs": jobs or []})
        if path == f"/repos/{REPO}/actions/runs":
            return httpx2.Response(
                200,
                json={"total_count": len(listed or []), "workflow_runs": listed or []},
            )
        return httpx2.Response(404)

    return calls, handler


def client(handler) -> GitHub:
    return GitHub("token", REPO, transport=httpx2.MockTransport(handler))


def s3_keys(s3, settings, prefix: str) -> set[str]:
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=settings.s3_bucket, Prefix=prefix)
    return {item["Key"] for page in pages for item in page.get("Contents", [])}


def test_queued_with_run_id_is_polled_without_listing(
    make_run, session, session_factory, engine, s3, settings
):
    run = make_run(
        status=RunStatus.QUEUED,
        gh_run_id=123,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    calls, handler = make_handler(run_status="in_progress")

    with client(handler) as github:
        summary = ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert summary.matched == 1
    assert not any(path.endswith("/actions/runs") for path in calls)
    stored = session.get(Run, run.id)
    assert stored.status is RunStatus.RUNNING
    assert stored.started_at == datetime(2026, 11, 13, 1, 0, tzinfo=UTC)


def test_missing_github_run_marks_infra_error(
    make_run, session, session_factory, engine, s3, settings
):
    run = make_run(
        status=RunStatus.QUEUED,
        gh_run_id=404404,
        dispatched_at=NOW - timedelta(minutes=5),
    )

    def failure(request):
        if request.url.path.endswith("/runs/404404"):
            return httpx2.Response(404)
        return None

    _, handler = make_handler(failure=failure)
    with client(handler) as github:
        ingest_tick(engine, session_factory, s3, settings, github, NOW)

    stored = session.get(Run, run.id)
    assert stored.status is RunStatus.INFRA_ERROR
    assert stored.error == "GitHub run no longer exists"


def test_queued_run_is_matched_by_exact_title(
    make_run, session, session_factory, engine, s3, settings
):
    run = make_run(dispatched_at=NOW - timedelta(minutes=5))
    listed = [
        {"id": 1, "display_title": "another-uid", "run_started_at": STARTED},
        {"id": 2, "display_title": f"master-{run.uid}", "run_started_at": STARTED},
        {"id": 3, "display_title": f"prefix {run.uid}", "run_started_at": STARTED},
        {"id": 4, "display_title": "Run on testrig", "run_started_at": STARTED},
    ]
    calls, handler = make_handler(listed=listed, run_status="in_progress")

    with client(handler) as github:
        summary = ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert summary.matched == 1
    stored = session.get(Run, run.id)
    assert stored.status is RunStatus.RUNNING
    assert stored.gh_run_id == 2
    assert stored.gh_run_url == f"https://github.com/{REPO}/actions/runs/2"
    assert calls.count(f"GET /repos/{REPO}/actions/runs") == 1


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(hours=7), RunStatus.LOST),
        (timedelta(hours=1), RunStatus.QUEUED),
    ],
)
def test_lost_detection_uses_lost_after_hours(
    make_run, session, session_factory, engine, s3, settings, age, expected
):
    run = make_run(dispatched_at=NOW - age)
    _, handler = make_handler(listed=[])

    with client(handler) as github:
        summary = ingest_tick(engine, session_factory, s3, settings, github, NOW)

    stored = session.get(Run, run.id)
    assert stored.status is expected
    if expected is RunStatus.LOST:
        assert stored.error == "no GitHub run found"
        assert summary.lost == 1
    else:
        assert summary.left == 1


def test_completed_run_with_wrong_config_name_is_rejected(
    make_run, session, session_factory, engine, s3, settings, tmp_path
):
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    content = zip_report(report_with_name(tmp_path, "someone-elses-run"), tmp_path)
    _, handler = make_handler(
        run_status="completed",
        conclusion="success",
        artifacts=artifact_payload(1, content),
        content=content,
    )

    with client(handler) as github:
        summary = ingest_tick(engine, session_factory, s3, settings, github, NOW)

    stored = session.get(Run, run.id)
    assert stored.status is RunStatus.INFRA_ERROR
    assert "report belongs to another run" in stored.error
    assert summary.infra_error == 1
    assert session.scalar(select(func.count()).select_from(PackageResult)) == 0
    assert s3_keys(s3, settings, run_prefix(run)) == set()


def test_completed_run_with_matching_report_is_stored(
    make_run, session, session_factory, engine, s3, settings, tmp_path
):
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    content = zip_report(report_with_name(tmp_path, str(run.uid)), tmp_path)
    _, handler = make_handler(
        run_status="completed",
        conclusion="success",
        artifacts=artifact_payload(1, content),
        content=content,
    )

    with client(handler) as github:
        summary = ingest_tick(engine, session_factory, s3, settings, github, NOW)

    stored = session.get(Run, run.id)
    assert stored.status is RunStatus.COMPLETED
    assert summary.completed == 1
    assert session.scalar(select(func.count()).select_from(PackageResult)) == 2
    assert session.scalar(select(func.count()).select_from(TestResult)) > 0


def test_failed_conclusion_with_real_report_is_completed(
    make_run, session, session_factory, engine, s3, settings, tmp_path
):
    run_payload = json.loads((GITHUB_FIXTURES / "run-43459288882.json").read_text())
    artifacts = json.loads((GITHUB_FIXTURES / "run-43459288882-artifacts.json").read_text())[
        "artifacts"
    ]
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=43459288882,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    content = zip_report(
        report_with_name(tmp_path, str(run.uid), FIXTURES / "real-2026-11-13"),
        tmp_path,
    )
    _, handler = make_handler(
        artifacts=artifacts,
        content=content,
        run_payloads={43459288882: run_payload},
    )

    with client(handler) as github:
        summary = ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert summary.completed == 1
    assert session.get(Run, run.id).status is RunStatus.COMPLETED
    assert session.scalar(select(func.count()).select_from(PackageResult)) == 55


def test_cancelled_run_without_artifacts_is_infra_error(
    make_run, session, session_factory, engine, s3, settings
):
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    _, handler = make_handler(run_status="completed", conclusion="cancelled", artifacts=[])

    with client(handler) as github:
        ingest_tick(engine, session_factory, s3, settings, github, NOW)

    stored = session.get(Run, run.id)
    assert stored.status is RunStatus.INFRA_ERROR
    assert "cancelled" in stored.error


def test_tar_inside_zip_is_extracted(
    make_run, session, session_factory, engine, s3, settings, tmp_path
):
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    report = report_with_name(tmp_path, str(run.uid))
    tar_path = shutil.make_archive(str(tmp_path / "bundle"), "gztar", root_dir=report)
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(tar_path, arcname="bundle.tar.gz")
    content = archive.read_bytes()
    _, handler = make_handler(
        run_status="completed",
        conclusion="success",
        artifacts=artifact_payload(1, content),
        content=content,
    )

    with client(handler) as github:
        ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert session.get(Run, run.id).status is RunStatus.COMPLETED
    assert session.scalar(select(func.count()).select_from(PackageResult)) == 2


def test_a_running_run_gets_progress_from_the_job_list(
    make_run, session, session_factory, engine, s3, settings
):
    run = make_run(status=RunStatus.RUNNING, gh_run_id=1, dispatched_at=NOW - timedelta(minutes=5))
    _, handler = make_handler(run_status="in_progress", jobs=JOBS_IN_PROGRESS)

    with client(handler) as github:
        summary = ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert summary.left == 1
    row = session.get(Run, run.id)
    assert row.status is RunStatus.RUNNING
    assert (row.progress_total, row.progress_done) == (58, 30)
    assert row.progress_failed == ["invenio-records-rest"]
    assert row.progress_at is not None


@pytest.mark.parametrize("garbage", [True, False])
def test_broken_progress_is_logged_and_the_run_keeps_running(
    garbage, caplog, make_run, session, session_factory, engine, s3, settings
):
    run = make_run(status=RunStatus.RUNNING, gh_run_id=1, dispatched_at=NOW - timedelta(minutes=5))

    def failure(request):
        if not request.url.path.endswith("/jobs"):
            return None
        if garbage:
            return httpx2.Response(200, content=b"garbage")
        raise httpx2.ConnectError("boom")

    _, handler = make_handler(failure=failure)

    with caplog.at_level("WARNING"):
        with client(handler) as github:
            summary = ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert summary.left == 1
    row = session.get(Run, run.id)
    assert row.status is RunStatus.RUNNING
    assert row.error is None
    assert row.progress_at is None
    assert "progress not updated" in caplog.text


def test_a_progress_auth_error_stops_the_tick(
    make_run, session, session_factory, engine, s3, settings
):
    run = make_run(status=RunStatus.RUNNING, gh_run_id=1, dispatched_at=NOW - timedelta(minutes=5))

    def failure(request):
        return httpx2.Response(401) if request.url.path.endswith("/jobs") else None

    _, handler = make_handler(failure=failure)

    with client(handler) as github:
        with pytest.raises(GitHubAuthError):
            ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert session.get(Run, run.id).status is RunStatus.RUNNING


def test_a_completed_run_does_not_ask_for_jobs(
    make_run, session, session_factory, engine, s3, settings
):
    make_run(status=RunStatus.RUNNING, gh_run_id=1, dispatched_at=NOW - timedelta(minutes=5))
    calls, handler = make_handler(run_status="completed", conclusion="success")

    with client(handler) as github:
        ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert not any(call.endswith("/jobs") for call in calls)
    assert any(call.endswith("/artifacts") for call in calls)


def test_running_in_progress_is_unchanged(make_run, session, session_factory, engine, s3, settings):
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    _, handler = make_handler(run_status="in_progress")

    with client(handler) as github:
        summary = ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert summary.left == 1
    assert session.get(Run, run.id).status is RunStatus.RUNNING
    assert session.scalar(select(func.count()).select_from(PackageResult)) == 0


def test_ingest_lock_prevents_a_second_tick(
    make_run, session, session_factory, engine, s3, settings
):
    run = make_run(
        status=RunStatus.QUEUED,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    calls, handler = make_handler()

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        assert connection.scalar(select(func.pg_try_advisory_lock(INGEST_LOCK_KEY)))
        try:
            with client(handler) as github:
                summary = ingest_tick(engine, session_factory, s3, settings, github, NOW)
        finally:
            connection.execute(select(func.pg_advisory_unlock(INGEST_LOCK_KEY)))

    assert summary.busy is True
    assert calls == []
    assert session.get(Run, run.id).status is RunStatus.QUEUED


def test_completed_run_without_final_report_is_infra_error(
    make_run, session, session_factory, engine, s3, settings
):
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    _, handler = make_handler(run_status="completed", conclusion="success", artifacts=[])

    with client(handler) as github:
        ingest_tick(engine, session_factory, s3, settings, github, NOW)

    stored = session.get(Run, run.id)
    assert stored.status is RunStatus.INFRA_ERROR
    assert stored.error == "no results artifacts (workflow conclusion: success)"


def test_expired_final_report_is_infra_error(
    make_run, session, session_factory, engine, s3, settings
):
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    artifacts = artifact_payload(1, b"content")
    artifacts[0]["expired"] = True
    _, handler = make_handler(run_status="completed", conclusion="success", artifacts=artifacts)

    with client(handler) as github:
        ingest_tick(engine, session_factory, s3, settings, github, NOW)

    stored = session.get(Run, run.id)
    assert stored.status is RunStatus.INFRA_ERROR
    assert "no results artifacts" in stored.error


def test_oversized_artifact_is_rejected_before_download(
    make_run, session, session_factory, engine, s3, settings
):
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    artifacts = artifact_payload(1, b"content")
    artifacts[0]["size_in_bytes"] = 300 * 1024 * 1024
    calls, handler = make_handler(run_status="completed", conclusion="success", artifacts=artifacts)

    with client(handler) as github:
        ingest_tick(engine, session_factory, s3, settings, github, NOW)

    stored = session.get(Run, run.id)
    assert stored.status is RunStatus.INFRA_ERROR
    assert "too large" in stored.error
    assert not any(path.endswith("/zip") for path in calls)


def test_extraction_limit_is_enforced(
    make_run, session, session_factory, engine, s3, settings, tmp_path, monkeypatch
):
    monkeypatch.setattr(archive_module, "MAX_EXTRACT_BYTES", 10)
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    content = zip_report(report_with_name(tmp_path, str(run.uid)), tmp_path)
    _, handler = make_handler(
        run_status="completed",
        conclusion="success",
        artifacts=artifact_payload(1, content),
        content=content,
    )

    with client(handler) as github:
        ingest_tick(engine, session_factory, s3, settings, github, NOW)

    stored = session.get(Run, run.id)
    assert stored.status is RunStatus.INFRA_ERROR
    assert "too large to extract" in stored.error


def test_artifact_download_404_is_treated_as_expired(
    make_run, session, session_factory, engine, s3, settings
):
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    artifacts = artifact_payload(1, b"content")

    def failure(request):
        if request.url.path.endswith("/zip"):
            return httpx2.Response(404)
        return None

    _, handler = make_handler(
        run_status="completed",
        conclusion="success",
        artifacts=artifacts,
        content=b"content",
        failure=failure,
    )

    with client(handler) as github:
        ingest_tick(engine, session_factory, s3, settings, github, NOW)

    stored = session.get(Run, run.id)
    assert stored.status is RunStatus.INFRA_ERROR
    assert stored.error == "no results artifacts (workflow conclusion: success)"


def test_artifacts_404_marks_the_run_infra_error(
    make_run, session, session_factory, engine, s3, settings
):
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )

    def failure(request):
        if request.url.path.endswith("/artifacts"):
            return httpx2.Response(404)
        return None

    _, handler = make_handler(run_status="completed", conclusion="success", failure=failure)

    with client(handler) as github:
        ingest_tick(engine, session_factory, s3, settings, github, NOW)

    stored = session.get(Run, run.id)
    assert stored.status is RunStatus.INFRA_ERROR
    assert stored.error == "GitHub run no longer exists"


def test_unexpected_exception_marks_only_that_run(
    make_run, session, session_factory, engine, s3, settings, tmp_path, monkeypatch
):
    first = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    second = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=2,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    first_content = zip_report(
        report_with_name(tmp_path, str(first.uid)), tmp_path, name="artifact-1"
    )
    second_content = zip_report(
        report_with_name(tmp_path, str(second.uid)), tmp_path, name="artifact-2"
    )
    artifacts_by_run = {
        1: artifact_payload(101, first_content),
        2: artifact_payload(102, second_content),
    }
    _, handler = make_handler(
        run_status="completed",
        conclusion="success",
        artifacts_by_run=artifacts_by_run,
        content_by_artifact={101: first_content, 102: second_content},
    )
    real_store = ingest_github.store_run

    def flaky_store(session_factory, s3, settings, run, run_dir, **kwargs):
        if str(run.uid) == str(first.uid):
            raise RuntimeError("boom")
        return real_store(session_factory, s3, settings, run, run_dir, **kwargs)

    monkeypatch.setattr(ingest_github, "store_run", flaky_store)

    with client(handler) as github:
        summary = ingest_tick(engine, session_factory, s3, settings, github, NOW)

    first_stored = session.get(Run, first.id)
    assert first_stored.status is RunStatus.INFRA_ERROR
    assert first_stored.error == "RuntimeError: boom"
    assert session.get(Run, second.id).status is RunStatus.COMPLETED
    assert summary.infra_error == 1
    assert summary.completed == 1


def test_the_newest_final_report_is_used(
    make_run, session, session_factory, engine, s3, settings, tmp_path
):
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    old_content = zip_report(
        report_with_name(tmp_path, str(run.uid), directory="old-report"),
        tmp_path,
        name="old",
    )
    new_content = zip_report(
        report_with_name(
            tmp_path,
            str(run.uid),
            FIXTURES / "real-2026-11-13",
            directory="new-report",
        ),
        tmp_path,
        name="new",
    )
    artifacts = [
        artifact_payload(100, old_content)[0],
        artifact_payload(200, new_content)[0],
    ]
    _, handler = make_handler(
        run_status="completed",
        conclusion="success",
        artifacts=artifacts,
        content_by_artifact={100: old_content, 200: new_content},
    )

    with client(handler) as github:
        ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert session.get(Run, run.id).status is RunStatus.COMPLETED
    assert session.scalar(select(func.count()).select_from(PackageResult)) == 55


def test_extract_rejects_an_absolute_member(tmp_path):
    archive = tmp_path / "absolute.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("/etc/passwd", "escaped")
    destination = tmp_path / "out"
    destination.mkdir()

    with pytest.raises(RunUnusable, match="unsafe path"):
        archive_module.extract_archive(archive, destination)


def test_extract_file_limit_counts_all_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_module, "MAX_EXTRACT_FILES", 1)
    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.txt", "a")
        zf.writestr("b.txt", "b")

    with pytest.raises(RunUnusable, match="too many files"):
        archive_module.extract_archive(archive, tmp_path / "out")


def test_zip_slip_is_rejected(make_run, session, session_factory, engine, s3, settings, tmp_path):
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    archive = tmp_path / "slip.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.txt", "escaped")
    content = archive.read_bytes()
    _, handler = make_handler(
        run_status="completed",
        conclusion="success",
        artifacts=artifact_payload(1, content),
        content=content,
    )

    with client(handler) as github:
        ingest_tick(engine, session_factory, s3, settings, github, NOW)

    stored = session.get(Run, run.id)
    assert stored.status is RunStatus.INFRA_ERROR
    assert "unsafe path" in stored.error
    assert not (tmp_path / "evil.txt").exists()


def test_500_for_one_run_does_not_stop_the_other(
    make_run, session, session_factory, engine, s3, settings, tmp_path
):
    first = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    second = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=2,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    content = zip_report(report_with_name(tmp_path, str(second.uid)), tmp_path)

    def failure(request):
        if request.url.path.endswith("/runs/1"):
            return httpx2.Response(500)
        return None

    _, handler = make_handler(
        run_status="completed",
        conclusion="success",
        artifacts=artifact_payload(2, content),
        content=content,
        failure=failure,
    )

    with client(handler) as github:
        summary = ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert summary.completed == 1
    assert session.get(Run, first.id).status is RunStatus.RUNNING
    assert session.get(Run, second.id).status is RunStatus.COMPLETED


def test_401_stops_the_tick_and_leaves_the_run(
    make_run, session, session_factory, engine, s3, settings
):
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )

    def failure(request):
        return httpx2.Response(401)

    _, handler = make_handler(failure=failure)
    with client(handler) as github:
        with pytest.raises(GitHubAuthError):
            ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert session.get(Run, run.id).status is RunStatus.RUNNING


@pytest.fixture
def ops_posts(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(
        alerts.discord, "post", lambda url, content, transport=None: sent.append(content) or True
    )
    return sent


def test_the_tick_posts_the_github_auth_alert_and_reraises(
    ops_posts, make_run, session, session_factory, engine, s3, settings
):
    make_run(status=RunStatus.RUNNING, gh_run_id=1, dispatched_at=NOW - timedelta(minutes=5))
    _, handler = make_handler(failure=lambda request: httpx2.Response(401))

    with client(handler) as github:
        with pytest.raises(GitHubAuthError):
            ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert any("GitHub authentication failed" in content for content in ops_posts)


def test_the_tick_warns_about_an_expiring_token(
    ops_posts, make_run, session, session_factory, engine, s3, settings
):
    make_run(status=RunStatus.RUNNING, gh_run_id=1, dispatched_at=NOW - timedelta(minutes=5))
    expiry = (NOW + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S UTC")

    def with_expiry(request):
        return httpx2.Response(
            200,
            json={"id": 1, "status": "in_progress", "run_started_at": STARTED},
            headers={"github-authentication-token-expiration": expiry},
        )

    _, handler = make_handler(failure=with_expiry)
    with client(handler) as github:
        ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert any("GitHub token expires" in content for content in ops_posts)


def test_no_active_runs_makes_no_http_calls(
    session, session_factory, engine, s3, settings, make_run, monkeypatch
):
    sent: list[str] = []
    monkeypatch.setattr(
        alerts.discord,
        "post",
        lambda webhook_url, content, transport=None: sent.append(content) or True,
    )
    failed = {"pkg::test_a": int(TS.FAILED)}
    first = make_run("master", NOW - timedelta(hours=2), status=RunStatus.COMPLETED, results=failed)
    second = make_run(
        "master", NOW - timedelta(hours=1), status=RunStatus.COMPLETED, results=failed
    )
    with session_factory() as db, db.begin():
        db.execute(update(Run).where(Run.id.in_([first.id, second.id])).values(ingested_at=NOW))

    calls, handler = make_handler()
    with client(handler) as github:
        summary = ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert summary.active == 0
    assert calls == []
    # An idle tick still retries notifications that a previous tick could not post: one per
    # failing run, oldest first.
    assert len(sent) == 2
    assert all("test_a" in content for content in sent)
    assert session.get(Run, first.id).alerts_done_at is not None
    assert session.get(Run, second.id).alerts_done_at is not None


def test_running_tick_twice_is_idempotent(
    make_run, session, session_factory, engine, s3, settings, tmp_path
):
    run = make_run(
        status=RunStatus.RUNNING,
        gh_run_id=1,
        dispatched_at=NOW - timedelta(minutes=5),
    )
    content = zip_report(report_with_name(tmp_path, str(run.uid)), tmp_path)
    calls, handler = make_handler(
        run_status="completed",
        conclusion="success",
        artifacts=artifact_payload(1, content),
        content=content,
    )

    with client(handler) as github:
        first = ingest_tick(engine, session_factory, s3, settings, github, NOW)
        calls_after_first = len(calls)
        second = ingest_tick(engine, session_factory, s3, settings, github, NOW)

    assert first.completed == 1
    assert second.active == 0
    assert len(calls) == calls_after_first
    assert session.scalar(select(func.count()).select_from(PackageResult)) == 2
