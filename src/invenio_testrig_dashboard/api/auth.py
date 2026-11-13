"""GitHub OAuth login (no extra auth library) and the session helpers.

The session stores only the GitHub login and, during login, the OAuth ``state``. GitHub's
access token is used once to read the username and then discarded.
"""

import html
import logging
import re
import secrets
from typing import Any
from urllib.parse import unquote, urlencode, urlsplit

import httpx2
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import SecretStr

from ..runs import short_error
from ..settings import Settings
from .schemas import MeOut

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,39}$")
AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105 (a URL, not a secret)
USER_URL = "https://api.github.com/user"
TIMEOUT_SECONDS = 20.0
SESSION_MAX_AGE = 7 * 24 * 3600
# Our own name, not Starlette's default "session": cookies ignore the port, so on localhost a
# Flask/Invenio app's "session" cookie would overwrite ours mid-login (invalid OAuth state).
SESSION_COOKIE = "itd_session"


def oauth_enabled(settings: Settings) -> bool:
    return settings.github_oauth_client_id is not None


def request_settings(request: Request) -> Settings:
    return request.app.state.settings


def is_maintainer(login: str, settings: Settings) -> bool:
    # GitHub logins are case-insensitive.
    return login.lower() in {maintainer.lower() for maintainer in settings.maintainers}


def session_login(request: Request) -> str | None:
    """The logged-in GitHub login, or None (also when login is not configured)."""
    if not oauth_enabled(request_settings(request)):
        return None
    login = request.session.get("login")
    return login if isinstance(login, str) else None


def safe_next(value: str | None) -> str:
    """A same-site path to return to after login; anything else (open redirect) becomes "/".

    Validated after percent-decoding, so an encoded ``/%0d%0aLocation: …`` is rejected too;
    the raw value is returned (it is what the browser is asked to open).
    """
    if not value:
        return "/"
    decoded = unquote(value)
    if (
        not decoded.startswith("/")
        or decoded.startswith("//")
        or "\\" in decoded
        or not decoded.isprintable()
    ):
        return "/"
    return value


def _came_from_callback(request: Request) -> bool:
    """Whether the referer is the OAuth callback (the request right after a login)."""
    referer = request.headers.get("referer")
    return referer is not None and urlsplit(referer).path == "/api/auth/callback"


async def login_gate(request: Request, call_next):
    """Every page and API call needs a logged-in user; only health and auth are open.

    Fails closed: without OAuth settings ``session_login`` is None, so everything is refused.
    """
    path = request.url.path
    # rstrip: a probe or ingress written with a trailing slash must still be ready.
    if path.rstrip("/") == "/api/health" or path.startswith("/api/auth/") or session_login(request):
        return await call_next(request)
    if path == "/api" or path.startswith("/api/"):
        return JSONResponse({"detail": "log in first"}, status_code=401)
    if _came_from_callback(request):
        # The callback did log in and redirected here, yet this request has no session: the
        # browser dropped the cookie. That happens when public_url's scheme is https (Secure
        # cookie) but the site is reached over http — otherwise it looks like a login loop.
        log.warning(
            "session cookie not kept after the login callback — "
            "check that public_url's scheme matches the site"
        )
    back = f"{path}?{request.url.query}" if request.url.query else path
    return RedirectResponse(f"/api/auth/login?{urlencode({'next': back})}", status_code=302)


def _login_failed() -> HTMLResponse:
    # Not a redirect into the app: with login required that would loop back to GitHub.
    return HTMLResponse(
        '<p>GitHub login failed. <a href="/api/auth/login">Try again</a></p>', status_code=502
    )


