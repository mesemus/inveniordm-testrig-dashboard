"""Postgres backups to S3: pg_dump, upload + verify, prune.

Dumps go to their own bucket (``s3_backup_bucket``), never the artifacts bucket, as
``YYYY/MM/DD/invenio-testrig-dashboard-HHMMSS.dump`` (UTC, the dump's start time).
"""

import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from .redact import redact
from .settings import Settings

log = logging.getLogger(__name__)

PG_DUMP = "pg_dump"
# pg_dump refuses a server newer than itself; the bundled Postgres is 18.
PG_DUMP_MIN_MAJOR = 18
PG_DUMP_TIMEOUT_SECONDS = 1800
KEY_RE = re.compile(
    r"^(\d{4})/(\d{2})/(\d{2})/invenio-testrig-dashboard-(\d{2})(\d{2})(\d{2})\.dump$"
)
# libpq reads these; any inherited value (e.g. a stray PGSERVICE) could redirect the dump.
_LIBPQ_PREFIX = "PG"


class BackupError(RuntimeError):
    """The dump, upload or verification failed; nothing usable was stored."""


@dataclass
class BackupResult:
    key: str
    size: int
    seconds: float
    pruned: list[str] = field(default_factory=list)
    prune_error: str | None = None


def backup_key(started_at: datetime) -> str:
    """The only place the backup key is built."""
    started = started_at.astimezone(UTC)
    return f"{started:%Y/%m/%d}/invenio-testrig-dashboard-{started:%H%M%S}.dump"


def key_time(key: str) -> datetime | None:
    """The dump time encoded in a backup key, or None for a key we didn't write."""
    match = KEY_RE.fullmatch(key)
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except ValueError:  # e.g. month 13: looks like ours, isn't
        return None


def pg_environment(settings: Settings) -> dict[str, str]:
    """libpq environment for pg_dump: the password never goes on the command line.

    Command lines are visible to anyone who can run `ps` in the container.
    """
    url = make_url(settings.database_url.get_secret_value())
    env = {k: v for k, v in os.environ.items() if not k.startswith(_LIBPQ_PREFIX)}
    parts = {
        "PGHOST": url.host,
        "PGPORT": str(url.port) if url.port else None,
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
        "PGDATABASE": url.database,
        "PGSSLMODE": url.query.get("sslmode"),
    }
    env.update({key: str(value) for key, value in parts.items() if value})
    return env


def _pg_dump_major() -> int:
    try:
        result = subprocess.run(  # noqa: S603 - fixed arguments
            [PG_DUMP, "--version"], capture_output=True, text=True, check=True, timeout=30
        )
    except FileNotFoundError as exc:
        raise BackupError(f"{PG_DUMP} not found (install the PostgreSQL 18 client)") from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise BackupError(f"{PG_DUMP} --version failed: {exc}") from exc
    match = re.search(r"(\d+)(?:\.\d+)?", result.stdout)
    if match is None:
        raise BackupError(f"cannot read the {PG_DUMP} version from {result.stdout.strip()!r}")
    return int(match.group(1))


def dump(settings: Settings, path: Path, timeout: float = PG_DUMP_TIMEOUT_SECONDS) -> None:
    """pg_dump --format=custom into path; BackupError on any failure (stderr redacted)."""
    major = _pg_dump_major()
    if major < PG_DUMP_MIN_MAJOR:
        raise BackupError(f"{PG_DUMP} {major} is too old; the server needs {PG_DUMP_MIN_MAJOR}+")
    command = [PG_DUMP, "--format=custom", "--no-password", "--file", str(path)]
    try:
        result = subprocess.run(  # noqa: S603 - fixed arguments; the target comes from PG* env
            command,
            env=pg_environment(settings),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BackupError(f"{PG_DUMP} timed out after {timeout:g} s") from exc
    if result.returncode != 0:
        stderr = redact(result.stderr.strip(), settings)[-2000:]
        raise BackupError(f"{PG_DUMP} failed (exit {result.returncode}): {stderr}")
    if not path.is_file() or path.stat().st_size == 0:
        raise BackupError(f"{PG_DUMP} exited 0 but wrote no dump")


def upload(s3: Any, bucket: str, path: Path, key: str) -> int:
    """Upload the dump and check the stored size; returns it. BackupError on a mismatch."""
    size = path.stat().st_size
    s3.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": "application/octet-stream"})
    stored = s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
    if stored != size:
        raise BackupError(f"uploaded {key} has {stored} bytes, the dump has {size}")
    return size


def prune(s3: Any, bucket: str, now: datetime, retention_days: int, keep: str) -> list[str]:
    """Delete our dumps older than retention_days; returns the deleted keys.

    The age comes from the key, not LastModified (a copy or re-upload doesn't reset it). Keys we
    didn't write are left alone. With bucket versioning a delete only adds a delete marker.
    """
    cutoff = now - timedelta(days=retention_days)
    expired: list[str] = []
    ignored = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for item in page.get("Contents", []):
            key = item["Key"]
            dumped_at = key_time(key)
            if dumped_at is None:
                ignored += 1
            elif key != keep and dumped_at < cutoff:
                expired.append(key)
    if ignored:
        log.info("prune: left %d object(s) alone that aren't our dumps", ignored)
    for start in range(0, len(expired), 1000):
        batch = expired[start : start + 1000]
        response = s3.delete_objects(
            Bucket=bucket, Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True}
        )
        if response.get("Errors"):
            first = response["Errors"][0]
            raise RuntimeError(f"could not delete {first.get('Key')}: {first.get('Code')}")
    return expired


def run_backup(settings: Settings, s3: Any, now: datetime | None = None) -> BackupResult:
    """Dump, upload, verify, then prune. Raises BackupError if no verified dump was stored;
    a failed prune is reported in the result (the new dump is safe, the next night retries).
    """
    bucket = settings.s3_backup_bucket
    if bucket is None:
        raise BackupError("s3_backup_bucket is not set")
    started_at = now or datetime.now(UTC)
    key = backup_key(started_at)
    clock = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="itd-backup-") as tmp:
        path = Path(tmp) / "backup.dump"
        dump(settings, path)
        size = upload(s3, bucket, path, key)
    result = BackupResult(key=key, size=size, seconds=time.monotonic() - clock)
    try:
        result.pruned = prune(s3, bucket, started_at, settings.backup_retention_days, keep=key)
    except Exception as exc:
        result.prune_error = f"{type(exc).__name__}: {exc}"
        log.warning("backup stored, but pruning old dumps failed: %s", result.prune_error)
    return result
