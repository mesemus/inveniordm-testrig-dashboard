"""Read pytest JUnit XML. Artifacts are untrusted input, so parse defensively."""

import hashlib
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import Element, ParseError

import defusedxml.ElementTree as ET
from defusedxml import DefusedXmlException

from .status import TestStatus

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_NAME_BYTES = 16 * 1024
NAME_HASH_LENGTH = 16
NAME_PREFIX_BYTES = MAX_NAME_BYTES - len("\u2026".encode("utf-8")) - NAME_HASH_LENGTH
MAX_MESSAGE_LENGTH = 2000

# Higher wins when a test case appears twice; skipped ranks below passed, so a real outcome wins.
_SEVERITY = {
    TestStatus.SKIPPED: 0,
    TestStatus.PASSED: 1,
    TestStatus.FAILED: 2,
    TestStatus.ERROR: 3,
}


class JunitError(Exception):
    """The JUnit file cannot be read or parsed."""


@dataclass(frozen=True)
class ParsedCase:
    classname: str
    name: str
    status: TestStatus
    duration_s: float | None
    message: str | None


@dataclass(frozen=True)
class JunitResult:
    cases: tuple[ParsedCase, ...]
    duration_s: float | None


def _shorten(value: str) -> str:
    """Apply the 16 KB UTF-8 safety cap; real names are far below it."""
    encoded = value.encode("utf-8")
    if len(encoded) <= MAX_NAME_BYTES:
        return value
    digest = hashlib.sha256(encoded).hexdigest()[:NAME_HASH_LENGTH]
    prefix = encoded[:NAME_PREFIX_BYTES].decode("utf-8", errors="ignore")
    return f"{prefix}\u2026{digest}"


def _float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _message(element: Element) -> str | None:
    parts = [element.get("message"), element.text]
    text = "\n".join(part.strip() for part in parts if part and part.strip())
    return text[:MAX_MESSAGE_LENGTH] or None


def _case_status(element: Element) -> tuple[TestStatus, str | None]:
    worst: tuple[TestStatus, str | None] | None = None
    for child in element:
        if child.tag == "error":
            candidate = (TestStatus.ERROR, _message(child))
        elif child.tag == "failure":
            candidate = (TestStatus.FAILED, _message(child))
        elif child.tag == "skipped":
            candidate = (TestStatus.SKIPPED, None)
        else:
            continue
        if worst is None or _SEVERITY[candidate[0]] > _SEVERITY[worst[0]]:
            worst = candidate
    return worst or (TestStatus.PASSED, None)


def _parse_xml(path: Path) -> Element:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise JunitError(f"cannot read file: {exc}") from exc
    if size > MAX_FILE_BYTES:
        raise JunitError(f"file too large ({size} bytes, limit {MAX_FILE_BYTES})")
    try:
        # Bytes, not text, so the XML encoding declaration is honoured.
        return ET.fromstring(path.read_bytes())
    except (DefusedXmlException, ParseError, ValueError, OSError) as exc:
        raise JunitError(str(exc)[:MAX_MESSAGE_LENGTH]) from exc


def read_junit(path: Path) -> JunitResult:
    """Parse a pytest JUnit file, applying name shortening and duplicate collapsing."""
    root = _parse_xml(path)
    if root.tag not in ("testsuites", "testsuite"):
        raise JunitError(f"unexpected root element <{root.tag[:100]}>")
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")

    suite_times = []
    for suite in suites:
        time = _float(suite.get("time"))
        if time is not None:
            suite_times.append(time)
    duration_s = sum(suite_times) if suite_times else None

    cases: dict[tuple[str, str], ParsedCase] = {}
    # iter() also finds test cases in nested <testsuite> elements.
    for element in root.iter("testcase"):
        classname = _shorten(element.get("classname") or "")
        name = _shorten(element.get("name") or "")
        status, message = _case_status(element)
        case = ParsedCase(
            classname=classname,
            name=name,
            status=status,
            duration_s=_float(element.get("time")),
            message=message,
        )

        key = (classname, name)
        previous = cases.get(key)
        if previous is None:
            cases[key] = case
            continue

        kept = case if _SEVERITY[status] > _SEVERITY[previous.status] else previous
        log.warning(
            "duplicate test case %s::%s (%s vs %s), keeping %s",
            classname,
            name,
            previous.status.name,
            status.name,
            kept.status.name,
        )
        cases[key] = kept

    return JunitResult(cases=tuple(cases.values()), duration_s=duration_s)
