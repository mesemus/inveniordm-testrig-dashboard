from datetime import UTC, datetime, timedelta

from invenio_testrig_dashboard.status import RunStatus
from invenio_testrig_dashboard.status import TestStatus as TS

BASE = datetime(2026, 11, 13, 1, 0, tzinfo=UTC)
P = int(TS.PASSED)
F = int(TS.FAILED)


def test_test_case_detail_and_history(client, make_run):
    first = make_run("master", BASE, status=RunStatus.COMPLETED, results={"pkg::test_a": P})
    make_run(
        "master",
        BASE + timedelta(hours=1),
        status=RunStatus.COMPLETED,
        results={"pkg::other": F},
    )
    last = make_run(
        "master",
        BASE + timedelta(hours=2),
        status=RunStatus.COMPLETED,
        results={"pkg::test_a": F},
    )
    case_id = client.get(f"/api/runs/{first.uid}/tests").json()["items"][0]["test_case_id"]

    detail = client.get(f"/api/test-cases/{case_id}").json()

    assert detail["package"] == "pkg"
    assert detail["name"] == "test_a"
    assert detail["first_seen_run_uid"] == str(first.uid)

    history = client.get(f"/api/test-cases/{case_id}/history", params={"target": "master"}).json()

    assert history["items"][0]["uid"] == str(first.uid)
    assert history["items"][-1]["uid"] == str(last.uid)
    assert [item["status"] for item in history["items"]] == ["passed", None, "failed"]
    assert history["flaky"] is False


def test_history_flaky_flag(client, make_run):
    run = None
    for index, status in enumerate([P, F, P, F]):
        run = make_run(
            "master",
            BASE + timedelta(hours=index),
            status=RunStatus.COMPLETED,
            results={"pkg::test_a": status},
        )
    case_id = client.get(f"/api/runs/{run.uid}/tests").json()["items"][0]["test_case_id"]

    body = client.get(f"/api/test-cases/{case_id}/history", params={"target": "master"}).json()

    assert body["flaky"] is True


def test_history_flaky_window_does_not_shrink_with_limit(client, make_run):
    run = None
    for index, status in enumerate([P, F, P, F, P, P, P]):
        run = make_run(
            "master",
            BASE + timedelta(hours=index),
            status=RunStatus.COMPLETED,
            results={"pkg::test_a": status},
        )
    case_id = client.get(f"/api/runs/{run.uid}/tests").json()["items"][0]["test_case_id"]

    body = client.get(
        f"/api/test-cases/{case_id}/history", params={"target": "master", "limit": 3}
    ).json()

    assert body["flaky"] is True
    assert len(body["items"]) == 3


def test_test_case_errors(client, make_run):
    run = make_run("master", BASE, status=RunStatus.COMPLETED, results={"pkg::test_a": P})
    case_id = client.get(f"/api/runs/{run.uid}/tests").json()["items"][0]["test_case_id"]

    assert client.get("/api/test-cases/999999").status_code == 404
    assert client.get(f"/api/test-cases/{case_id}/history").status_code == 422
    assert (
        client.get(f"/api/test-cases/{case_id}/history", params={"target": "nope"}).status_code
        == 404
    )
    for value in (2**63, int("9" * 40)):
        assert client.get(f"/api/test-cases/{value}").status_code == 422
        assert (
            client.get(f"/api/test-cases/{value}/history", params={"target": "master"}).status_code
            == 422
        )
    assert (
        client.get(f"/api/test-cases/{case_id}/history", params={"target": "x" * 201}).status_code
        == 422
    )
