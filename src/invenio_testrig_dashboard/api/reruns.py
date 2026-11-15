"""Request a manual run (the re-run button) and start it right away."""

import logging
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from .. import dispatch
from .auth import is_maintainer, request_settings, session_login
from .pagination import Branch
from .schemas import RerunConflict, RerunOut

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["reruns"])

JSON_MEDIA_TYPE = "application/json"
# One dispatch tick holds the global advisory lock for its whole duration; retry while another
# tick has it, but give up after one dispatch interval (by then the scheduled dispatch picks the run up).
DISPATCH_RETRY_SECONDS = 10
DISPATCH_RETRY_FOR = 15 * 60


def _dispatch_now(app: Any, branch: str) -> None:
    """Dispatch the branch's requested run after the 201, like ``itd dispatch <branch>``.

    Never raises: an exception here could only reach the log anyway.
    ponytail: one Starlette thread-pool thread per re-run (40 by default), bounded by "one
    active run per target"; use a queue if a pod ever serves many targets at once.
    """
    try:
        deadline = time.monotonic() + DISPATCH_RETRY_FOR
        while True:
            summary = dispatch.dispatch_tick(
                app.state.engine,
                app.state.session_factory,
                app.state.settings,
                now=datetime.now(UTC),
                run_testrig=app.state.run_testrig,
                only_branch=branch,
            )
            if not summary.busy or time.monotonic() >= deadline:
                return
            time.sleep(DISPATCH_RETRY_SECONDS)
    except Exception:
        log.exception("immediate dispatch of %s failed; the next dispatch tick retries it", branch)


def _conflict(exc: dispatch.RunRequestRejected) -> HTTPException:
    active = exc.active
    if active is None:  # pragma: no cover - "active" always carries it
        return HTTPException(409, str(exc))
    detail = RerunConflict(
        message=str(exc),
        uid=active.uid,
        status=active.status,
        dispatched_at=active.dispatched_at,
        lost_at=active.lost_at,
    )
    return HTTPException(409, detail=detail.model_dump(mode="json"))


@router.post(
    "/targets/{branch}/runs",
    response_model=RerunOut,
    status_code=201,
    responses={409: {"model": RerunConflict, "description": "the target has an active run"}},
)
def request_rerun(branch: Branch, request: Request, background: BackgroundTasks) -> RerunOut:
    settings = request_settings(request)
    login_name = session_login(request)
    if login_name is None:
        raise HTTPException(401, "log in first")
    # Checked on every request, so removing someone from the config takes effect immediately.
    if not is_maintainer(login_name, settings):
        log.info("re-run refused for %s on %s: not a maintainer", login_name, branch)
        raise HTTPException(403, "not a maintainer")
    # SameSite=Lax plus a JSON-only endpoint is the CSRF protection.
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != JSON_MEDIA_TYPE:
        raise HTTPException(415, "Content-Type must be application/json")

    try:
        uid = dispatch.request_run(
            request.app.state.session_factory,
            settings,
            branch,
            login_name,
            now=datetime.now(UTC),
        )
    except dispatch.RunRequestRejected as exc:
        if exc.kind == "active":
            log.info("re-run refused for %s on %s: %s", login_name, branch, exc)
            raise _conflict(exc) from exc
        raise HTTPException(404, str(exc)) from exc

    if uid is None:  # pragma: no cover - only dry runs return None
        raise HTTPException(500, "run was not created")
    log.info("run %s requested by %s for %s", uid, login_name, branch)
    # After the response: a dispatch is a subprocess that can take minutes.
    background.add_task(_dispatch_now, request.app, branch)
    return RerunOut(uid=uid)
