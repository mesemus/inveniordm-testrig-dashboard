from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from invenio_testrig_dashboard.api import create_app
from invenio_testrig_dashboard.models import PackageResult, Run, TestCase, TestResult, case_key
from invenio_testrig_dashboard.settings import Settings
from invenio_testrig_dashboard.status import PackageStatus


@pytest.fixture
def api_settings(settings: Settings, db_url: str) -> Settings:
    # The app must talk to the session's throw-away database, not the developer's DB_URL.
    return settings.model_copy(update={"database_url": SecretStr(db_url)})


@pytest.fixture
def api_app(api_settings: Settings) -> FastAPI:
    return create_app(api_settings)


@pytest.fixture
def oauth_settings(api_settings: Settings) -> Settings:
    return api_settings.model_copy(
        update={
            "github_oauth_client_id": SecretStr("client-id"),
            "github_oauth_client_secret": SecretStr("client-secret"),
            "session_secret": SecretStr("x" * 32),
            "maintainers": ["octocat"],
            "public_url": "http://dash.example",
            "require_login": True,
        }
    )


@pytest.fixture
def client(api_app: FastAPI) -> Iterator[TestClient]:
    # The `with` form runs the lifespan, which puts engine/session factory/S3 clients on
    # app.state; a plain TestClient(app) skips it and every request fails with AttributeError.
    with TestClient(api_app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def add_packages(session, session_factory: sessionmaker[Session]):
    """Attach package_result rows (needed by matrix/compare and the run detail)."""

    def add(
        run: Run,
        packages: dict[str, tuple[PackageStatus, bool]],
        side: str = "original",
    ) -> None:
        with session_factory() as db, db.begin():
            for name, (status, has_junit) in packages.items():
                db.add(
                    PackageResult(
                        run_id=run.id,
                        package=name,
                        artifact_dir=f"artifacts/{name}",
                        side=side,
                        status=status,
                        has_junit=has_junit,
                    )
                )

    return add


@pytest.fixture
def set_message(session, session_factory: sessionmaker[Session]):
    """Set the failure message of a test result created by make_run(results=...)."""

    def set_message(run: Run, key: str, message: str) -> None:
        package, _, name = key.partition("::")
        with session_factory() as db, db.begin():
            case = db.scalar(
                select(TestCase).where(TestCase.key_hash == case_key(package, "", name))
            )
            row = db.get(TestResult, (run.id, case.id))
            row.message = message

    return set_message
