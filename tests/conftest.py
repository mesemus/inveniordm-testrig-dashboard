import os
import secrets
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from invenio_testrig_dashboard.db import make_session_factory, run_migrations
from invenio_testrig_dashboard.ingest import store_run
from invenio_testrig_dashboard.models import Base, Run, Target, TestCase, TestResult, case_key
from invenio_testrig_dashboard.settings import Settings
from invenio_testrig_dashboard.status import RunStatus, RunTrigger
from invenio_testrig_dashboard.storage import make_s3_client

FIXTURES = Path(__file__).parent / "fixtures" / "runs"

# The real value comes from the installed testrig (dispatch.testrig_ref()); fixture rows
# only need a value for a NOT NULL column.
TESTRIG_REF = "v1.0.0-test"

# Matches docker-compose.yml and the CI service containers.
DB_URL = (
    "postgresql://invenio_testrig_dashboard:invenio_testrig_dashboard"
    "@localhost:25432/invenio_testrig_dashboard"
)


@pytest.fixture(autouse=True)
def _isolate_environment(tmp_path, monkeypatch):
    # Exported ITD_* variables (e.g. from a sourced .env) must never reach tests,
    # and the repo's .env/config.yaml must never be read.
    for key in list(os.environ):
        if key.startswith("ITD_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    # The ops alerts' local dedupe file must not be shared between tests (or with the machine).
    from invenio_testrig_dashboard import alerts

    monkeypatch.setattr(alerts, "LOCAL_DEDUPE_FILE", tmp_path / "ops-alerts.json")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        seed_repository="oarepo/invenio-rdm-seed",
        runner_repository="oarepo/invenio-testrig-dashboard-runner",
        s3_bucket="invenio-testrig-dashboard-test",
        s3_backup_bucket="invenio-testrig-dashboard-db-backup-test",
        s3_endpoint_url="http://localhost:29000",
        database_url=DB_URL,
        github_token="test-token",
        s3_access_key="devaccess",
        s3_secret_key="devsecret",
        # Most tests read data anonymously; the login gate has its own tests (test_auth.py).
        require_login=False,
    )


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    """A fresh, migrated database for this test session."""
    name = f"itd_test_{secrets.token_hex(4)}"
    admin_url = make_url(DB_URL).set(database="postgres", drivername="postgresql+psycopg")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))  # noqa: S608

    url = make_url(DB_URL).set(database=name)
    migration_engine = create_engine(url.set(drivername="postgresql+psycopg"))
    try:
        with migration_engine.begin() as connection:
            run_migrations(connection)
        yield url.render_as_string(hide_password=False)
    finally:
        migration_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))  # noqa: S608
        admin_engine.dispose()


@pytest.fixture(scope="session")
def engine(db_url: str) -> Iterator[Engine]:
    eng = create_engine(make_url(db_url).set(drivername="postgresql+psycopg"), pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    _truncate_all(session_factory)
    with session_factory() as session:
        try:
            yield session
        finally:
            session.rollback()
    _truncate_all(session_factory)


def _truncate_all(session_factory: sessionmaker[Session]) -> None:
    # Table names come from our own metadata, never from input.
    with session_factory() as session, session.begin():
        for table in Base.metadata.sorted_tables:
            session.execute(text(f"TRUNCATE {table.name} RESTART IDENTITY CASCADE"))  # noqa: S608


def _add_results(
    session_factory: sessionmaker[Session], run_id: int, results: dict[str, int]
) -> None:
    """Insert test results from ``{"pkg::name": status}`` (scenario helper)."""
    with session_factory() as session, session.begin():
        for key, result_status in results.items():
            package, separator, name = key.partition("::")
            if not separator or not package or not name:
                raise ValueError(f"results keys look like 'package::test_name', not {key!r}")
            key_hash = case_key(package, "", name)
            session.execute(
                pg_insert(TestCase)
                .values(
                    key_hash=key_hash,
                    package=package,
                    classname="",
                    name=name,
                    first_seen_run_id=run_id,
                )
                .on_conflict_do_nothing(index_elements=[TestCase.key_hash])
            )
            case_id = session.scalar(select(TestCase.id).where(TestCase.key_hash == key_hash))
            session.execute(
                insert(TestResult).values(
                    run_id=run_id, test_case_id=case_id, status=int(result_status)
                )
            )


def _empty_bucket(client, bucket: str) -> None:
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        for start in range(0, len(objects), 1000):
            client.delete_objects(Bucket=bucket, Delete={"Objects": objects[start : start + 1000]})


@pytest.fixture
def s3(settings):
    client = make_s3_client(settings)
    yield client
    _empty_bucket(client, settings.s3_bucket)


@pytest.fixture
def make_target(session, session_factory: sessionmaker[Session]):
    # Depends on `session` so every test that creates targets starts and ends with empty tables.
    def create(
        branch: str = "master",
        schedule: str = "0 1 * * *",
        enabled: bool = True,
    ) -> Target:
        with session_factory() as session, session.begin():
            target = session.scalar(select(Target).where(Target.branch == branch))
            if target is None:
                target = Target(branch=branch, schedule=schedule, enabled=enabled)
                session.add(target)
                session.flush()
            else:
                target.schedule = schedule
                target.enabled = enabled
        return target

    return create


@pytest.fixture
def make_run(session, make_target, session_factory: sessionmaker[Session], settings: Settings):
    def create(
        target: str = "master",
        created_at: datetime | None = None,
        *,
        status: RunStatus = RunStatus.QUEUED,
        trigger: RunTrigger = RunTrigger.MANUAL,
        uid: uuid.UUID | None = None,
        gh_run_id: int | None = None,
        gh_run_url: str | None = None,
        dispatched_at: datetime | None = None,
        started_at: datetime | None = None,
        results: dict[str, int] | None = None,
    ) -> Run:
        target_row = make_target(target)
        with session_factory() as session, session.begin():
            run = Run(
                uid=uid or uuid.uuid4(),
                target_id=target_row.id,
                status=status,
                trigger=trigger,
                triggered_by="test",
                testrig_ref=TESTRIG_REF,
                created_at=created_at or datetime.now(UTC),
                gh_run_id=gh_run_id,
                gh_run_url=gh_run_url,
                dispatched_at=dispatched_at,
                started_at=started_at,
            )
            session.add(run)
            session.flush()

        if results:
            _add_results(session_factory, run.id, results)
        return run

    return create


@pytest.fixture
def ingest_fixture(make_run, session_factory, s3, settings):
    def ingest(name: str, target: str = "master", created_at: datetime | None = None) -> Run:
        run = make_run(target=target, created_at=created_at)
        store_run(session_factory, s3, settings, run, FIXTURES / name, alert=False)
        with session_factory() as session:
            return session.get(Run, run.id)

    return ingest
