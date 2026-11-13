import copy
import logging
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import pytest
from fastapi.testclient import TestClient

from invenio_testrig_dashboard.api import auth, create_app
from invenio_testrig_dashboard.settings import ConfigError

LOGIN = "OctoCat"


def github_handler(login: str = LOGIN, token_status: int = 200):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/login/oauth/access_token":
            if token_status != 200:
                return httpx2.Response(token_status, json={"error": "bad_verification_code"})
            return httpx2.Response(200, json={"access_token": "gho_test_secret"})
        if request.url.path == "/user":
            return httpx2.Response(200, json={"login": login})
        return httpx2.Response(404)

    return handler


def make_client(settings, handler=None) -> TestClient:
    transport = httpx2.MockTransport(handler) if handler is not None else None
    return TestClient(create_app(settings, http_transport=transport))


def start_login(client: TestClient) -> str:
    response = client.get("/api/auth/login", follow_redirects=False)
    assert response.status_code == 302
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def test_login_redirects_to_github_with_state(oauth_settings):
    with make_client(oauth_settings) as client:
        response = client.get("/api/auth/login", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://github.com/login/oauth/authorize?")
    query = parse_qs(urlsplit(location).query)
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["http://dash.example/api/auth/callback"]
    assert query["state"][0]


def test_callback_logs_in_and_me_reports_it(oauth_settings):
    with make_client(oauth_settings, github_handler()) as client:
        state = start_login(client)
        callback = client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
        me = client.get("/api/auth/me")

    assert callback.status_code == 302
    assert callback.headers["location"] == "/"
    assert me.json() == {
        "login": LOGIN,
        "is_maintainer": True,
        "login_enabled": True,
    }


def test_callback_with_wrong_state_is_400(oauth_settings, caplog):
    with make_client(oauth_settings, github_handler()) as client:
        start_login(client)
        response = client.get("/api/auth/callback?code=abc&state=wrong", follow_redirects=False)

    assert response.status_code == 400
    assert 'href="http://dash.example/api/auth/login"' in response.text
    assert "the state differs" in caplog.text


def test_a_login_started_on_another_host_explains_itself(oauth_settings, caplog):
    """127.0.0.1 vs localhost: the state cookie lives on the host the login started on."""
    with make_client(oauth_settings, github_handler()) as client:
        client.base_url = "http://127.0.0.1"
        state = start_login(client)
        # GitHub sends the browser back to public_url's host, which has no cookie.
        response = client.get(
            f"http://dash.example/api/auth/callback?code=abc&state={state}",
            follow_redirects=False,
        )

    assert response.status_code == 400
    assert "no session cookie" in caplog.text
    assert "host 'dash.example', public_url host 'dash.example'" in caplog.text
    # Never the state or the code.
    assert state not in caplog.text
    assert "code=abc" not in caplog.text


def test_a_foreign_session_cookie_on_the_same_host_does_not_break_the_login(oauth_settings):
    """Another localhost app (Flask/Invenio) uses a cookie named "session"; ours has its own name."""
    with make_client(oauth_settings, github_handler()) as client:
        state = start_login(client)
        # While the user is on GitHub, another app on the same host sets its "session" cookie
        # (same host and path as ours; cookies ignore the port).
        ours = next(cookie for cookie in client.cookies.jar if cookie.name == auth.SESSION_COOKIE)
        foreign = copy.copy(ours)
        foreign.name, foreign.value = "session", "a-flask-session-from-another-app"
        client.cookies.jar.set_cookie(foreign)
        callback = client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)

    assert callback.status_code == 302


def test_callback_with_github_error_shows_the_try_again_page(oauth_settings):
    with make_client(oauth_settings, github_handler(token_status=500)) as client:
        state = start_login(client)
        response = client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)

    assert response.status_code == 502
    assert 'href="/api/auth/login"' in response.text


def test_callback_rejects_an_impossible_login(oauth_settings):
    with make_client(oauth_settings, github_handler(login="not a login!")) as client:
        state = start_login(client)
        response = client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
        me = client.get("/api/auth/me")

    assert response.status_code == 502
    assert me.json()["login"] is None


def test_oauth_not_configured(api_settings):
    with make_client(api_settings) as client:
        login = client.get("/api/auth/login", follow_redirects=False)
        callback = client.get("/api/auth/callback?code=x&state=y", follow_redirects=False)
        logout = client.post("/api/auth/logout")
        me = client.get("/api/auth/me")

    assert login.status_code == 404
    assert callback.status_code == 404
    assert logout.status_code == 404
    assert me.json() == {"login": None, "is_maintainer": False, "login_enabled": False}


