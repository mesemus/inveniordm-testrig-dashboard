"""S3 storage: client creation, run prefixes, and directory uploads."""

import logging
from datetime import UTC
from mimetypes import guess_type
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

from .models import Run
from .runs import RunRef
from .settings import Settings

log = logging.getLogger(__name__)


def _make_client(settings: Settings, endpoint_url: str | None) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        # RustFS and most S3-compatible servers need path-style URLs.
        config=Config(s3={"addressing_style": "path"}),
    )


def make_s3_client(settings: Settings) -> Any:
    return _make_client(settings, settings.s3_endpoint_url)


def make_presign_client(settings: Settings) -> Any:
    """A client that signs URLs against the endpoint the browser can reach.

    Inside compose/k8s the app talks to an internal S3 address while the browser needs the
    public one; the host is signed, so the two cases need two clients.
    """
    return _make_client(settings, settings.s3_public_endpoint_url or settings.s3_endpoint_url)


def run_prefix(run: Run | RunRef) -> str:
    """The only place the S3 run prefix is built: ``runs/YYYY/MM/DD/<uid>/``."""
    created_at = run.created_at.astimezone(UTC)
    return f"runs/{created_at:%Y/%m/%d}/{run.uid}/"


def content_type(name: str) -> str:
    """Content type for an uploaded file; text gets an explicit UTF-8 charset (logs are UTF-8)."""
    guessed = guess_type(name)[0] or "text/plain"
    return f"{guessed}; charset=utf-8" if guessed.startswith("text/") else guessed


def upload_dir(s3: Any, bucket: str, local_dir: Path, prefix: str) -> None:
    """Upload every regular file under local_dir, keeping the relative layout (idempotent).

    Uploaded artifacts are public, so this is the last gate before publication: symlinks are
    never followed (a link could point at a secret on this machine), and only files that
    really live inside local_dir are uploaded.
    """
    root = local_dir.resolve()
    for path in sorted(local_dir.rglob("*")):  # rglob doesn't descend into symlinked dirs
        if path.is_symlink():
            log.warning("not uploading symlink %s", path.relative_to(local_dir).as_posix())
            continue
        if not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        key = prefix + path.relative_to(local_dir).as_posix()
        s3.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type(path.name)})
