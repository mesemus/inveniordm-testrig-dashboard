import time
from datetime import UTC, datetime

import pytest

from invenio_testrig_dashboard.ingest import store_run
from invenio_testrig_dashboard.status import RunStatus
from tests.reportdata import write_volume_report


@pytest.mark.slow
def test_run_tests_is_fast_with_the_volume_report(
    client, make_run, session_factory, s3, settings, tmp_path
):
    report = tmp_path / "volume"
    write_volume_report(report)
    run = make_run("master", datetime.now(UTC), status=RunStatus.QUEUED)
    assert store_run(session_factory, s3, settings, run, report, alert=False) is (
        RunStatus.COMPLETED
    )

    started = time.perf_counter()
    response = client.get(
        f"/api/runs/{run.uid}/tests",
        params={"package": "pkg-00", "page_size": 100, "q": "test_1"},
    )
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert response.json()["total"] > 0
    assert elapsed < 1.0
