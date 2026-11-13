import time

from sqlalchemy import select

from invenio_testrig_dashboard.models import Target as TargetRow
from invenio_testrig_dashboard.settings import Target as TargetConfig
from invenio_testrig_dashboard.targets import sync_targets


def targets(*branches: str, schedule: str = "0 1 * * *") -> list[TargetConfig]:
    return [TargetConfig(branch=branch, schedule=schedule) for branch in branches]


def rows(session) -> dict[str, tuple[str, bool]]:
    # Select columns, not ORM objects, so values always come fresh from the database.
    result = session.execute(select(TargetRow.branch, TargetRow.schedule, TargetRow.enabled))
    return {branch: (schedule, enabled) for branch, schedule, enabled in result}


def test_sync_inserts_all_targets(session):
    changes = sync_targets(session, targets("master", "14.x"))

    assert set(rows(session)) == {"master", "14.x"}
    assert all(enabled for _, enabled in rows(session).values())
    assert changes.added == ["master", "14.x"]


def test_sync_updates_changed_schedule(session):
    sync_targets(session, targets("master"))

    changes = sync_targets(session, targets("master", schedule="0 2 * * *"))

    assert rows(session)["master"] == ("0 2 * * *", True)
    assert changes.updated == ["master"]


def test_sync_disables_removed_target_but_keeps_row(session):
    sync_targets(session, targets("master", "14.x"))

    changes = sync_targets(session, targets("master"))

    assert rows(session)["14.x"] == ("0 1 * * *", False)
    assert changes.disabled == ["14.x"]


def test_sync_reenables_target(session):
    sync_targets(session, targets("master", "14.x"))
    sync_targets(session, targets("master"))

    changes = sync_targets(session, targets("master", "14.x"))

    assert rows(session)["14.x"] == ("0 1 * * *", True)
    assert changes.updated == ["14.x"]


def test_sync_empty_list_disables_everything(session):
    sync_targets(session, targets("master", "14.x"))

    changes = sync_targets(session, [])

    assert all(not enabled for _, enabled in rows(session).values())
    assert changes.disabled == ["14.x", "master"]


def test_unchanged_target_keeps_updated_at(session):
    sync_targets(session, targets("master"))
    before = session.scalar(select(TargetRow.updated_at))
    session.commit()  # now() is the transaction start time, so use a new transaction
    time.sleep(0.01)

    changes = sync_targets(session, targets("master"))

    assert changes.updated == []
    assert session.scalar(select(TargetRow.updated_at)) == before
