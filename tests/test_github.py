from datetime import UTC, datetime

import httpx2
import pytest

import invenio_testrig_dashboard.github as github_module
from invenio_testrig_dashboard.github import (
    DownloadTooLarge,
    GitHub,
    GitHubAuthError,
    GitHubError,
    GitHubRateLimited,
)

REPO = "oarepo/invenio-testrig-dashboard-runner"


def make_client(handler) -> GitHub:
    return GitHub("token", REPO, transport=httpx2.MockTransport(handler))


def test_list_dispatch_runs_follows_link_pagination():
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.params.get("page") == "2":
            return httpx2.Response(
                200, json={"workflow_runs": [{"id": 2, "display_title": "second"}]}
            )
        return httpx2.Response(
            200,
            json={"workflow_runs": [{"id": 1, "display_title": "first"}]},
            headers={
                "Link": '<https://api.github.com/repos/oarepo/x/actions/runs?page=2>; rel="next"'
            },
        )

    with make_client(handler) as github:
        runs = github.list_dispatch_runs(datetime(2026, 11, 13, tzinfo=UTC))

    assert [run["id"] for run in runs] == [1, 2]


def test_pagination_fails_loudly_when_the_link_never_ends(monkeypatch):
    monkeypatch.setattr(github_module, "MAX_PAGES", 2)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={"workflow_runs": []},
            headers={"Link": '<https://api.github.com/repos/x/actions/runs?page=2>; rel="next"'},
        )

    with make_client(handler) as github:
        with pytest.raises(GitHubError, match="pagination"):
            github.list_dispatch_runs(datetime(2026, 11, 13, tzinfo=UTC))


def test_list_jobs_asks_for_the_latest_attempt_and_follows_pagination():
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if request.url.params.get("page") == "2":
            return httpx2.Response(200, json={"jobs": [{"id": 2}]})
        return httpx2.Response(
            200,
            json={"jobs": [{"id": 1}]},
            headers={
                "Link": '<https://api.github.com/repos/oarepo/x/actions/runs/1/jobs?page=2>; rel="next"'
            },
        )

    with make_client(handler) as github:
        jobs = github.list_jobs(1)

    assert [job["id"] for job in jobs] == [1, 2]
    assert seen[0].url.path == f"/repos/{REPO}/actions/runs/1/jobs"
    assert seen[0].url.params["filter"] == "latest"
    assert seen[0].url.params["per_page"] == "100"


def test_401_raises_auth_error():
    with make_client(lambda request: httpx2.Response(401)) as github:
        with pytest.raises(GitHubAuthError):
            github.get_run(1)


def test_exhausted_rate_limit_raises():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(403, headers={"x-ratelimit-remaining": "0"})

    with make_client(handler) as github:
        with pytest.raises(GitHubRateLimited):
            github.get_run(1)


def test_expiry_header_is_parsed_as_utc():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={"id": 1},
            headers={"github-authentication-token-expiration": "2026-12-01 10:00:00 UTC"},
        )

    with make_client(handler) as github:
        github.get_run(1)

    assert github.token_expires_at == datetime(2026, 12, 1, 10, 0, tzinfo=UTC)


def test_expiry_header_with_offset_is_parsed():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={"id": 1},
            headers={"github-authentication-token-expiration": "2026-12-01 12:00:00 +0200"},
        )

    with make_client(handler) as github:
        github.get_run(1)

    assert github.token_expires_at == datetime(2026, 12, 1, 10, 0, tzinfo=UTC)


def test_download_artifact_follows_redirect_and_writes_file(tmp_path):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "objects.example":
            # The GitHub token must not follow the redirect to the storage host.
            assert "authorization" not in request.headers
            return httpx2.Response(200, content=b"artifact bytes")
        assert request.headers["authorization"] == "Bearer token"
        return httpx2.Response(302, headers={"Location": "https://objects.example/signed/download"})

    destination = tmp_path / "artifact.zip"
    with make_client(handler) as github:
        github.download_artifact(
            {
                "archive_download_url": "https://api.github.com/repos/oarepo/x/actions/artifacts/1/zip"
            },
            destination,
        )

    assert destination.read_bytes() == b"artifact bytes"


def test_download_artifact_rejects_a_non_github_url(tmp_path):
    destination = tmp_path / "artifact.zip"
    with make_client(lambda request: httpx2.Response(200)) as github:
        with pytest.raises(GitHubError, match="api.github.com"):
            github.download_artifact(
                {"archive_download_url": "http://169.254.169.254/latest/meta-data"},
                destination,
            )


def test_download_artifact_stops_over_the_limit(tmp_path):
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"0123456789")

    destination = tmp_path / "artifact.zip"
    with make_client(handler) as github:
        with pytest.raises(DownloadTooLarge):
            github.download_artifact(
                {
                    "archive_download_url": "https://api.github.com/repos/oarepo/x/actions/artifacts/1/zip"
                },
                destination,
                max_bytes=5,
            )
