"""Safe extraction of downloaded artifacts (untrusted input)."""

import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from .runs import RunUnusable

MAX_EXTRACT_BYTES = 1024 * 1024 * 1024
MAX_EXTRACT_FILES = 20_000


def find_report_root(root: Path) -> Path | None:
    """The shallowest directory containing a report (``data.json``, or ``results.json``)."""
    for name in ("data.json", "results.json"):
        matches = sorted(root.rglob(name), key=lambda path: len(path.parts))
        if matches:
            return matches[0].parent
    return None


def _safe_name(name: str) -> None:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts:
        raise RunUnusable(f"unsafe path in artifact: {name[:200]!r}")


def _is_regular_zip_member(info: zipfile.ZipInfo) -> bool:
    if info.is_dir():
        return False
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    # Many zips (and zipfile.writestr) carry permission bits only, no file type.
    return file_type == 0 or stat.S_ISREG(mode)


def _check_extract_limits(total_bytes: int, total_files: int) -> None:
    if total_bytes > MAX_EXTRACT_BYTES:
        raise RunUnusable(f"artifact too large to extract (over {MAX_EXTRACT_BYTES} bytes)")
    if total_files > MAX_EXTRACT_FILES:
        raise RunUnusable(f"artifact has too many files (over {MAX_EXTRACT_FILES})")


def extract_archive(archive: Path, dest: Path) -> None:
    """Extract a GitHub artifact zip (and any tar inside it) safely, within the limits.

    A tar is extracted into ``dest`` itself, not next to the tar; find_report_root() then
    picks the shallowest report, wherever it ended up.
    """
    total_bytes = 0
    total_files = 0
    try:
        with zipfile.ZipFile(archive) as zf:
            members = []
            for info in zf.infolist():
                # Validate every name, including entries we would skip: an unsafe path is
                # an unusable artifact, not something to silently ignore.
                _safe_name(info.filename)
                total_files += 1
                if _is_regular_zip_member(info):
                    total_bytes += info.file_size
                    members.append(info)
                _check_extract_limits(total_bytes, total_files)
            zf.extractall(dest, members=members)  # noqa: S202 (names validated, regular files only)
    except zipfile.BadZipFile as exc:
        raise RunUnusable(f"invalid artifact zip: {exc}") from exc

    for tar_path in sorted(dest.rglob("*")):
        lower = tar_path.name.lower()
        if not (lower.endswith(".tar") or lower.endswith(".tar.gz") or lower.endswith(".tgz")):
            continue
        try:
            with tarfile.open(tar_path) as tf:
                tar_members = tf.getmembers()
                for member in tar_members:
                    total_files += 1
                    if member.isreg():
                        total_bytes += member.size
                    _check_extract_limits(total_bytes, total_files)
                tf.extractall(dest, members=tar_members, filter="data")
        except tarfile.TarError as exc:
            raise RunUnusable(f"invalid artifact tar: {exc}") from exc
        tar_path.unlink(missing_ok=True)
