import logging
import threading
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx2
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from invenio_testrig_dashboard import dispatch
from invenio_testrig_dashboard.api import create_app, reruns
from invenio_testrig_dashboard.db import try_advisory_lock
from invenio_testrig_dashboard.models import Run
from invenio_testrig_dashboard.status import RunStatus, RunTrigger

LOGIN = "OctoCat"


def handler(request: httpx2.Request) -> httpx2.Response:
    if request.url.path == "/login/oauth/access_token":
        return httpx2.Response(200, json={"access_token": "token"})
    if request.url.path == "/user":
        return httpx2.Response(200, json={"login": LOGIN})
    return httpx2.Response(404)


def recording_runner(calls: list[list[str]], result=None):
    def run(job: dispatch.TestrigJob) -> dispatch.TestrigResult:
        calls.append(job.command)
        return result if result is not None else dispatch.TestrigResult(0, "", "")

    return run


def make_client(settings, runner=None) -> TestClient:
    # Never run the real testrig in an API test: the default runner just succeeds.
    return TestClient(
        create_app(
            settings,
            http_transport=httpx2.MockTransport(handler),
            run_testrig=runner or recording_runner([]),
        )
    )


def login(client: TestClient) -> None:
    start = client.get("/api/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
    assert callback.status_code == 302


def rerun(client: TestClient, branch: str = "master", json_content: bool = True):
    headers = {"Content-Type": "application/json"} if json_content else {}
    return client.post(f"/api/targets/{branch}/runs", headers=headers)


def run_count(session_factory) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(Run)) or 0


def test_anonymous_cannot_request_a_run(oauth_settings, make_target, session_factory):
    make_target("master")

    with make_client(oauth_settings) as client:
        response = rerun(client)

    assert response.status_code == 401
    assert run_count(session_factory) == 0


def test_non_maintainer_is_forbidden(oauth_settings, make_target, session_factory):
    make_target("master")
    settings = oauth_settings.model_copy(update={"maintainers": ["someoneelse"]})

    with make_client(settings) as client:
        login(client)
        response = rerun(client)

    assert response.status_code == 403
    assert run_count(session_factory) == 0


def test_maintainer_creates_a_queued_manual_run(oauth_settings, make_target, session_factory):
    make_target("master")

    with make_client(oauth_settings) as client:
        login(client)
        response = rerun(client)

    assert response.status_code == 201, response.text
    uid = response.json()["uid"]
    with session_factory() as session:
        run = session.scalar(select(Run).where(Run.uid == uid))
    assert run.status is RunStatus.QUEUED
    assert run.trigger is RunTrigger.MANUAL
    assert run.triggered_by == LOGIN


def test_a_second_request_conflicts_with_details(oauth_settings, make_target, session_factory):
    make_target("master")

    with make_client(oauth_settings) as client:
        login(client)
        first = rerun(client)
        second = rerun(client)

    assert first.status_code == 201
    uid = first.json()["uid"]
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["uid"] == uid
    # The first request's run was already dispatched (C1), so it is queued, not requested.
    assert detail["status"] == "queued"
    assert detail["lost_at"] is not None
    assert run_count(session_factory) == 1


def test_two_concurrent_requests_create_only_one_run(oauth_settings, make_target, session_factory):
    """Two sessions POST at the same moment: the target row lock serialises them."""
    make_target("master")
    barrier = threading.Barrier(2)
    statuses: list[int] = []
    failures: list[BaseException] = []

    def attempt() -> None:
        try:
            with make_client(oauth_settings) as client:
                login(client)
                barrier.wait(timeout=10)
                statuses.append(rerun(client).status_code)
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            failures.append(exc)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not failures
    assert not any(thread.is_alive() for thread in threads)
    assert sorted(statuses) == [201, 409]
    assert run_count(session_factory) == 1


def run_status(session_factory, uid: str) -> Run:
    with session_factory() as session:
        return session.scalar(select(Run).where(Run.uid == uid))


