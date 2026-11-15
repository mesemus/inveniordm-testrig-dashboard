import json
import logging
import tomllib
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from invenio_testrig_dashboard import __version__
from invenio_testrig_dashboard.api import create_app

ALL_PATHS = {
    "/api/compare",
    "/api/compare/tests",
    "/api/health",
    "/api/runs/{uid}",
    "/api/runs/{uid}/artifacts/{path}",
    "/api/runs/{uid}/tests",
    "/api/runs/{uid}/tests/{test_case_id}",
    "/api/targets",
    "/api/targets/{branch}/matrix",
    "/api/targets/{branch}/runs",
    "/api/targets/{branch}/trend",
    "/api/test-cases/{case_id}",
    "/api/test-cases/{case_id}/history",
}


def test_health_ok(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_the_version_is_pyprojects(settings):
    # One source: pyproject.toml -> the installed metadata -> /api/health, the UI footer,
    # the OpenAPI document and the image tag (`make image`).
    pyproject = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text())
    assert __version__ == pyproject["project"]["version"]
    assert create_app(settings).version == __version__


def test_a_database_outage_is_a_503_not_a_500(settings, caplog):
    # A realistic password: the logging filter redacts it everywhere, a one-letter one too.
    dead = settings.model_copy(
        update={"database_url": SecretStr("postgresql://user:dead-password@127.0.0.1:1/db")}
    )

    with (
        caplog.at_level(logging.WARNING),
        TestClient(create_app(dead), raise_server_exceptions=False) as client,
    ):
        response = client.get("/api/targets")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
    # One warning line, no traceback per request while the database is down.
    (record,) = [
        r for r in caplog.records if "database unavailable for /api/targets" in r.getMessage()
    ]
    assert record.levelno == logging.WARNING and record.exc_info is None


def test_health_returns_503_when_the_database_is_down(settings):
    dead = settings.model_copy(
        update={"database_url": SecretStr("postgresql://u:p@127.0.0.1:1/db")}
    )

    with TestClient(create_app(dead), raise_server_exceptions=False) as client:
        response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}


def test_unhandled_errors_are_500_without_a_traceback_or_secret(settings, caplog):
    secret = "topsecretpw"
    settings = settings.model_copy(
        update={"database_url": SecretStr(f"postgresql://u:{secret}@127.0.0.1:1/db")}
    )
    app = create_app(settings)

    @app.get("/api/boom")
    def boom() -> None:
        raise RuntimeError(f"connection failed with {secret}")

    with caplog.at_level(logging.ERROR):
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": "internal error"}
    assert secret not in caplog.text


def test_committed_openapi_schema_is_current(settings):
    committed = json.loads((Path(__file__).parents[2] / "openapi.json").read_text())
    generated = create_app(settings).openapi()

    assert committed == generated
    assert ALL_PATHS <= set(committed["paths"])


def test_spa_is_served_with_a_history_fallback(api_settings, tmp_path, caplog):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=spa-root></div>")
    (dist / "assets" / "app.js").write_text("console.log('spa')")

    with TestClient(create_app(api_settings, frontend_dist=dist)) as client:
        page = client.get("/runs/some-uid")
        asset = client.get("/assets/app.js")
        unknown_api = client.get("/api/nope")
        docs = client.get("/docs")
        openapi = client.get("/openapi.json")
        head = client.head("/runs/some-uid")
        null_path = client.get("/%00")
        null_run = client.get("/runs/%00")

    assert page.status_code == 200
    assert "spa-root" in page.text
    assert page.headers["cache-control"] == "no-cache"
    assert asset.status_code == 200
    assert "console.log" in asset.text
    # Unknown API paths stay JSON: they must not fall back to index.html.
    assert unknown_api.status_code == 404
    assert unknown_api.json() == {"detail": "not found"}
    # The SPA catch-all must not swallow FastAPI's own pages.
    assert docs.status_code == 200
    assert openapi.status_code == 200
    assert head.status_code == 200
    # NUL in any path is a 404, not a 500 with a traceback in the log.
    assert null_path.status_code == 404
    assert null_run.status_code == 404
    assert [record for record in caplog.records if record.levelno >= logging.ERROR] == []


def test_frontend_dist_setting_is_used(api_settings, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><div id=configured-spa></div>")
    configured = api_settings.model_copy(update={"frontend_dist": dist})

    with TestClient(create_app(configured)) as client:
        response = client.get("/somewhere")

    assert response.status_code == 200
    assert "configured-spa" in response.text


def test_no_spa_routes_without_a_build(api_settings, tmp_path):
    app = create_app(api_settings, frontend_dist=tmp_path / "missing")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/runs/some-uid")

    assert response.status_code == 404
