import functools
import json
import logging
import signal
import traceback
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

import typer
from croniter import croniter
from pydantic import BaseModel, SecretStr, ValidationError
from rich.console import Console
from rich.table import Table
from rich.text import Text
from sqlalchemy import select

from . import __version__, alerts
from . import backup as backup_module
from . import dispatch as dispatch_module
from . import github as github_module
from .db import DatabaseUnavailable, make_engine, make_session_factory, migrate, wait_for_database
from .ingest import store_run
from .ingest_github import ingest_tick
from .models import Run, Target
from .parsing import InvalidResults, parse_run_dir
from .redact import install_logging_filter
from .runs import short_error
from .settings import ConfigError, Settings
from .status import FAILING, RunStatus, RunTrigger
from .storage import make_s3_client
from .targets import sync_targets

log = logging.getLogger(__name__)

STUB_COMMANDS: tuple[str, ...] = ()

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    # Never print local variables (settings, tokens) in tracebacks, whatever Typer's default.
    pretty_exceptions_show_locals=False,
    help="Invenio Testrig Dashboard management CLI.",
)
err_console = Console(stderr=True)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_print_version, is_eager=True, help="Show the version and exit."
        ),
    ] = False,
) -> None:
    """Invenio Testrig Dashboard management CLI."""


def _format_item(item: Any) -> str:
    if isinstance(item, BaseModel):
        return ", ".join(f"{key}={value}" for key, value in item.model_dump().items())
    return str(item)


def _format_value(value: Any) -> Text:
    if isinstance(value, SecretStr):
        return Text(str(value))  # **********
    if value is None:
        return Text("not set", style="dim")
    if isinstance(value, list):
        if not value:
            return Text("none", style="dim")
        return Text("\n".join(_format_item(item) for item in value))
    # Text, not markup, so config values containing [brackets] print literally.
    return Text(str(value))


def print_settings(settings: Settings, now: datetime | None = None) -> None:
    table = Table(title="Invenio Testrig Dashboard configuration")
    table.add_column("Setting", style="cyan", no_wrap=True)
    table.add_column("Value")
    for name in type(settings).model_fields:
        if name == "targets":
            # One line per target is unreadable inside a cell; they get their own table below.
            count = len(settings.targets)
            table.add_row(name, Text(f"{count} (see Targets below)" if count else "none"))
            continue
        table.add_row(name, _format_value(getattr(settings, name)))
    console = Console()
    console.print(table)
    if settings.targets:
        console.print(_targets_table(settings, now or datetime.now(UTC)))


def _targets_table(settings: Settings, now: datetime) -> Table:
    table = Table(title="Targets (branches of the seed repository)")
    table.add_column("Branch", style="cyan", no_wrap=True)
    table.add_column("Schedule (UTC)", no_wrap=True)
    table.add_column("Enabled", no_wrap=True)
    table.add_column("Next fire (UTC)", no_wrap=True)
    for target in settings.targets:
        next_fire = (
            croniter(target.schedule, now).get_next(datetime).strftime("%Y-%m-%d %H:%M")
            if target.enabled
            else "-"
        )
        table.add_row(
            Text(target.branch),  # Text, not markup: branch names are printed literally
            Text(target.schedule),
            Text("yes" if target.enabled else "no", style="" if target.enabled else "dim"),
            Text(next_fire),
            style="" if target.enabled else "dim",
        )
    return table


def _load_settings(ctx: typer.Context) -> Settings:
    """Load settings once per invocation, on first use (so --help never needs a config)."""
    if not isinstance(ctx.obj, Settings):
        try:
            ctx.obj = Settings()
        except (ConfigError, ValidationError) as exc:
            err_console.print("[bold red]Invalid configuration[/bold red]")
            err_console.print(str(exc), markup=False)
            raise typer.Exit(1) from exc
        # Installed as soon as the secrets are known; every later log record is redacted.
        install_logging_filter(ctx.obj)
    return ctx.obj