def test_the_re_run_starts_immediately(oauth_settings, make_target, session_factory):
    make_target("master")
    calls: list[list[str]] = []

    with make_client(oauth_settings, recording_runner(calls)) as client:
        login(client)
        response = rerun(client)

    assert response.status_code == 201, response.text
    assert len(calls) == 1
    assert "@master" in " ".join(calls[0])
    run = run_status(session_factory, response.json()["uid"])
    assert run.status is RunStatus.QUEUED
    assert run.dispatched_at is not None


def test_a_failing_runner_is_still_201_and_marks_the_run(
    oauth_settings, make_target, session_factory
):
    make_target("master")
    calls: list[list[str]] = []
    failed = dispatch.TestrigResult(1, "", "boom")

    with make_client(oauth_settings, recording_runner(calls, failed)) as client:
        login(client)
        response = rerun(client)

    assert response.status_code == 201
    assert run_status(session_factory, response.json()["uid"]).status is RunStatus.INFRA_ERROR


def test_a_raising_runner_is_still_201_and_marked_by_dispatch(
    oauth_settings, make_target, session_factory, caplog
):
    make_target("master")

    def runner(job: dispatch.TestrigJob) -> dispatch.TestrigResult:
        raise RuntimeError("nope")

    with caplog.at_level(logging.ERROR):
        with make_client(oauth_settings, runner) as client:
            login(client)
            response = rerun(client)

    assert response.status_code == 201
    # dispatch's own handler marks the run and logs; _dispatch_now is not involved.
    assert "dispatching failed" in caplog.text
    assert run_status(session_factory, response.json()["uid"]).status is RunStatus.INFRA_ERROR


def test_the_background_task_never_raises(oauth_settings, make_target, caplog, monkeypatch):
    """Without `_dispatch_now`'s except, TestClient would re-raise the tick's exception."""
    make_target("master")

    def boom(*args, **kwargs):
        raise RuntimeError("tick failed")

    monkeypatch.setattr(reruns.dispatch, "dispatch_tick", boom)

    with caplog.at_level(logging.ERROR):
        with make_client(oauth_settings) as client:
            login(client)
            response = rerun(client)

    assert response.status_code == 201
    assert "immediate dispatch of master failed" in caplog.text


def test_a_busy_dispatch_lock_falls_back_to_the_cron(
    oauth_settings, make_target, session_factory, engine, monkeypatch
):
    make_target("master")
    calls: list[list[str]] = []
    # One attempt, not fifteen minutes of retries.
    monkeypatch.setattr(reruns, "DISPATCH_RETRY_FOR", 0)

    with try_advisory_lock(engine, dispatch.DISPATCH_LOCK_KEY) as locked:
        assert locked
        with make_client(oauth_settings, recording_runner(calls)) as client:
            login(client)
            response = rerun(client)

    assert response.status_code == 201
    assert calls == []
    assert run_status(session_factory, response.json()["uid"]).status is RunStatus.REQUESTED


def test_the_cron_cannot_start_the_same_run_again(
    oauth_settings, make_target, session_factory, engine
):
    make_target("master")
    calls: list[list[str]] = []
    runner = recording_runner(calls)

    with make_client(oauth_settings, runner) as client:
        login(client)
        assert rerun(client).status_code == 201
    assert len(calls) == 1

    dispatch.dispatch_tick(
        engine,
        session_factory,
        oauth_settings,
        now=datetime.now(UTC),
        run_testrig=runner,
        only_branch="master",
    )

    assert len(calls) == 1


def test_unknown_and_disabled_targets_are_404(oauth_settings, make_target, session_factory):
    make_target("old", enabled=False)

    with make_client(oauth_settings) as client:
        login(client)
        unknown = rerun(client, branch="nope")
        disabled = rerun(client, branch="old")

    assert unknown.status_code == 404
    assert disabled.status_code == 404
    assert run_count(session_factory) == 0


def test_a_non_json_content_type_is_rejected(oauth_settings, make_target, session_factory):
    make_target("master")

    with make_client(oauth_settings) as client:
        login(client)
        response = client.post("/api/targets/master/runs", data={"branch": "master"})

    assert response.status_code == 415
    assert run_count(session_factory) == 0