def _invalid_state(request: Request, settings: Settings, expected: object) -> HTMLResponse:
    """Explain a failed state check: the same 400 hides very different causes.

    The usual one is a host mismatch: the login started on a host other than public_url's (e.g.
    127.0.0.1 vs localhost), so the session cookie holding the state lives on that host, and
    GitHub returns to public_url, where the browser has no cookie. Logged without any values.
    """
    if SESSION_COOKIE not in request.cookies:
        reason = "no session cookie (did the login start on another host than public_url?)"
    elif not isinstance(expected, str):
        reason = "the session holds no OAuth state (unreadable cookie, or a repeated callback)"
    else:
        reason = "the state differs (a newer login in another tab replaced it)"
    log.warning(
        "login failed: invalid OAuth state: %s; session cookies sent: %d; host %r, "
        "public_url host %r",
        reason,
        request.headers.get("cookie", "").count(f"{SESSION_COOKIE}="),
        request.url.hostname,
        urlsplit(settings.public_url).hostname,
    )
    login = html.escape(f"{settings.public_url}/api/auth/login", quote=True)
    return HTMLResponse(
        "<p>The GitHub login could not be completed (invalid OAuth state). Open the dashboard "
        f'at its configured address and <a href="{login}">log in again</a>.</p>',
        status_code=400,
    )


def _require_oauth(settings: Settings) -> None:
    if not oauth_enabled(settings):
        raise HTTPException(404, "login is not configured")


def oauth_credentials(settings: Settings) -> tuple[SecretStr, SecretStr]:
    """The OAuth app's id and secret, guaranteed non-None (``_require_oauth`` checked first)."""
    client_id = settings.github_oauth_client_id
    client_secret = settings.github_oauth_client_secret
    if client_id is None or client_secret is None:  # pragma: no cover - settings validator
        raise HTTPException(404, "login is not configured")
    return client_id, client_secret


@router.get("/login")
def login(request: Request, next: str | None = None) -> RedirectResponse:
    settings = request_settings(request)
    _require_oauth(settings)
    client_id, _ = oauth_credentials(settings)
    # Two tabs share this one state/next slot, so the older tab's callback answers 400
    # "invalid OAuth state". That is the accepted trade-off for a session-only state.
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    request.session["login_next"] = safe_next(next)
    query = urlencode(
        {
            "client_id": client_id.get_secret_value(),
            "redirect_uri": f"{settings.public_url}/api/auth/callback",
            "state": state,
        }
    )
    return RedirectResponse(f"{AUTHORIZE_URL}?{query}", status_code=302)


@router.get("/callback")
def callback(request: Request, code: str | None = None, state: str | None = None) -> Response:
    settings = request_settings(request)
    _require_oauth(settings)
    expected = request.session.get("oauth_state")
    if (
        not code
        or not state
        or not isinstance(expected, str)
        or not secrets.compare_digest(state, expected)
    ):
        return _invalid_state(request, settings, expected)

    try:
        login_name = _exchange_code(settings, request.app.state.http_transport, code)
    except Exception as exc:
        # Never log the code or token; short_error already removes URLs.
        log.warning("login failed: %s", short_error(exc))
        return _login_failed()

    if not LOGIN_PATTERN.fullmatch(login_name):
        log.warning("login failed: unusable GitHub login")
        return _login_failed()

    request.session.pop("oauth_state", None)
    back = safe_next(request.session.pop("login_next", None))
    request.session["login"] = login_name
    log.info("login ok for %s", login_name)
    return RedirectResponse(back, status_code=302)


@router.get("/me", response_model=MeOut)
def me(request: Request) -> MeOut:
    settings = request_settings(request)
    login_name = session_login(request)
    return MeOut(
        login=login_name,
        is_maintainer=login_name is not None and is_maintainer(login_name, settings),
        login_enabled=oauth_enabled(settings),
    )


@router.post("/logout", status_code=204)
def logout(request: Request) -> Response:
    _require_oauth(request_settings(request))
    request.session.clear()
    return Response(status_code=204)


def _exchange_code(settings: Settings, transport: Any, code: str) -> str:
    client_id, client_secret = oauth_credentials(settings)
    with httpx2.Client(timeout=TIMEOUT_SECONDS, transport=transport) as client:
        token_response = client.post(
            ACCESS_TOKEN_URL,
            data={
                "client_id": client_id.get_secret_value(),
                "client_secret": client_secret.get_secret_value(),
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        token_response.raise_for_status()
        token = token_response.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("GitHub returned no access token")

        user_response = client.get(
            USER_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        user_response.raise_for_status()
        login_value = user_response.json().get("login")
        if not isinstance(login_value, str):
            raise ValueError("GitHub returned no login")
        return login_value
