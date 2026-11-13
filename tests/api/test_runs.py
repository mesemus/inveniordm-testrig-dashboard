from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from invenio_testrig_dashboard.models import Run
from invenio_testrig_dashboard.runs import mark_infra_error
from invenio_testrig_dashboard.status import RunStatus
from invenio_testrig_dashboard.status import TestStatus as TS

BASE = datetime(2026, 11, 13, 1, 0, tzinfo=UTC)
P = int(TS.PASSED)
F = int(TS.FAILED)
E = int(TS.ERROR)
S = int(TS.SKIPPED)


def test_run_detail_from_the_real_fixture(client, make_run, ingest_fixture):
    previous = make_run("master", BASE, status=RunStatus.COMPLETED)
    run = ingest_fixture("real-2026-11-13", "master", BASE + timedelta(hours=1))

    body = client.get(f"/api/runs/{run.uid}").json()

    assert body["branch"] == "master"
    assert body["status"] == "completed"
    assert body["previous_completed_uid"] == str(previous.uid)
    packages = {package["package"]: package for package in body["packages"]}
    assert "invenio-records-rest" in packages
    assert "original_junit.xml" in packages["invenio-records-rest"]["artifacts"]["junit"]
    assert (
        "artifacts/invenio-records-rest/original_junit.xml"
        in packages["invenio-records-rest"]["artifacts"]["junit"]
    )
    assert "patched_playwright-report" in packages["e2e"]["artifacts"]["playwright_report"]
    assert "junit" not in packages["e2e"]["artifacts"]


def test_run_detail_hides_non_github_urls(client, make_run):
    bad = make_run("master", BASE, status=RunStatus.COMPLETED, gh_run_url="https://evil.example/x")
    good = make_run(
        "master",
        BASE + timedelta(hours=1),
        status=RunStatus.COMPLETED,
        gh_run_url="https://github.com/oarepo/runner/actions/runs/1",
    )

    assert client.get(f"/api/runs/{bad.uid}").json()["gh_run_url"] is None
    assert (
        client.get(f"/api/runs/{good.uid}").json()["gh_run_url"]
        == "https://github.com/oarepo/runner/actions/runs/1"
    )


def test_infra_error_run_has_no_packages(client, ingest_fixture, session_factory, settings):
    run = ingest_fixture("real-2026-11-13", "master", BASE)
    mark_infra_error(session_factory, run.id, "workflow was cancelled", settings)

    body = client.get(f"/api/runs/{run.uid}").json()

    assert body["status"] == "infra_error"
    assert body["packages"] == []
    assert body["error"] == "workflow was cancelled"


