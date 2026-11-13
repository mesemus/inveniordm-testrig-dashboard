"""Minimal GitHub REST client: workflow runs and artifacts."""

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2

from .settings import Settings

log = logging.getLogger(__name__)

API_VERSION = "2022-11-28"
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
MAX_GH_RUN_ID = 2**63 - 1
MAX_PAGES = 100
TOKEN_EXPIRY_WARNING = timedelta(days=14)
EXPIRY_HEADER = "github-authentication-token-expiration"


class GitHubError(Exception):
    """Base class for errors reported by the GitHub client."""


class GitHubAuthError(GitHubError):
    """The GitHub token is invalid, expired, or lacks permissions."""


class GitHubRateLimited(GitHubError):
    """The GitHub API rate limit is exhausted."""


class DownloadTooLarge(GitHubError):
    """The artifact download exceeded the configured byte limit."""


def _next_link(header: str | None) -> str | None:
    if not header:
        return None
    for part in header.split(","):
        match = re.search(r'<([^>]+)>\s*;\s*rel="next"', part)
        if match:
            return match.group(1)
    return None


def _parse_expiry(value: str) -> datetime | None:
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d %H:%M:%S %z"):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


class GitHub:
    """The calls ingest needs; one method per HTTP call (plus pagination)."""

    def __init__(
        self,
        token: str,
        repo: str,
        transport: httpx2.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.repo = repo
        self.token_expires_at: datetime | None = None
        self._expiry_warned = False
        self.client = httpx2.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            },
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    def __enter__(self) -> GitHub:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    def _check(self, response: httpx2.Response) -> None:
        self._record_expiry(response)
        if response.status_code == 401:
            raise GitHubAuthError("GitHub authentication failed")
        if response.status_code in (403, 429):
            if response.headers.get("x-ratelimit-remaining") == "0":
                raise GitHubRateLimited("GitHub API rate limit exhausted")
        response.raise_for_status()

    def _record_expiry(self, response: httpx2.Response) -> None:
        value = response.headers.get(EXPIRY_HEADER)
        if not value:
            return
        parsed = _parse_expiry(value)
        if parsed is None:
            if not self._expiry_warned:
                self._expiry_warned = True
                log.warning("could not parse %s: %r", EXPIRY_HEADER, value[:100])
            return
        self.token_expires_at = parsed
        if not self._expiry_warned:
            self._expiry_warned = True
            if parsed - datetime.now(UTC) < TOKEN_EXPIRY_WARNING:
                log.warning("GitHub token expires soon: %s", parsed.isoformat())

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx2.Response:
        response = self.client.get(path, params=params)
        self._check(response)
        return response

    def _paginate(self, path: str, params: dict[str, Any] | None, key: str) -> list[dict]:
        results: list[dict] = []
        url: str | None = path
        current_params = params
        for _ in range(MAX_PAGES):
            if not url:
                return results
            response = self.client.get(url, params=current_params)
            self._check(response)
            results.extend(response.json().get(key, []))
            url = _next_link(response.headers.get("link"))
            current_params = None
        raise GitHubError(f"pagination did not stop after {MAX_PAGES} pages")

    def list_dispatch_runs(self, created_since: datetime) -> list[dict]:
        created = created_since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self._paginate(
            f"/repos/{self.repo}/actions/runs",
            {
                "event": "workflow_dispatch",
                "created": f">={created}",
                "per_page": 100,
            },
            "workflow_runs",
        )

    def get_run(self, gh_run_id: int) -> dict:
        return self._get(f"/repos/{self.repo}/actions/runs/{gh_run_id}").json()

    def list_artifacts(self, gh_run_id: int) -> list[dict]:
        return self._paginate(
            f"/repos/{self.repo}/actions/runs/{gh_run_id}/artifacts",
            {"per_page": 100},
            "artifacts",
        )

    def list_jobs(self, gh_run_id: int) -> list[dict]:
        """The latest attempt of every job of a run (more than 100 jobs are paginated).

        ``filter=latest`` means a "Re-run failed jobs" does not double-count a job.
        """
        return self._paginate(
            f"/repos/{self.repo}/actions/runs/{gh_run_id}/jobs",
            {"filter": "latest", "per_page": 100},
            "jobs",
        )

    def download_artifact(
        self,
        artifact: dict,
        dest_path: Path,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
    ) -> None:
        """Stream the artifact to dest_path, aborting over max_bytes (headers can lie)."""
        url = artifact.get("archive_download_url")
        if not isinstance(url, str) or not url:
            raise GitHubError("artifact has no download URL")
        # API values are input too: only ask GitHub for the download. The redirect that
        # follows may legitimately go to GitHub's storage host.
        parts = httpx2.URL(url)
        if parts.scheme != "https" or parts.host != "api.github.com":
            raise GitHubError("artifact download URL is not on api.github.com")
        with self.client.stream("GET", url) as response:
            self._check(response)
            total = 0
            with open(dest_path, "wb") as file:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise DownloadTooLarge(f"artifact download exceeded {max_bytes} bytes")
                    file.write(chunk)


def make_github_client(settings: Settings) -> GitHub:
    return GitHub(
        settings.github_token.get_secret_value(),
        settings.runner_repository,
    )
