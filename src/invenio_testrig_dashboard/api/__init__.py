"""The FastAPI app (login required): one factory, one lifespan, routers under /api."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError
from starlette.middleware.sessions import SessionMiddleware

from .. import __version__
from ..db import make_engine, make_session_factory
from ..redact import install_logging_filter
from ..runs import short_error
from ..settings import ConfigError, Settings
from ..storage import make_presign_client, make_s3_client
from . import artifacts, auth, compare, health, reruns, runs, targets, test_cases

log = logging.getLogger(__name__)


async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
    # The logging filter redacts exception texts; clients never see a traceback.
    log.error("unhandled API error for %s", request.url.path, exc_info=exc)
    return JSONResponse({"detail": "internal error"}, status_code=500)


async def _database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    # A database outage is expected and temporary: a 503 (like /api/health) and one log line,
    # not a 500 with a full traceback for every request until the database is back.
    log.warning("database unavailable for %s: %s", request.url.path, short_error(exc))
    return JSONResponse({"detail": "database unavailable"}, status_code=503)


def create_app(
    settings: Settings,
    frontend_dist: Path | None = None,
    http_transport: Any = None,
    run_testrig: Any = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # uvicorn has configured its loggers by now; only re-installing covers them.
        install_logging_filter(settings)
        if settings.require_login and not auth.oauth_enabled(settings):
            # The gate would refuse everything anyway; say why at startup instead.
            raise ConfigError(
                "require_login needs the GitHub OAuth settings; "
                "set require_login: false only for local development"
            )
        if not settings.require_login:
            # A security downgrade must leave a trace in the pod log.
            log.warning("require_login is disabled: every page and API call is public")
        engine = make_engine(settings)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        app.state.s3 = make_s3_client(settings)
        app.state.s3_presign = make_presign_client(settings)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Invenio Testrig Dashboard API",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = settings
    # Injected in tests so GitHub's OAuth calls can be mocked with httpx2.MockTransport.
    app.state.http_transport = http_transport
    # Injected in tests so "re-run now" dispatches with a fake instead of running testrig.
    app.state.run_testrig = run_testrig
    if settings.require_login:
        # Added before SessionMiddleware, so it runs inside it and can read the session.
        app.middleware("http")(auth.login_gate)
    if settings.session_secret is not None:
        app.add_middleware(
            SessionMiddleware,
            session_cookie=auth.SESSION_COOKIE,
            secret_key=settings.session_secret.get_secret_value(),
            same_site="lax",
            https_only=settings.public_url.startswith("https://"),
            max_age=auth.SESSION_MAX_AGE,
        )
    # These routers must come before the SPA catch-all added by _add_spa.
    for module in (health, targets, runs, test_cases, compare, artifacts, auth, reruns):
        app.include_router(module.router)
    app.add_exception_handler(OperationalError, _database_unavailable)
    app.add_exception_handler(Exception, _internal_error)

    dist = (
        Path(frontend_dist)
        if frontend_dist is not None
        else settings.frontend_dist or Path("frontend/dist")
    )
    if (dist / "index.html").is_file():
        _add_spa(app, dist)
    return app


def _add_spa(app: FastAPI, dist: Path) -> None:
    """Serve the built SPA, with the history fallback; never part of the OpenAPI schema."""
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    root = dist.resolve()

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        # NUL would make Path.resolve() raise (a 500 for every scanner that tries it).
        if "\x00" in full_path or full_path == "api" or full_path.startswith("api/"):
            # A JSON 404 for unknown API paths, never index.html.
            raise HTTPException(404, "not found")
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        # Assets are content-hashed and cache well; the shell must not point at old hashes.
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})


def load_app() -> FastAPI:
    """Entry point for ``uvicorn --reload``; the only place the app builds its own settings."""
    return create_app(Settings())
