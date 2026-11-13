import uuid
from datetime import UTC, datetime, timedelta

from invenio_testrig_dashboard.status import PackageStatus, RunStatus
from invenio_testrig_dashboard.status import TestStatus as TS

BASE = datetime(2026, 11, 13, 1, 0, tzinfo=UTC)
P = int(TS.PASSED)
F = int(TS.FAILED)
S = int(TS.SKIPPED)


def _pair(make_run, add_packages):
    base = make_run(
        "master",
        BASE,
        status=RunStatus.COMPLETED,
        results={
            "pkg::flipped": P,
            "pkg::stays": P,
            "pkg::gone": P,
            "pkg::skipflip": S,
            "pkg2::notjunit_gone": P,
        },
    )
    head = make_run(
        "master",
        BASE + timedelta(hours=1),
        status=RunStatus.COMPLETED,
        results={
            "pkg::flipped": F,
            "pkg::stays": F,
            "pkg::new": P,
            "pkg::skipflip": P,
        },
    )
    add_packages(base, {"pkg": (PackageStatus.PASSED, True), "pkg2": (PackageStatus.PASSED, True)})
    add_packages(head, {"pkg": (PackageStatus.FAILED, True), "pkg2": (PackageStatus.PASSED, False)})
    return base, head


def test_compare_counts_and_packages(client, make_run, add_packages):
    base, head = _pair(make_run, add_packages)

    body = client.get("/api/compare", params={"base": str(base.uid), "head": str(head.uid)}).json()

    assert body["counts"] == {"flipped": 2, "new": 1, "gone": 1}
    packages = {package["package"]: package for package in body["packages"]}
    assert packages["pkg"]["base_status"] == "passed"
    assert packages["pkg"]["head_status"] == "failed"
    assert packages["pkg"]["changed"] is True
    assert packages["pkg2"]["changed"] is False


def test_compare_tests_by_kind(client, make_run, add_packages):
    base, head = _pair(make_run, add_packages)
    params = {"base": str(base.uid), "head": str(head.uid), "page_size": 100}

    flipped = client.get("/api/compare/tests", params={**params, "kind": "flipped"}).json()
    assert flipped["total"] == 2
    assert {item["name"] for item in flipped["items"]} == {"flipped", "stays"}
    assert all(item["base_status"] == "passed" for item in flipped["items"])
    assert all(item["head_status"] == "failed" for item in flipped["items"])

    new = client.get("/api/compare/tests", params={**params, "kind": "new"}).json()
    assert new["total"] == 1
    assert new["items"][0]["name"] == "new"
    assert new["items"][0]["base_status"] is None
    assert new["items"][0]["head_status"] == "passed"

    gone = client.get("/api/compare/tests", params={**params, "kind": "gone"}).json()
    assert gone["total"] == 1
    assert gone["items"][0]["name"] == "gone"
    assert gone["items"][0]["head_status"] is None


def test_compare_counts_match_the_tabs(client, make_run, add_packages):
    base, head = _pair(make_run, add_packages)
    params = {"base": str(base.uid), "head": str(head.uid)}

    counts = client.get("/api/compare", params=params).json()["counts"]

    for kind in ("flipped", "new", "gone"):
        tab = client.get(
            "/api/compare/tests", params={**params, "kind": kind, "page_size": 100}
        ).json()
        assert counts[kind] == tab["total"]


def test_compare_rejects_different_targets_and_unfinished_runs(client, make_run, add_packages):
    base, head = _pair(make_run, add_packages)
    other = make_run("other", BASE + timedelta(hours=2), status=RunStatus.COMPLETED)
    broken = make_run("master", BASE + timedelta(hours=3), status=RunStatus.INFRA_ERROR)

    different = client.get("/api/compare", params={"base": str(base.uid), "head": str(other.uid)})
    assert different.status_code == 400

    unfinished = client.get("/api/compare", params={"base": str(base.uid), "head": str(broken.uid)})
    assert unfinished.status_code == 400

    assert (
        client.get(
            "/api/compare", params={"base": str(uuid.uuid4()), "head": str(head.uid)}
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/compare/tests",
            params={"base": str(uuid.uuid4()), "head": str(head.uid), "kind": "flipped"},
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/compare/tests",
            params={"base": str(base.uid), "head": str(head.uid), "kind": "bogus"},
        ).status_code
        == 422
    )
