import logging
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import alembic.command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, Engine, Table, create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .settings import Settings

log = logging.getLogger(__name__)

MIGRATION_LOCK_KEY = 20261113
ALEMBIC_SCRIPT_LOCATION = "invenio_testrig_dashboard:alembic"


def make_engine(settings: Settings) -> Engine:
    # The config keeps the plain postgresql:// form (pg_dump needs it).
    url = make_url(settings.database_url.get_secret_value()).set(drivername="postgresql+psycopg")
    return create_engine(url, pool_pre_ping=True)


class DatabaseUnavailable(RuntimeError):
    """The database did not accept connections within the wait time."""


def wait_for_database(engine: Engine, timeout: float, interval: float = 2.0) -> None:
    """Retry connecting until the database answers or ``timeout`` seconds have passed.

    In k8s, ``migrate-and-sync`` runs as the web pod's initContainer, which on a first install
    starts before the Postgres StatefulSet accepts connections (design: no Helm hook).
    Every ``OperationalError`` is retried, including a wrong password (psycopg reports both
    the same way); the last error is reported when the time is up.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            with engine.connect():
                return
        except OperationalError as exc:
            # libpq's first line ("connection refused", "password authentication failed …")
            # names the host, never the password; the logging filter redacts the rest anyway.
            reason = (str(exc.orig).strip().splitlines() or [type(exc.orig).__name__])[0]
            if time.monotonic() + interval > deadline:
                raise DatabaseUnavailable(
                    f"database not reachable after {timeout:g} s: {reason}"
                ) from exc
            log.info("waiting for the database: %s", reason)
            time.sleep(interval)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def alembic_config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", ALEMBIC_SCRIPT_LOCATION)
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def run_migrations(connection: Connection) -> tuple[str | None, str | None]:
    before = MigrationContext.configure(connection).get_current_revision()
    alembic.command.upgrade(alembic_config(connection), "head")
    after = MigrationContext.configure(connection).get_current_revision()
    return before, after


def migrate(engine: Engine) -> tuple[str | None, str | None]:
    """Apply pending migrations under a Postgres advisory lock (safe with several pods).

    The lock is transaction-scoped: it is released on commit *and* on rollback, so a failing
    migration surfaces its own error instead of a failed unlock in an aborted transaction.
    """
    with engine.begin() as connection:
        connection.execute(select(func.pg_advisory_xact_lock(MIGRATION_LOCK_KEY)))
        return run_migrations(connection)


@contextmanager
def try_advisory_lock(engine: Engine, key: int) -> Iterator[bool]:
    """Try a session-level advisory lock for the duration of the block.

    Yields ``False`` when another process holds it. The unlock runs on an AUTOCOMMIT
    connection and its failure is only logged, so it can never hide the block's error.
    """
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        if not connection.scalar(select(func.pg_try_advisory_lock(key))):
            yield False
            return
        try:
            yield True
        finally:
            try:
                connection.execute(select(func.pg_advisory_unlock(key)))
            except Exception:
                log.exception("could not release advisory lock %s", key)


def copy_rows(
    session: Session,
    table: Table,
    rows: Iterable[Sequence[Any]],
    columns: Sequence[str] | None = None,
) -> None:
    """Bulk-load rows into a table with Postgres COPY.

    This is the only place that touches the raw psycopg connection. COPY is much faster
    than INSERT for the tens of thousands of test results a run produces.
    `table` is a model's ``__table__`` or a temporary ``Table``; each row holds values
    for `columns` (default: all table columns) in order. Identifiers come only from the Table
    object, so they are never user input; values go through COPY, never into the SQL text.
    """
    names = list(columns) if columns is not None else [column.name for column in table.c]
    selected = [table.c[name] for name in names]  # KeyError on a typo, before any SQL is sent
    preparer = session.get_bind().dialect.identifier_preparer
    quoted_table = preparer.format_table(table)
    quoted_columns = ", ".join(preparer.format_column(column) for column in selected)
    raw = session.connection().connection.driver_connection
    if raw is None:
        raise RuntimeError("session is not bound to a psycopg connection")

    with raw.cursor() as cursor:
        with cursor.copy(f"COPY {quoted_table} ({quoted_columns}) FROM STDIN") as copy:  # noqa: S608
            for row in rows:
                copy.write_row(row)
