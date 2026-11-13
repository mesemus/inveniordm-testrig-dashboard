"""Artifact listing (expected files) and the presigned-URL redirect."""

import logging
import uuid
from urllib.parse import quote

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from ..models import PackageResult
from ..parsing import RESERVED_PACKAGE_NAMES
from ..runs import RunRef
from ..storage import run_prefix
from .deps import S3Dep, S3PresignDep, SessionDep, SettingsDep, run_or_404

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["artifacts"])

PRESIGN_EXPIRES = 900
MISSING_CODES = {"404", "NoSuchKey", "NotFound"}
# e2e is the one synthetic package that testrig runs on the patched side; keep this in sync
# with parsing._side (which chooses patched unless patched was skipped).
E2E_PACKAGE = "e2e"


def _layout(package: PackageResult) -> tuple[str, str]:
    """The report directory and the side that was parsed.

    Rows ingested before artifact paths were recorded have neither, so fall back to testrig's convention.
    """
    side = package.side or ("patched" if package.package == E2E_PACKAGE else "original")
    return package.artifact_dir or f"artifacts/{package.package}", side


def expected_artifacts(run_uid: uuid.UUID, package: PackageResult) -> dict[str, str]:
    """Names of the files testrig normally writes, as API URLs (existence is not checked)."""
    directory, side = _layout(package)
    paths = {
        "log": f"{directory}/{side}_log.log",
        "simplified_log": f"{directory}/{side}_simplified_log.log",
        "freeze": f"{directory}/{side}_freeze.txt",
        "warnings": f"{directory}/{side}_warnings.json",
    }
    if package.has_junit:
        paths["junit"] = f"{directory}/{side}_junit.xml"
    if package.package not in RESERVED_PACKAGE_NAMES:
        paths["top_commits"] = f"top_commits/{package.package}_unpatched.json"
    if package.package == E2E_PACKAGE:
        paths["playwright_report"] = f"{directory}/patched_playwright-report/index.html"
        paths["server_log"] = f"{directory}/patched_server_log.log"
    return {
        name: f"/api/runs/{run_uid}/artifacts/{quote(path, safe='/')}"
        for name, path in paths.items()
    }


def _check_path(path: str) -> None:
    if not path or path.startswith("/") or "\\" in path or "\x00" in path or ".." in path:
        raise HTTPException(400, "invalid artifact path")


@router.get("/runs/{uid}/artifacts/{path:path}", response_class=RedirectResponse)
def get_artifact(
    uid: uuid.UUID,
    path: str,
    session: SessionDep,
    settings: SettingsDep,
    s3: S3Dep,
    s3_presign: S3PresignDep,
) -> RedirectResponse:
    """Redirect to a short-lived presigned URL; nothing large flows through the API."""
    run = run_or_404(session, uid)
    _check_path(path)

    key = run_prefix(RunRef(run.id, run.uid, run.created_at)) + path
    try:
        s3.head_object(Bucket=settings.s3_bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in MISSING_CODES:
            # Retention, a skipped symlink, or a file testrig never produced: all normal.
            raise HTTPException(404, "artifact removed (retention)") from exc
        raise

    url = s3_presign.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=PRESIGN_EXPIRES,
    )
    return RedirectResponse(url, status_code=307)
