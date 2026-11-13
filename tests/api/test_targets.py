from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from invenio_testrig_dashboard.models import Run
from invenio_testrig_dashboard.status import PackageStatus, RunStatus

BASE = datetime(2026, 11, 13, 1, 0, tzinfo=UTC)


def test_targets_overview(client, make_target, make_run):
    make_target("b")
    completed = make_run("a", BASE, status=RunStatus.COMPLETED)
    latest = make_run("a", BASE + timedelta(hours=1), status=RunStatus.QUEUED)
    make_target("a", enabled=False)

    body = client.get("/api/targets").json()

    assert [target["branch"] for target in body] == ["a", "b"]
    a, b = body
    assert a["enabled"] is False
    assert a["latest_run"]["uid"] == str(latest.uid)
    assert a["latest_run"]["status"] == "queued"
    assert a["latest_completed_run"]["uid"] == str(completed.uid)
    assert b["latest_run"] is None
    assert b["latest_completed_run"] is None


def test_target_runs_are_paginated_newest_first_and_filterable(client, make_target, make_run):
    make_target("master")
    first = make_run("master", BASE, status=RunStatus.COMPLETED)
    second = make_run("master", BASE + timedelta(hours=1), status=RunStatus.INFRA_ERROR)
    third = make_run("master", BASE + timedelta(hours=2), status=RunStatus.COMPLETED)

    body = client.get("/api/targets/master/runs").json()

    assert body["total"] == 3
    assert body["page"] == 0
    assert body["page_size"] == 50
    assert [item["uid"] for item in body["items"]] == [
        str(third.uid),
        str(second.uid),
        str(first.uid),
    ]

    filtered = client.get("/api/targets/master/runs", params={"status": "infra_error"}).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["uid"] == str(second.uid)

    page = client.get("/api/targets/master/runs", params={"page": 1, "page_size": 2}).json()
    assert page["total"] == 3
    assert [item["uid"] for item in page["items"]] == [str(first.uid)]

    assert client.get("/api/targets/master/runs", params={"page_size": 101}).status_code == 422
    assert client.get("/api/targets/nope/runs").status_code == 404


def test_progress_is_reported_on_the_list_and_the_overview(
    client, make_target, make_run, session_factory
):
    make_target("master")
    running = make_run("master", BASE, status=RunStatus.RUNNING, gh_run_id=1)
    with session_factory() as db, db.begin():
        db.execute(
            update(Run)
            .where(Run.id == running.id)
            .values(
                progress_total=58,
                progress_done=30,
                progress_failed=["invenio-records-rest"],
                progress_at=BASE,
            )
        )

    item = client.get("/api/targets/master/runs").json()["items"][0]
    assert (item["progress_total"], item["progress_done"]) == (58, 30)
    assert item["progress_failed"] == ["invenio-records-rest"]
    assert item["progress_at"] is not None

    latest = client.get("/api/targets").json()[0]["latest_run"]
    assert latest["progress_total"] == 58
    assert latest["progress_done"] == 30
    assert latest["progress_failed"] == ["invenio-records-rest"]


def test_matrix_has_one_column_per_completed_run_and_nulls(
    client, make_target, make_run, add_packages
):
    make_target("master")
    first = make_run("master", BASE, status=RunStatus.COMPLETED)
    second = make_run("master", BASE + timedelta(hours=1), status=RunStatus.COMPLETED)
    add_packages(
        first,
        {"pkg-a": (PackageStatus.PASSED, True), "pkg-b": (PackageStatus.FAILED, True)},
    )
    add_packages(
        second,
        {"pkg-a": (PackageStatus.FAILED, True), "pkg-c": (PackageStatus.PASSED, True)},
    )

    body = client.get("/api/targets/master/matrix").json()

    assert [run["uid"] for run in body["runs"]] == [str(first.uid), str(second.uid)]
    cells = {package["package"]: package["cells"] for package in body["packages"]}
    assert cells["pkg-a"] == ["passed", "failed"]
    assert cells["pkg-b"] == ["failed", None]
    assert cells["pkg-c"] == [None, "passed"]


def test_trend_computes_duration_and_returns_oldest_first(
    client, make_target, make_run, session_factory
):
    make_target("master")
    older = make_run("master", BASE, status=RunStatus.COMPLETED, started_at=BASE)
    newer = make_run(
        "master", BASE + timedelta(hours=1), status=RunStatus.COMPLETED, started_at=BASE
    )
    with session_factory() as db, db.begin():
        db.execute(
            update(Run)
            .where(Run.id == older.id)
            .values(finished_at=BASE + timedelta(seconds=90), tests_total=10, tests_failed=1)
        )
        db.execute(
            update(Run)
            .where(Run.id == newer.id)
            .values(
                finished_at=BASE + timedelta(seconds=120),
                tests_total=12,
                tests_failed=2,
                packages_failed=1,
            )
        )

    body = client.get("/api/targets/master/trend").json()

    assert [point["uid"] for point in body] == [str(older.uid), str(newer.uid)]
    assert body[0]["duration_s"] == 90.0
    assert body[1]["duration_s"] == 120.0
    assert body[1]["tests_total"] == 12
    assert body[1]["packages_failed"] == 1


def test_matrix_and_trend_only_consider_completed_runs(client, make_target, make_run):
    make_target("master")
    make_run("master", BASE, status=RunStatus.INFRA_ERROR)

    assert client.get("/api/targets/master/matrix").json() == {"runs": [], "packages": []}
    assert client.get("/api/targets/master/trend").json() == []
    assert client.get("/api/targets/nope/matrix").status_code == 404
    assert client.get("/api/targets/nope/trend").status_code == 404