def test_logout_clears_the_session(oauth_settings):
    with make_client(oauth_settings, github_handler()) as client:
        state = start_login(client)
        client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
        logout = client.post("/api/auth/logout")
        me = client.get("/api/auth/me")

    assert logout.status_code == 204
    assert me.json()["login"] is None


def test_me_answers_json_with_the_spa_mounted(oauth_settings, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><div id=spa></div>")

    with TestClient(create_app(oauth_settings, frontend_dist=dist)) as client:
        me = client.get("/api/auth/me")
        spa = client.get("/somewhere", follow_redirects=False)

    assert me.status_code == 200
    assert me.headers["content-type"].startswith("application/json")
    assert spa.status_code == 302


def test_the_session_cookie_and_login_log(oauth_settings, caplog):
    with caplog.at_level(logging.INFO):
        with make_client(oauth_settings, github_handler()) as client:
            state = start_login(client)
            client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
            cookie = client.cookies.get("itd_session")

    assert cookie is not None
    # Only the outcome is logged; the code/token never is.
    assert "login ok for" in caplog.text
    assert "gho_test_secret" not in caplog.text


def log_in(client: TestClient, next_path: str | None = None):
    url = "/api/auth/login" + (f"?{urlencode({'next': next_path})}" if next_path else "")
    response = client.get(url, follow_redirects=False)
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    return client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)


def test_anonymous_requests_need_a_login(oauth_settings, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><div id=spa></div>")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("console.log(1)")

    with TestClient(create_app(oauth_settings, frontend_dist=dist)) as client:
        api = client.get("/api/targets")
        unknown_api = client.get("/api/nothing")
        docs = client.get("/openapi.json", follow_redirects=False)
        page = client.get("/test-cases/1?target=14.x", follow_redirects=False)
        asset = client.get("/assets/app.js", follow_redirects=False)
        health = client.get("/api/health")
        health_slash = client.get("/api/health/", follow_redirects=True)
        me = client.get("/api/auth/me")

    assert api.status_code == 401
    assert api.json() == {"detail": "log in first"}
    assert unknown_api.status_code == 401
    assert docs.status_code == 302
    assert asset.status_code == 302
    assert page.status_code == 302
    location = urlsplit(page.headers["location"])
    assert location.path == "/api/auth/login"
    assert parse_qs(location.query) == {"next": ["/test-cases/1?target=14.x"]}
    assert health.status_code == 200
    # A probe written with a trailing slash must still answer; the SPA catch-all would
    # otherwise shadow Starlette's slash redirect and turn it into a 404.
    assert health_slash.status_code == 200
    assert me.status_code == 200


def test_login_returns_to_the_requested_page_and_unlocks_the_api(oauth_settings):
    with make_client(oauth_settings, github_handler()) as client:
        callback = log_in(client, "/test-cases/1?target=14.x")
        api = client.get("/api/targets")

    assert callback.headers["location"] == "/test-cases/1?target=14.x"
    assert api.status_code == 200


def test_login_never_redirects_off_site(oauth_settings):
    evil_values = (
        "https://evil.example/",
        "//evil.example/",
        "/\\evil.example",
        "relative",
        # Percent-encoded header injection: rejected on the decoded value.
        "/%0d%0aLocation:%20https://evil.example",
        "%2F%2Fevil.example",
    )
    for evil in evil_values:
        with make_client(oauth_settings, github_handler()) as client:
            assert log_in(client, evil).headers["location"] == "/", evil


def test_the_gate_warns_when_the_cookie_did_not_stick(oauth_settings, caplog):
    with caplog.at_level(logging.WARNING):
        with make_client(oauth_settings) as client:
            # Anonymous request right after the callback: the session cookie was dropped.
            response = client.get(
                "/runs/1",
                headers={"referer": "http://dash.example/api/auth/callback?code=x&state=y"},
                follow_redirects=False,
            )

    assert response.status_code == 302
    assert "public_url's scheme" in caplog.text


def test_require_login_without_oauth_refuses_to_start(api_settings):
    app = create_app(api_settings.model_copy(update={"require_login": True}))

    with pytest.raises(ConfigError, match="require_login"):
        with TestClient(app):
            pass
