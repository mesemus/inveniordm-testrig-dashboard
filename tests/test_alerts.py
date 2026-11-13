import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import update

from invenio_testrig_dashboard import alerts, history
from invenio_testrig_dashboard import dispatch as dispatch_module
from invenio_testrig_dashboard.ingest import store_run
from invenio_testrig_dashboard.models import Run
from invenio_testrig_dashboard.status import RunStatus
from invenio_testrig_dashboard.status import TestStatus as TS

FIXTURES = Path(__file__).parent / "fixtures" / "runs"
# Fixed, so failures never depend on the wall clock (retry windows use DB time + this).
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)

P = int(TS.PASSED)
F = int(TS.FAILED)
E = int(TS.ERROR)
S = int(TS.SKIPPED)
X = "infra_error"


@pytest.fixture
def posts(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(
        alerts.discord,
        "post",
        lambda webhook_url, content, transport=None: sent.append(content) or True,
    )
    return sent


def _history(make_run, history, name: str = "test_a", target: str = "master"):
    runs = []
    for index, entry in enumerate(history):
        created_at = NOW - timedelta(hours=1) + index * timedelta(minutes=5)
        if entry == X:
            run = make_run(target, created_at, status=RunStatus.INFRA_ERROR)
        else:
            results = None if entry is None else {f"pkg::{name}": entry}
            run = make_run(target, created_at, status=RunStatus.COMPLETED, results=results)
        runs.append(run)
    return runs


def _alert(session_factory, settings, run):
    alerts.alert_completed_run(session_factory, settings, run.id, clock=lambda: NOW)


@pytest.mark.parametrize(
    ("history", "expected"),
    [
        # Every completed run with a failed test posts (since 2026-11-15).
        ([F], "New:"),  # no previous run: everything failing is new
        ([P, F], "New:"),
        ([None, F], "New:"),  # absent before
        ([F, F], "Still failing:"),
        ([P, F, F, F], "Still failing:"),  # no snoozing: every run posts
        ([P, E], "New:"),
        ([F, X, F], "Still failing:"),  # infra_error runs don't count as "previous"
        # A run without failed tests posts only when the previous run's failures were fixed.
        ([F, P], "all tests pass again"),
        ([F, X, P], "all tests pass again"),
        ([P], None),
        ([P, P], None),
        ([F, S], None),  # skipped is neither failing nor fixed
        ([F, None], None),  # the test didn't run
    ],
)
def test_run_scenarios(posts, make_run, session_factory, settings, history, expected):
    runs = _history(make_run, history)

    _alert(session_factory, settings, runs[-1])

    if expected is None:
        assert posts == []
    else:
        assert len(posts) == 1
        assert "test_a" in posts[0]
        assert expected in posts[0]


def test_new_still_and_fixed_in_one_message(posts, make_run, session_factory, settings):
    base = NOW - timedelta(hours=1)
    make_run(
        "master",
        base,
        status=RunStatus.COMPLETED,
        results={"pkg::test_old": F, "pkg::test_fixed": F, "pkg::test_new": P},
    )
    run = make_run(
        "master",
        base + timedelta(minutes=5),
        status=RunStatus.COMPLETED,
        results={"pkg::test_old": F, "pkg::test_fixed": P, "pkg::test_new": E},
    )

    _alert(session_factory, settings, run)

    (text,) = posts
    assert text.startswith("**[master]** 2 failed test(s), 1 new")
    new, rest = text.split("**Still failing:**")
    still, fixed = rest.split("**Fixed:**")
    assert "test_new" in new and "test_old" in still and "test_fixed" in fixed
    assert "compare?base=" in text


def test_every_run_is_notified_even_a_late_older_one(posts, make_run, session_factory, settings):
    older = make_run(
        "master", NOW - timedelta(hours=2), status=RunStatus.COMPLETED, results={"pkg::test_a": F}
    )
    newer = make_run(
        "master", NOW - timedelta(hours=1), status=RunStatus.COMPLETED, results={"pkg::test_a": F}
    )
    with session_factory() as db, db.begin():
        db.execute(update(Run).where(Run.id.in_([older.id, newer.id])).values(ingested_at=NOW))

    _alert(session_factory, settings, newer)
    _alert(session_factory, settings, older)

    assert len(posts) == 2
    assert "Still failing:" in posts[0]  # compared with the older run
    assert "New:" in posts[1]  # the older run has no predecessor
    alerts.retry_pending(session_factory, settings, datetime.now(UTC) + timedelta(minutes=1))
    assert len(posts) == 2


def test_suite_pseudo_case_counts_as_a_failed_test(posts, make_run, session_factory, settings):
    runs = _history(make_run, [P, E], name="<suite>")

    _alert(session_factory, settings, runs[-1])

    assert len(posts) == 1
    assert "<suite>" in posts[0]


def test_an_alert_is_not_posted_twice(posts, make_run, session_factory, settings):
    runs = _history(make_run, [P, F])

    _alert(session_factory, settings, runs[-1])
    _alert(session_factory, settings, runs[-1])

    assert len(posts) == 1


def test_retry_posts_every_pending_run_oldest_first(
    monkeypatch, make_run, session_factory, settings
):
    runs = _history(make_run, [F, F])
    with session_factory() as db, db.begin():
        db.execute(update(Run).where(Run.id.in_([r.id for r in runs])).values(ingested_at=NOW))
    sent: list[str] = []
    monkeypatch.setattr(
        alerts.discord,
        "post",
        lambda webhook_url, content, transport=None: sent.append(content) or True,
    )

    alerts.retry_pending(session_factory, settings, NOW + timedelta(minutes=1))

    assert len(sent) == 2
    assert "New:" in sent[0] and "Still failing:" in sent[1]


def test_digest_is_truncated_to_discords_limit():
    tests = [alerts.ChangedTest(i, "pkg", "cls", f"test_{i}") for i in range(500)]
    head = uuid.uuid4()

    text = alerts.build_digest("master", head, tests, [], [], uuid.uuid4(), "https://dash")

    assert len(text) <= 2000
    assert "… and " in text and "more" in text
    assert "https://dash/compare?base=" in text


def test_a_very_long_test_name_cannot_fill_the_message():
    test = alerts.ChangedTest(1, "invenio-s3", "cls", "x" * 10_000)

    text = alerts.build_digest("master", uuid.uuid4(), [test], [], [], uuid.uuid4(), "https://dash")

    assert len(text) <= 2000
    assert "x" * 130 not in text


def test_digest_keeps_the_compare_link_when_the_header_cannot_fit():
    public_url = "https://dash/" + "x" * 1500
    head, base = uuid.uuid4(), uuid.uuid4()

    text = alerts.build_digest(
        "master", head, [alerts.ChangedTest(1, "pkg", "cls", "test_a")], [], [], base, public_url
    )

    assert len(text) <= 2000
    assert f"compare?base={base}&head={head}" in text


def test_digest_uses_the_frontend_routes():
    test = alerts.ChangedTest(7, "pkg", "cls", "test_a")
    head, base = uuid.uuid4(), uuid.uuid4()

    text = alerts.build_digest("master", head, [test], [], [], base, "https://dash")

    assert f"https://dash/runs/{head}" in text
    assert "https://dash/test-cases/7?target=master" in text
    assert f"https://dash/compare?base={base}&head={head}" in text


def test_digest_without_a_previous_run_has_no_compare_link():
    test = alerts.ChangedTest(7, "pkg", "cls", "test_a")

    text = alerts.build_digest("master", uuid.uuid4(), [test], [], [], None, "https://dash")

    assert text.startswith("**[master]** 1 failed test(s) — https://dash/runs/")
    assert "compare" not in text


def test_digest_encodes_the_branch_in_the_history_link():
    test = alerts.ChangedTest(7, "pkg", "cls", "test_a")
    head, base = uuid.uuid4(), uuid.uuid4()

    text = alerts.build_digest("feat#1", head, [test], [], [], base, "https://dash")

    assert "https://dash/test-cases/7?target=feat%231" in text


def test_ops_alert_deduplicates_within_its_window(posts, settings, session_factory, session):
    alerts.ops_alert(session_factory, settings, "k", "boom", timedelta(hours=1), clock=lambda: NOW)
    alerts.ops_alert(
        session_factory,
        settings,
        "k",
        "boom",
        timedelta(hours=1),
        clock=lambda: NOW + timedelta(minutes=30),
    )
    assert len(posts) == 1

    alerts.ops_alert(
        session_factory,
        settings,
        "k",
        "boom",
        timedelta(hours=1),
        clock=lambda: NOW + timedelta(hours=2),
    )
    assert len(posts) == 2


def test_ops_alerts_fall_back_to_the_main_webhook(monkeypatch, settings, session_factory, session):
    urls: list[str | None] = []
    monkeypatch.setattr(
        alerts.discord,
        "post",
        lambda webhook_url, content, transport=None: urls.append(webhook_url) or True,
    )
    main = SecretStr("https://discord.com/api/webhooks/1/main")
    settings = settings.model_copy(update={"discord_webhook_url": main})

    alerts.ops_alert(session_factory, settings, "k", "text", timedelta(hours=1), clock=lambda: NOW)

    assert urls == [main.get_secret_value()]


def test_token_expiry_warns_only_within_fourteen_days(posts, settings, session_factory, session):
    alerts.check_token_expiry(
        session_factory, settings, NOW + timedelta(days=20), clock=lambda: NOW
    )
    assert posts == []

    alerts.check_token_expiry(
        session_factory, settings, NOW + timedelta(days=10), clock=lambda: NOW
    )
    assert len(posts) == 1
    assert "expires" in posts[0]


def test_ops_alert_posts_without_dedupe_when_the_db_is_unreachable(monkeypatch, settings):
    sent: list[str] = []
    monkeypatch.setattr(
        alerts.discord,
        "post",
        lambda webhook_url, content, transport=None: sent.append(content) or True,
    )

    class BrokenSessionFactory:
        def __call__(self):
            raise RuntimeError("database is down")

    factory = BrokenSessionFactory()
    alerts.ops_alert(factory, settings, "crash:dispatch", "boom", timedelta(hours=1))
    alerts.ops_alert(factory, settings, "crash:dispatch", "boom", timedelta(hours=1))

    assert len(sent) == 2


def test_stale_target_check(posts, make_target, make_run, session_factory, settings):
    make_target("old", schedule="0 1 * * *")
    make_target("fresh", schedule="0 1 * * *")
    make_run("old", NOW - timedelta(days=3), status=RunStatus.COMPLETED)
    make_run("fresh", NOW - timedelta(days=1), status=RunStatus.COMPLETED)

    alerts.check_stale_targets(session_factory, settings, clock=lambda: NOW)

    assert len(posts) == 1
    assert "old" in posts[0]
    assert "fresh" not in posts[0]


def test_dispatch_tick_runs_the_stale_check_but_dry_run_does_not(
    posts, make_target, make_run, engine, session_factory, settings
):
    make_target("stale", schedule="0 1 * * *")
    make_run("stale", NOW - timedelta(days=3), status=RunStatus.COMPLETED)

    def runner(job):
        return dispatch_module.TestrigResult(0, "", "")

    dispatch_module.dispatch_tick(
        engine, session_factory, settings, NOW, runner, dry_run=True, clock=lambda: NOW
    )
    assert posts == []

    dispatch_module.dispatch_tick(engine, session_factory, settings, NOW, runner, clock=lambda: NOW)
    assert len(posts) == 1
    assert "stale" in posts[0]


def test_storing_an_invalid_run_posts_an_ops_alert(posts, make_run, session_factory, s3, settings):
    run = make_run("master", NOW, status=RunStatus.QUEUED)

    status = store_run(session_factory, s3, settings, run, FIXTURES / "bad_schema", alert=True)

    assert status is RunStatus.INFRA_ERROR
    assert any(str(run.uid) in content and "infra_error" in content for content in posts)


def test_discord_failure_keeps_the_alert_pending(
    monkeypatch, make_run, session_factory, s3, settings
):
    fixture = FIXTURES / "failing"
    first = make_run("master", NOW - timedelta(hours=2), status=RunStatus.QUEUED)
    assert (
        store_run(session_factory, s3, settings, first, fixture, alert=True) is RunStatus.COMPLETED
    )

    second = make_run("master", NOW - timedelta(hours=1), status=RunStatus.QUEUED)

    def failing_post(webhook_url, content, transport=None):
        raise RuntimeError("discord is down")

    monkeypatch.setattr(alerts.discord, "post", failing_post)
    assert (
        store_run(session_factory, s3, settings, second, fixture, alert=True) is RunStatus.COMPLETED
    )
    with session_factory() as db:
        assert db.get(Run, second.id).alerts_done_at is None

    sent: list[str] = []
    monkeypatch.setattr(
        alerts.discord,
        "post",
        lambda webhook_url, content, transport=None: sent.append(content) or True,
    )
    alerts.retry_pending(session_factory, settings, datetime.now(UTC) + timedelta(minutes=1))

    assert len(sent) == 1
    with session_factory() as db:
        assert db.get(Run, second.id).alerts_done_at is not None


@pytest.mark.parametrize(
    ("module", "forbidden"),
    [
        (alerts, {"runs", "ingest", "ingest_github", "dispatch", "cli"}),
        (history, {"runs", "ingest", "ingest_github", "dispatch", "cli", "alerts", "api"}),
    ],
)
def test_leaf_modules_have_no_import_cycles(module, forbidden):
    """These are imported by runs/ingest/api, so they must not import those back."""
    import ast

    tree = ast.parse(Path(module.__file__).read_text())
    imported = {
        alias.name if isinstance(node, ast.Import) else (node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    imported |= {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and not node.module
        for alias in node.names
    }
    assert not {name.rsplit(".", 1)[-1] for name in imported} & forbidden


def test_seeding_without_alert_never_alerts_later(posts, make_run, session_factory, s3, settings):
    fixture = FIXTURES / "failing"
    first = make_run("master", NOW - timedelta(hours=2), status=RunStatus.QUEUED)
    second = make_run("master", NOW - timedelta(hours=1), status=RunStatus.QUEUED)

    store_run(session_factory, s3, settings, first, fixture, alert=False)
    store_run(session_factory, s3, settings, second, fixture, alert=False)
    # The next ingest tick's retry must not post the skipped digest either.
    alerts.retry_pending(session_factory, settings, datetime.now(UTC))

    assert posts == []
    with session_factory() as db:
        assert db.get(Run, second.id).alerts_done_at is not None


def test_seeding_with_alert_posts_every_failing_run(posts, make_run, session_factory, s3, settings):
    fixture = FIXTURES / "failing"
    first = make_run("master", NOW - timedelta(hours=2), status=RunStatus.QUEUED)
    second = make_run("master", NOW - timedelta(hours=1), status=RunStatus.QUEUED)

    store_run(session_factory, s3, settings, first, fixture, alert=True)
    store_run(session_factory, s3, settings, second, fixture, alert=True)

    assert len(posts) == 2
    assert "New:" in posts[0] and "Still failing:" not in posts[0]
    assert "Still failing:" in posts[1] and "New:" not in posts[1]


def test_invalid_seed_posts_no_ops_alert(posts, make_run, session_factory, s3, settings):
    run = make_run("master", NOW, status=RunStatus.QUEUED)

    status = store_run(session_factory, s3, settings, run, FIXTURES / "bad_schema", alert=False)

    assert status is RunStatus.INFRA_ERROR
    assert posts == []


def test_catalogue_windows_match_the_design():
    assert alerts.RUN_FAILED_WINDOW == timedelta(days=365)
    assert alerts.GITHUB_AUTH_WINDOW == timedelta(hours=6)
    assert alerts.TOKEN_EXPIRY_WINDOW == timedelta(hours=24)
    assert alerts.STALE_TARGET_WINDOW == timedelta(hours=24)
    assert alerts.CRASH_WINDOW == timedelta(hours=1)
