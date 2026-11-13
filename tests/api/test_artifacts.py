import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from invenio_testrig_dashboard.api import create_app
from invenio_testrig_dashboard.api.artifacts import _check_path, expected_artifacts
from invenio_testrig_dashboard.models import PackageResult
from invenio_testrig_dashboard.status import PackageStatus

BASE = datetime(2026, 11, 13, 16, 8, tzinfo=UTC)
JUNIT = "artifacts/invenio-records-rest/original_junit.xml"


@pytest.mark.parametrize("path", ["../x", "/etc/passwd", "a\\b", "a\x00b", ""])
def test_invalid_artifact_paths_are_rejected(path):
    with pytest.raises(HTTPException) as exc:
        _check_path(path)

    assert exc.value.status_code == 400


def test_expected_artifacts_fall_back_for_rows_without_layout():
    run_uid = uuid.uuid4()
    normal = PackageResult(
        package="invenio-records-rest",
        artifact_dir=None,
        side=None,
        status=PackageStatus.PASSED,
        has_junit=True,
    )
    e2e = PackageResult(
        package="e2e",
        artifact_dir=None,
        side=None,
        status=PackageStatus.PASSED,
        has_junit=False,
    )

    assert (
        "artifacts/invenio-records-rest/original_junit.xml"
        in expected_artifacts(run_uid, normal)["junit"]
    )
    assert (
        "artifacts/e2e/patched_playwright-report/index.html"
        in expected_artifacts(run_uid, e2e)["playwright_report"]
    )


def test_existing_artifact_redirects_to_a_presigned_url(client, ingest_fixture):
    run = ingest_fixture("real-2026-11-13", "master", BASE)

    response = client.get(f"/api/runs/{run.uid}/artifacts/{JUNIT}", follow_redirects=False)

    assert response.status_code == 307
    location = response.headers["location"]
    assert "localhost:29000" in location
    assert "invenio-testrig-dashboard-test" in location
    assert str(run.uid) in location


def test_missing_artifact_is_404_with_the_retention_message(client, ingest_fixture):
    run = ingest_fixture("real-2026-11-13", "master", BASE)

    response = client.get(
        f"/api/runs/{run.uid}/artifacts/artifacts/invenio-access/original_freeze.txt",
        follow_redirects=False,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "artifact removed (retention)"}


def test_artifact_path_cannot_escape_the_run_prefix(client, ingest_fixture):
    run = ingest_fixture("real-2026-11-13", "master", BASE)

    # '..' anywhere (httpx2 normalizes a real '../' segment before sending it).
    assert client.get(f"/api/runs/{run.uid}/artifacts/foo/..hidden/x").status_code == 400
    assert client.get(f"/api/runs/{run.uid}/artifacts/foo%5Cbar").status_code == 400
    # backups/ is outside the run prefix, so it can never exist there.
    assert client.get(f"/api/runs/{run.uid}/artifacts/backups/2026-11-13.dump").status_code == 404


def test_unknown_run_is_404(client):
    response = client.get("/api/runs/00000000-0000-0000-0000-000000000000/artifacts/x")

    assert response.status_code == 404


def test_presigned_url_uses_the_public_endpoint(api_settings, ingest_fixture):
    run = ingest_fixture("real-2026-11-13", "master", BASE)
    public = api_settings.model_copy(
        update={"s3_public_endpoint_url": "http://public.example:9000"}
    )

    with TestClient(create_app(public), raise_server_exceptions=False) as client:
        response = client.get(f"/api/runs/{run.uid}/artifacts/{JUNIT}", follow_redirects=False)

    assert response.status_code == 307
    location = response.headers["location"]
    assert "public.example:9000" in location
    assert "localhost:29000" not in location