def test_run_detail_404_and_invalid_uid(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/runs/{missing}").status_code == 404
    assert client.get(f"/api/runs/{missing}/tests").status_code == 404
    assert client.get(f"/api/runs/{missing}/tests/1").status_code == 404
    assert client.get("/api/runs/not-a-uuid").status_code == 422
    for value in (2**63, int("9" * 40)):
        assert client.get(f"/api/runs/{missing}/tests/{value}").status_code == 422


def test_page_and_filter_bounds(client, make_run):
    run = make_run("master", BASE, status=RunStatus.COMPLETED, results={"pkg::a": P})

    assert client.get("/api/targets/master/runs", params={"page": 10_000}).status_code == 200
    assert client.get("/api/targets/master/runs", params={"page": 10_001}).status_code == 422
    assert client.get("/api/targets/master/runs", params={"page": 2**63}).status_code == 422
    assert client.get(f"/api/runs/{run.uid}/tests", params={"page": 2**63}).status_code == 422
    assert (
        client.get(f"/api/runs/{run.uid}/tests", params={"package": "x" * 201}).status_code == 422
    )


def test_run_tests_default_order_and_filters(client, make_run):
    run = make_run(
        "master",
        BASE,
        status=RunStatus.COMPLETED,
        results={
            "pkg::p_pass": P,
            "pkg::a_fail": F,
            "pkg::b_err": E,
            "pkg::c_skip": S,
            "other::z_fail": F,
        },
    )

    body = client.get(f"/api/runs/{run.uid}/tests").json()

    assert body["total"] == 5
    assert [item["status"] for item in body["items"]] == [
        "failed",
        "failed",
        "error",
        "passed",
        "skipped",
    ]
    assert [item["name"] for item in body["items"]][:2] == ["z_fail", "a_fail"]

    by_package = client.get(f"/api/runs/{run.uid}/tests", params={"package": "pkg"}).json()
    assert by_package["total"] == 4

    failing = client.get(
        f"/api/runs/{run.uid}/tests",
        params=[("status", "failed"), ("status", "error")],
    ).json()
    assert failing["total"] == 3

    assert client.get(f"/api/runs/{run.uid}/tests", params={"q": "a_fail"}).json()["total"] == 1
    # autoescape: '%' is a literal, and no name contains one.
    assert client.get(f"/api/runs/{run.uid}/tests", params={"q": "%"}).json()["total"] == 0

    descending = client.get(
        f"/api/runs/{run.uid}/tests", params={"sort": "name", "order": "desc"}
    ).json()
    assert descending["items"][0]["name"] == "z_fail"

    assert client.get(f"/api/runs/{run.uid}/tests", params={"sort": "bogus"}).status_code == 422
    assert client.get(f"/api/runs/{run.uid}/tests", params={"page": 10}).json()["items"] == []


def test_run_tests_rejects_bad_q(client, make_run):
    run = make_run("master", BASE, status=RunStatus.COMPLETED, results={"pkg::a": P})

    assert client.get(f"/api/runs/{run.uid}/tests", params={"q": "x" * 201}).status_code == 422
    assert client.get(f"/api/runs/{run.uid}/tests", params={"q": "\x00"}).status_code == 422


def test_nul_in_user_text_is_422(client, make_run):
    run = make_run("master", BASE, status=RunStatus.COMPLETED, results={"pkg::test_a": P})
    case_id = client.get(f"/api/runs/{run.uid}/tests").json()["items"][0]["test_case_id"]

    urls = [
        f"/api/runs/{run.uid}/tests?package=a%00b",
        f"/api/test-cases/{case_id}/history?target=a%00b",
        "/api/targets/a%00b/runs",
        "/api/targets/a%00b/matrix",
        "/api/targets/a%00b/trend",
    ]
    for url in urls:
        response = client.get(url)
        assert response.status_code == 422, (url, response.text)


def test_message_is_only_in_the_detail(client, make_run, set_message):
    run = make_run(
        "master", BASE, status=RunStatus.COMPLETED, results={"pkg::a_fail": F, "pkg::b_pass": P}
    )
    set_message(run, "pkg::a_fail", "AssertionError: boom")

    items = client.get(f"/api/runs/{run.uid}/tests").json()["items"]
    failing = next(item for item in items if item["name"] == "a_fail")
    assert failing["has_message"] is True
    passing = next(item for item in items if item["name"] == "b_pass")
    assert passing["has_message"] is False

    detail = client.get(f"/api/runs/{run.uid}/tests/{failing['test_case_id']}").json()
    assert detail["message"] == "AssertionError: boom"
    assert detail["status"] == "failed"


def test_run_test_detail_404(client, make_run):
    run = make_run("master", BASE, status=RunStatus.COMPLETED)

    assert client.get(f"/api/runs/{run.uid}/tests/999999").status_code == 404


def test_q_underscore_is_literal(client, make_run):
    run = make_run(
        "master",
        BASE,
        status=RunStatus.COMPLETED,
        results={"pkg::with_underscore": P, "pkg::withXunderscore": P},
    )

    body = client.get(f"/api/runs/{run.uid}/tests", params={"q": "_"}).json()

    assert body["total"] == 1
    assert body["items"][0]["name"] == "with_underscore"


def test_flaky_window_ignores_other_targets(client, make_run):
    # Master: P, F (one flip). Other target interleaved: F, P. If the window leaked across
    # targets the merged sequence P, F, F, P would have three flips and look flaky.
    make_run("master", BASE, status=RunStatus.COMPLETED, results={"pkg::test_a": P})
    make_run(
        "other",
        BASE + timedelta(hours=1),
        status=RunStatus.COMPLETED,
        results={"pkg::test_a": F},
    )
    master_run = make_run(
        "master",
        BASE + timedelta(hours=2),
        status=RunStatus.COMPLETED,
        results={"pkg::test_a": F},
    )
    other_run = make_run(
        "other",
        BASE + timedelta(hours=3),
        status=RunStatus.COMPLETED,
        results={"pkg::test_a": P},
    )

    for run in (master_run, other_run):
        items = client.get(f"/api/runs/{run.uid}/tests").json()["items"]
        assert items[0]["flaky"] is False


def test_flaky_after_two_flips(client, make_run):
    history = [P, F, P, F]
    run = None
    for index, status in enumerate(history):
        run = make_run(
            "master",
            BASE + timedelta(hours=index),
            status=RunStatus.COMPLETED,
            results={"pkg::test_a": status},
        )

    body = client.get(f"/api/runs/{run.uid}/tests").json()

    assert body["items"][0]["flaky"] is True


def test_one_flip_is_not_flaky(client, make_run):
    history = [P, P, P, F]
    run = None
    for index, status in enumerate(history):
        run = make_run(
            "master",
            BASE + timedelta(hours=index),
            status=RunStatus.COMPLETED,
            results={"pkg::test_a": status},
        )

    body = client.get(f"/api/runs/{run.uid}/tests").json()

    assert body["items"][0]["flaky"] is False


def test_run_detail_reports_live_progress(client, make_run, session_factory):
    running = make_run("master", BASE, status=RunStatus.RUNNING, gh_run_id=1)
    finished = make_run("master", BASE, status=RunStatus.COMPLETED)
    with session_factory() as session, session.begin():
        session.execute(
            update(Run)
            .where(Run.id == running.id)
            .values(
                progress_total=58,
                progress_done=30,
                progress_failed=["invenio-records-rest"],
                progress_at=BASE,
            )
        )

    body = client.get(f"/api/runs/{running.uid}").json()

    assert body["progress_total"] == 58
    assert body["progress_done"] == 30
    assert body["progress_failed"] == ["invenio-records-rest"]
    assert body["progress_at"] is not None
    # A run nothing has been read for reports nulls, not zeroes.
    done_body = client.get(f"/api/runs/{finished.uid}").json()
    assert done_body["progress_total"] is None
    assert done_body["progress_failed"] is None
    assert done_body["progress_at"] is None