def _guarded(name: str):
    """Crash reporter for commands that run unattended.

    Normal exits (typer.Exit/SystemExit) pass through; any other exception is logged with
    its traceback, posted to the ops webhook (deduplicated), and turned into exit code 1,
    so the Kubernetes Job is marked failed too.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(ctx: typer.Context, *args: Any, **kwargs: Any):
            try:
                return func(ctx, *args, **kwargs)
            except typer.Exit, SystemExit:
                raise
            except Exception as exc:
                log.exception("%s crashed", name)
                try:
                    _ops_crash(ctx, name, exc)
                except Exception:
                    # Reporting must never hide the original error.
                    log.exception("could not report the %s crash", name)
                raise typer.Exit(1) from exc

        return wrapper

    return decorator


def _ops_crash(ctx: typer.Context, name: str, exc: BaseException) -> None:
    try:
        settings = _load_settings(ctx)
    except typer.Exit:
        return
    engine = make_engine(settings)
    try:
        tail = "".join(traceback.format_exception(exc)[-4:])[-1200:]
        alerts.crash_alert(
            make_session_factory(engine), settings, name, f"{short_error(exc)}\n{tail}"
        )
    finally:
        engine.dispose()


@app.command()
def check_config(ctx: typer.Context) -> None:
    """Validate the configuration and print it with secrets masked."""
    settings = _load_settings(ctx)
    print_settings(settings)
    if settings.discord_webhook_url is None:
        # Without a webhook, alerts are only logged, yet count as delivered (fine for development).
        err_console.print(
            "warning: discord_webhook_url is not set; alerts are only logged", markup=False
        )


@app.command(name="parse-dir")
def parse_dir(
    path: Annotated[Path, typer.Argument(help="testrig report directory")],
    show_tests: Annotated[
        bool, typer.Option("--tests", help="Print every parsed test case.")
    ] = False,
) -> None:
    """Parse a testrig report directory and print a summary (debugging aid)."""
    try:
        run = parse_run_dir(path)
    except InvalidResults as exc:
        err_console.print("[bold red]Invalid results[/bold red]")
        err_console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc

    counts = Counter(package.status.value for package in run.packages)
    typer.echo(
        f"Packages: {len(run.packages)} ({', '.join(f'{k}: {v}' for k, v in counts.items())})"
    )

    table = Table(title=f"Parsed run (format v{run.format_version})")
    table.add_column("Package", style="cyan", no_wrap=True)
    table.add_column("Status")
    table.add_column("Cases", justify="right")
    table.add_column("Duration (s)", justify="right")
    for package in run.packages:
        fail = sum(1 for case in package.cases if case.status in FAILING)
        cases = f"{len(package.cases)}" + (f" ({fail} failing)" if fail else "")
        table.add_row(
            Text(package.name),
            Text(package.status.value),
            Text(cases),
            Text("-" if package.duration_s is None else f"{package.duration_s:.1f}"),
        )
    Console().print(table)

    if show_tests:
        for package in run.packages:
            for case in package.cases:
                # Plain output: test names contain [brackets], which Rich would swallow.
                typer.echo(f"{package.name}\t{case.status.name}\t{case.classname}::{case.name}")
                if case.message is not None:
                    typer.echo(case.message)


def _report_started_at(path: Path) -> datetime | None:
    try:
        return parse_run_dir(path).started_at
    except InvalidResults:
        return None


@app.command(name="ingest-dir")
def ingest_dir(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="testrig report directory")],
    target: Annotated[str, typer.Option("--target", help="seed repo branch")],
    uid: Annotated[str | None, typer.Option("--uid", help="run uid to create or re-ingest")] = None,
    alert: Annotated[
        bool,
        typer.Option(
            "--alert/--no-alert",
            help="Post streak/ops alerts like a real ingest. Off by default: ingest-dir seeds data.",
        ),
    ] = False,
) -> None:
    """Store a report directory in Postgres and S3."""
    settings = _load_settings(ctx)
    try:
        requested_uid = UUID(uid) if uid else uuid4()
    except ValueError as exc:
        err_console.print(f"invalid --uid: {uid}", markup=False)
        raise typer.Exit(2) from exc

    engine = make_engine(settings)
    session_factory = make_session_factory(engine)
    try:
        with session_factory() as session:
            target_row = session.scalar(select(Target).where(Target.branch == target))
            run_row = session.scalar(select(Run).where(Run.uid == requested_uid))

        if target_row is None:
            err_console.print(
                f"target {target!r} not found; run migrate-and-sync first", markup=False
            )
            raise typer.Exit(1)
        if run_row is not None and run_row.target_id != target_row.id:
            err_console.print(
                f"run {run_row.uid} belongs to another target, not {target!r}", markup=False
            )
            raise typer.Exit(1)

        if run_row is None:
            # Old fixture runs should appear on their real date.
            created_at = _report_started_at(path) or datetime.now(UTC)
            with session_factory() as session, session.begin():
                run_row = Run(
                    uid=requested_uid,
                    target_id=target_row.id,
                    status=RunStatus.QUEUED,
                    trigger=RunTrigger.MANUAL,
                    triggered_by="ingest-dir",
                    testrig_ref=dispatch_module.testrig_ref(),
                    created_at=created_at,
                )
                session.add(run_row)
                session.flush()

        s3 = make_s3_client(settings)
        final_status = store_run(session_factory, s3, settings, run_row, path, alert=alert)
    finally:
        engine.dispose()

    typer.echo(f"Run {run_row.uid}: {final_status.value}")
    if final_status is not RunStatus.COMPLETED:
        raise typer.Exit(1)


@app.command()
@_guarded("dispatch")
def dispatch(
    ctx: typer.Context,
    target: Annotated[
        str | None,
        typer.Argument(
            help="Dispatch this target now, outside its schedule: a manual run, also when "
            "today's scheduled attempts are used up. Other targets are left alone."
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would happen; changes nothing.")
    ] = False,
) -> None:
    """Create runs for due targets and start them through testrig.

    Schedules are interpreted in UTC.
    Exits 0 when the tick worked, even if individual runs became infra_error.
    With a TARGET, exits 1 if the run can't be requested or its dispatch fails.
    A new target is due immediately (it has no earlier scheduled run).
    A dispatch that fails (before any GitHub run exists) is retried on the next ticks,
    at most 3 attempts per cron fire.
    """
    settings = _load_settings(ctx)
    engine = make_engine(settings)
    session_factory = make_session_factory(engine)
    now = datetime.now(UTC)
    try:
        requested_uid = None
        if target is not None:
            try:
                requested_uid = dispatch_module.request_run(
                    session_factory, settings, target, "cli", now, dry_run=dry_run
                )
            except dispatch_module.RunRequestRejected as exc:
                err_console.print(str(exc), markup=False)
                raise typer.Exit(1) from exc
        summary = dispatch_module.dispatch_tick(
            engine,
            session_factory,
            settings,
            now=now,
            dry_run=dry_run,
            only_branch=target,
        )
    finally:
        engine.dispose()

    if requested_uid is not None:
        typer.echo(f"requested: {requested_uid} ({target})")
    if summary.busy:
        err_console.print("another dispatch is running; nothing was done")
        if requested_uid is not None:
            err_console.print("the requested run waits for the next dispatch tick", markup=False)
        return

    if dry_run:
        typer.echo("Dry run: nothing was changed")
        if target is not None:
            typer.echo(f"would request a manual run of {target} and dispatch it")
        if summary.due_branches:
            typer.echo(f"due: {', '.join(summary.due_branches)}")
        if summary.dispatched_uids:
            typer.echo(f"would dispatch: {', '.join(summary.dispatched_uids)}")
    else:
        if summary.created_branches:
            typer.echo(f"created: {', '.join(summary.created_branches)}")
        if summary.dispatched_uids:
            typer.echo(f"dispatched: {', '.join(summary.dispatched_uids)}")
        if summary.failed_uids:
            typer.echo(f"failed: {', '.join(summary.failed_uids)}")

    typer.echo(
        f"Runs: {len(summary.created_branches)} created, "
        f"{len(summary.dispatched_uids)} dispatched, {len(summary.failed_uids)} failed"
    )
    if requested_uid is not None and str(requested_uid) in summary.failed_uids:
        raise typer.Exit(1)


@app.command()
@_guarded("ingest")
def ingest(ctx: typer.Context) -> None:
    """Poll GitHub for active runs, download finished results, and store them.

    Exits 0 when the tick worked, even if individual runs became infra_error/lost.
    """
    settings = _load_settings(ctx)
    engine = make_engine(settings)
    session_factory = make_session_factory(engine)
    s3 = make_s3_client(settings)
    try:
        with github_module.make_github_client(settings) as client:
            summary = ingest_tick(
                engine, session_factory, s3, settings, client, now=datetime.now(UTC)
            )
    except github_module.GitHubAuthError as exc:
        # The tick already posted the github-auth ops alert.
        err_console.print("GitHub authentication failed; see the ops alert", markup=False)
        raise typer.Exit(1) from exc
    finally:
        engine.dispose()

    if summary.busy:
        err_console.print("another ingest is running; nothing was done")
        return
    typer.echo(
        f"Active: {summary.active}; matched: {summary.matched}; "
        f"still active: {summary.left}; completed: {summary.completed}; "
        f"infra_error: {summary.infra_error}; lost: {summary.lost}"
    )


@app.command()
@_guarded("migrate-and-sync")
def migrate_and_sync(
    ctx: typer.Context,
    wait_for_db: Annotated[
        float,
        typer.Option(
            "--wait-for-db",
            help="Seconds to keep retrying the database connection before giving up "
            "(the k8s initContainer can start before Postgres is ready).",
        ),
    ] = 120,
) -> None:
    """Apply pending migrations, then sync configured targets into the database."""
    settings = _load_settings(ctx)
    engine = make_engine(settings)
    try:
        try:
            wait_for_database(engine, wait_for_db)
        except DatabaseUnavailable as exc:
            err_console.print(str(exc), markup=False)
            raise typer.Exit(1) from exc
        before, after = migrate(engine)
        with make_session_factory(engine)() as session, session.begin():
            changes = sync_targets(session, settings.targets)
    finally:
        engine.dispose()

    typer.echo(f"Migrations: {before or 'empty'} -> {after or 'empty'}")
    typer.echo(
        f"Targets: {len(changes.added)} added, {len(changes.updated)} updated, "
        f"{len(changes.disabled)} disabled"
    )
    for label, branches in (
        ("added", changes.added),
        ("updated", changes.updated),
        ("disabled", changes.disabled),
    ):
        if branches:
            typer.echo(f"  {label}: {', '.join(branches)}")


@app.command()
@_guarded("backup")
def backup(ctx: typer.Context) -> None:
    """Dump the database to s3_backup_bucket, then delete dumps older than the retention.

    Exits 1 (and posts to ops Discord) when no verified dump was stored. A failed cleanup of old
    dumps only warns: the new dump is safe and the next run retries.
    """
    settings = _load_settings(ctx)
    if settings.s3_backup_bucket is None:
        err_console.print("s3_backup_bucket is not set (config.yaml or ITD_S3_BACKUP_BUCKET)")
        raise typer.Exit(1)
    result = backup_module.run_backup(settings, make_s3_client(settings))
    typer.echo(
        f"Backup: s3://{settings.s3_backup_bucket}/{result.key} "
        f"({result.size / 1_000_000:.1f} MB, {result.seconds:.1f} s)"
    )
    typer.echo(
        f"Pruned: {len(result.pruned)} dump(s) older than {settings.backup_retention_days} days"
    )
    if result.prune_error is not None:
        err_console.print(f"warning: pruning failed: {result.prune_error}", markup=False)


@app.command()
def web(
    ctx: typer.Context,
    host: Annotated[str, typer.Option(help="Bind address.")] = "0.0.0.0",  # noqa: S104
    port: Annotated[int, typer.Option(help="Port.")] = 8000,
    reload: Annotated[
        bool, typer.Option("--reload", help="Restart the server when the code changes.")
    ] = False,
) -> None:
    """Serve the read API with uvicorn."""
    settings = _load_settings(ctx)
    import uvicorn

    if reload:
        # --reload needs an import string, so this entry point builds its own settings.
        uvicorn.run(
            "invenio_testrig_dashboard.api:load_app",
            factory=True,
            host=host,
            port=port,
            reload=True,
        )
    else:
        from .api import create_app

        uvicorn.run(create_app(settings), host=host, port=port)


@app.command(name="export-openapi")
def export_openapi(
    ctx: typer.Context,
    output: Annotated[
        Path, typer.Option("--output", help="Where to write the OpenAPI schema.")
    ] = Path("openapi.json"),
) -> None:
    """Write the FastAPI OpenAPI schema as pretty JSON (used by the frontend)."""
    from .api import create_app

    schema = create_app(_load_settings(ctx)).openapi()
    output.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    typer.echo(f"Wrote {output}")


def _not_implemented(name: str):
    def command(ctx: typer.Context) -> None:
        """Not implemented yet; arrives in a later step."""
        _load_settings(ctx)
        err_console.print(f"[yellow]{name}[/yellow] is not implemented yet")
        raise typer.Exit(1)

    command.__name__ = name.replace("-", "_")
    return command


for _name in STUB_COMMANDS:
    app.command(_name)(_not_implemented(_name))


def _exit_on_sigterm(signum: int, frame: Any) -> None:
    # Kubernetes stops pods with SIGTERM. Python's default would end the process without
    # running cleanup; SystemExit runs `finally`/`with` blocks, so dispatch kills testrig's
    # process group and removes its temporary directory, and claimed runs stay consistent.
    raise SystemExit(128 + signum)


def main(args: list[str] | None = None) -> None:
    # Rendered logs are redacted by the filter installed when the settings load.
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    app(args=args)
