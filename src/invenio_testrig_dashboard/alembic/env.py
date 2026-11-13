from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from invenio_testrig_dashboard.models import Base
from invenio_testrig_dashboard.settings import Settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    url = make_url(Settings().database_url.get_secret_value())
    return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Programmatic callers (db.run_migrations) pass a connection in.
    connection = config.attributes.get("connection")
    if connection is not None:
        do_run_migrations(connection)
        return

    # Developer CLI: build an engine from the normal config.
    engine = create_engine(database_url())
    with engine.connect() as connection:
        do_run_migrations(connection)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
