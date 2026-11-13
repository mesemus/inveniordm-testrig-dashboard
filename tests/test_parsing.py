import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from invenio_testrig_dashboard import junit, parsing
from invenio_testrig_dashboard.parsing import (
    NO_RESULT_MESSAGE,
    SUITE_CLASSNAME,
    InvalidResults,
    parse_run_dir,
)
from invenio_testrig_dashboard.status import PackageStatus
from invenio_testrig_dashboard.status import TestStatus as Status

FIXTURES = Path(__file__).parent / "fixtures" / "runs"
REAL = FIXTURES / "real-2026-11-13"


def find_package(run, name):
    return next(package for package in run.packages if package.name == name)


def suite_cases(package):
    return [case for case in package.cases if case.classname == SUITE_CLASSNAME]


def write_report(tmp_path, packages, *, completed=True):
    data = {
        "started_at": "2026-11-13 16:07:21 UTC",
        "last_updated": "2026-11-13 16:20:33 UTC",
        "completed": completed,
        "python_version": "3.14",
        "packages": packages,
    }
    (tmp_path / "data.json").write_text(json.dumps(data))
    return tmp_path


def entry(
    artefact_dir,
    package,
    *,
    status="success",
    has_junit=False,
    patched_status="skipped",
    patched_junit=False,
):
    side = {
        "status": status,
        "package": {"reference": {"package": package}, "unpatched_reference": None},
    }
    skipped = {
        "status": "skipped",
        "package": {"reference": {"package": package}, "unpatched_reference": None},
    }
    return {
        "artefact_dir": artefact_dir,
        "has_original_junit": has_junit,
        "has_patched_junit": patched_junit,
        "original": side,
        "patched": {**skipped, "status": patched_status},
    }


def write_junit(tmp_path, xml):
    directory = tmp_path / "artifacts" / "x"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "original_junit.xml").write_text(xml)
    return tmp_path


def test_real_fixture_packages_and_statuses():
    run = parse_run_dir(REAL)

    assert len(run.packages) == 55
    assert "invenio-rdm-records-0" not in {package.name for package in run.packages}
    counts = Counter(package.status for package in run.packages)
    assert counts == {PackageStatus.PASSED: 53, PackageStatus.FAILED: 1, PackageStatus.NOT_RUN: 1}
    assert find_package(run, "e2e").status is PackageStatus.PASSED
    assert find_package(run, "repo").status is PackageStatus.NOT_RUN


def test_real_fixture_failure_and_case_counts():
    run = parse_run_dir(REAL)
    failed = find_package(run, "invenio-records-rest")

    failing_cases = [case for case in failed.cases if case.status in (Status.FAILED, Status.ERROR)]
    assert [(case.classname, case.name) for case in failing_cases] == [
        ("tests.test_index_prefixing", "test_index_creation")
    ]
    assert failing_cases[0].message.startswith("AssertionError")
    assert suite_cases(failed) == []

    parsed_cases = sum(
        1 for package in run.packages for case in package.cases if case.classname != SUITE_CLASSNAME
    )
    assert parsed_cases == 10932
    rdm_cases = [
        c for c in find_package(run, "invenio-rdm-records").cases if c.classname != SUITE_CLASSNAME
    ]
    assert len(rdm_cases) == 1178


def test_real_fixture_references_and_timestamps():
    run = parse_run_dir(REAL)
    access = find_package(run, "invenio-access")

    assert run.name == "Invenio Testrig"
    assert access.version == "7.1.0"
    assert access.git_commit.startswith("1251b1d9")
    assert access.commits_ahead == 3
    assert run.started_at == datetime(2026, 11, 13, 16, 7, 21, tzinfo=UTC)
    assert run.completed is True


def test_real_fixture_keeps_long_names_and_keeps_them_unique():
    run = parse_run_dir(REAL)
    s3 = find_package(run, "invenio-s3")

    longest = max(len(case.name) for case in s3.cases)
    assert longest == 10036  # unchanged by the 16 KB safety cap

    names = [(case.classname, case.name) for case in s3.cases]
    assert len(names) == len(set(names))


def test_ok_fixture_every_package_has_passed_suite_case():
    run = parse_run_dir(FIXTURES / "ok")

    assert run.name is None

    assert {package.name for package in run.packages} == {"pkg-a", "e2e"}
    for package in run.packages:
        assert [case.status for case in suite_cases(package)] == [Status.PASSED]
    assert find_package(run, "e2e").has_junit is False


def test_failing_fixture_statuses_and_suite_cases():
    run = parse_run_dir(FIXTURES / "failing")

    two_failing = find_package(run, "pkg-two-failing")
    assert two_failing.status is PackageStatus.FAILED
    assert [case.status for case in two_failing.cases].count(Status.FAILED) == 2
    for case in two_failing.cases:
        if case.message is not None:
            assert len(case.message) <= 2000

    both = [case for case in two_failing.cases if case.name == "test_fail_and_teardown_error"]
    assert [case.status for case in both] == [Status.ERROR]
    assert "RuntimeError" in both[0].message

    error = find_package(run, "pkg-error")
    assert error.status is PackageStatus.FAILED
    assert [case.status for case in error.cases if case.name == "test_boom"] == [Status.ERROR]

    no_junit = find_package(run, "pkg-no-junit")
    assert no_junit.has_junit is False
    assert [case.status for case in suite_cases(no_junit)] == [Status.ERROR]

    flag_missing = find_package(run, "pkg-flag-missing")
    assert flag_missing.has_junit is False
    assert [case.status for case in suite_cases(flag_missing)] == [Status.ERROR]

    all_pass = find_package(run, "pkg-all-pass")
    assert all_pass.status is PackageStatus.FAILED
    assert [case.status for case in all_pass.cases if case.classname != SUITE_CLASSNAME] == [
        Status.PASSED,
        Status.PASSED,
    ]
    assert [case.status for case in suite_cases(all_pass)] == [Status.ERROR]


def test_params_fixture_parametrization_duplicate_and_long_names():
    run = parse_run_dir(FIXTURES / "params")
    params = find_package(run, "params-pkg")
    names = {case.name for case in params.cases}

    assert {"test_x[a]", "test_x[b]"} <= names
    duplicates = [case for case in params.cases if case.name == "test_dup"]
    assert len(duplicates) == 1
    assert duplicates[0].status is Status.FAILED

    ascii_case = next(case for case in params.cases if case.name.startswith("test_ascii["))
    ascii_case2 = next(case for case in params.cases if case.name.startswith("test_ascii2["))
    unicode_case = next(case for case in params.cases if case.name.startswith("test_unicode["))
    big_class = next(case for case in params.cases if case.name == "test_big_class")
    for case in params.cases:
        assert len(case.name.encode("utf-8")) <= junit.MAX_NAME_BYTES
        assert len(case.classname.encode("utf-8")) <= junit.MAX_NAME_BYTES
    assert len(ascii_case.name) < 20_000  # the 20 KB input was capped
    assert len(big_class.classname) < 20_000
    # Same first 16 KB, different tail: the hash suffix keeps them distinct.
    assert ascii_case.name != ascii_case2.name
    assert ascii_case.name != unicode_case.name

    again = {
        (case.classname, case.name)
        for case in find_package(parse_run_dir(FIXTURES / "params"), "params-pkg").cases
    }
    assert again == {(case.classname, case.name) for case in params.cases}


def test_incomplete_fixture_parses_pending_package():
    run = parse_run_dir(FIXTURES / "incomplete")

    assert run.completed is False
    pending = find_package(run, "pkg-pending")
    assert pending.status is PackageStatus.NOT_RUN
    assert suite_cases(pending) == []


@pytest.mark.parametrize(
    ("fixture", "cause"),
    [
        ("bad_schema", "artefact_dir"),
        ("bad_status", "exploded"),
        ("escape_path", "artefact_dir"),
    ],
)
def test_invalid_fixtures_raise_readable_errors(fixture, cause):
    with pytest.raises(InvalidResults, match=cause):
        parse_run_dir(FIXTURES / fixture)


def test_bad_xml_does_not_fail_the_run():
    run = parse_run_dir(FIXTURES / "bad_xml")

    assert {package.name for package in run.packages} == {
        "pkg-truncated",
        "pkg-laughs",
        "pkg-html",
    }
    for package in run.packages:
        assert package.has_junit is False
        assert package.status is PackageStatus.ERROR
        [case] = suite_cases(package)
        assert case.status is Status.ERROR
        assert "original_junit.xml unreadable" in case.message


def test_results_json_is_detected_but_not_supported_yet(tmp_path):
    (tmp_path / "results.json").write_text("{}")

    with pytest.raises(InvalidResults, match="format v1"):
        parse_run_dir(tmp_path)


@pytest.mark.parametrize("package", ["x" * 201, "é" * 101])
def test_package_name_byte_length_is_bounded(tmp_path, package):
    write_report(tmp_path, [entry("artifacts/x", package)])

    with pytest.raises(InvalidResults, match="200 UTF-8 bytes"):
        parse_run_dir(tmp_path)


def test_pending_in_a_completed_run_is_an_error_with_a_reason(tmp_path):
    """A package whose job left no result crashed (or was cancelled); the rest is still good."""
    write_report(
        tmp_path,
        [
            entry("artifacts/x", "pkg-ok"),
            entry("artifacts/y", "pkg-pending", status="pending"),
        ],
    )

    run = parse_run_dir(tmp_path)

    assert run.completed is True
    assert {package.name: package.status for package in run.packages} == {
        "pkg-ok": PackageStatus.PASSED,
        "pkg-pending": PackageStatus.ERROR,
    }
    [case] = suite_cases(find_package(run, "pkg-pending"))
    assert case.status is Status.ERROR
    assert case.message == NO_RESULT_MESSAGE


def test_a_pending_patched_side_falls_back_to_the_original(tmp_path):
    write_report(
        tmp_path,
        [entry("artifacts/x", "pkg", status="success", patched_status="pending")],
    )

    [package] = parse_run_dir(tmp_path).packages

    assert package.status is PackageStatus.PASSED
    assert package.side == "original"


def _with_e2e(tmp_path, original: str, patched: str):
    write_report(tmp_path, [entry("artifacts/x", "pkg")])
    data = json.loads((tmp_path / "data.json").read_text())
    side = {"package": {"reference": {"package": "e2e"}, "unpatched_reference": None}}
    data["repository_e2e"] = {
        "artefact_dir": "artifacts/e2e",
        "has_original_junit": False,
        "has_patched_junit": False,
        "original": {"status": original, **side},
        "patched": {"status": patched, **side},
    }
    (tmp_path / "data.json").write_text(json.dumps(data))


@pytest.mark.parametrize(
    "original",
    [
        "skipped",  # run 44dc430b (2026-11-14)
        "pending",  # run 99fb4188 (2026-11-14): no artifacts/e2e at all
    ],
)
def test_a_crashed_e2e_job_is_an_error_not_not_run(tmp_path, original):
    """GitHub failed the e2e job, but testrig's completed report kept e2e "pending"."""
    _with_e2e(tmp_path, original=original, patched="pending")

    run = parse_run_dir(tmp_path)

    e2e = find_package(run, "e2e")
    assert e2e.status is PackageStatus.ERROR
    assert e2e.side == "patched"
    [case] = suite_cases(e2e)
    assert case.message == NO_RESULT_MESSAGE
    assert find_package(run, "pkg").status is PackageStatus.PASSED


def test_a_pending_e2e_in_an_unfinished_report_has_not_run_yet(tmp_path):
    _with_e2e(tmp_path, original="skipped", patched="pending")
    data = json.loads((tmp_path / "data.json").read_text())
    data["completed"] = False
    (tmp_path / "data.json").write_text(json.dumps(data))

    e2e = find_package(parse_run_dir(tmp_path), "e2e")

    assert e2e.status is PackageStatus.NOT_RUN
    assert suite_cases(e2e) == []


def test_absolute_artefact_dir_is_rejected(tmp_path):
    write_report(tmp_path, [entry("/etc", "pkg")])

    with pytest.raises(InvalidResults, match="escapes"):
        parse_run_dir(tmp_path)


def test_artefact_dir_is_canonicalized(tmp_path):
    write_report(tmp_path, [entry("artifacts/./pkg-a", "pkg-a")])

    assert parse_run_dir(tmp_path).packages[0].artifact_dir == "artifacts/pkg-a"


def test_invalid_json_is_reported(tmp_path):
    (tmp_path / "data.json").write_text("{not json")

    with pytest.raises(InvalidResults, match="not valid JSON"):
        parse_run_dir(tmp_path)


def test_non_object_json_is_reported(tmp_path):
    (tmp_path / "data.json").write_text("[]")

    with pytest.raises(InvalidResults, match="valid dictionary"):
        parse_run_dir(tmp_path)


def test_unreadable_junit_message_is_capped(tmp_path, monkeypatch):
    write_report(tmp_path, [entry("artifacts/x", "pkg", has_junit=True)])
    directory = tmp_path / "artifacts" / "x"
    directory.mkdir(parents=True)
    (directory / "original_junit.xml").write_text("<testsuites/>")

    def boom(path):
        raise junit.JunitError("x" * 10_000)

    monkeypatch.setattr(parsing, "read_junit", boom)

    [case] = suite_cases(parse_run_dir(tmp_path).packages[0])
    assert case.status is Status.ERROR
    assert len(case.message) <= 2000


def test_completed_run_without_packages_is_invalid(tmp_path):
    write_report(tmp_path, [])

    with pytest.raises(InvalidResults, match="no packages"):
        parse_run_dir(tmp_path)


def test_duplicate_package_names_are_rejected(tmp_path):
    write_report(tmp_path, [entry("artifacts/a", "pkg"), entry("artifacts/b", "pkg")])

    with pytest.raises(InvalidResults, match="duplicate package name"):
        parse_run_dir(tmp_path)


@pytest.mark.parametrize("package", ["e2e", "repo"])
def test_reserved_package_names_are_rejected(tmp_path, package):
    write_report(tmp_path, [entry("artifacts/a", package)])

    with pytest.raises(InvalidResults, match="reserved"):
        parse_run_dir(tmp_path)


def test_huge_json_integer_is_reported(tmp_path):
    payload = (
        '{"started_at": "x", "last_updated": "x", "completed": false, '
        '"python_version": "3.14", "packages": [], "x": ' + "9" * 5000 + "}"
    )
    (tmp_path / "data.json").write_text(payload)

    with pytest.raises(InvalidResults, match="not valid JSON"):
        parse_run_dir(tmp_path)


def test_deeply_nested_json_is_reported(tmp_path):
    (tmp_path / "data.json").write_text("[" * 200_000 + "]" * 200_000)

    with pytest.raises(InvalidResults, match="not valid JSON"):
        parse_run_dir(tmp_path)


def test_data_json_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(parsing, "MAX_DATA_JSON_BYTES", 1)
    (tmp_path / "data.json").write_text("{}")

    with pytest.raises(InvalidResults, match="too large"):
        parse_run_dir(tmp_path)


def test_non_finite_and_negative_times_become_none(tmp_path):
    write_report(tmp_path, [entry("artifacts/x", "pkg", has_junit=True)])
    write_junit(
        tmp_path,
        '<testsuites><testsuite name="pytest" time="nan">'
        '<testcase classname="t" name="nan" time="nan"/>'
        '<testcase classname="t" name="inf" time="inf"/>'
        '<testcase classname="t" name="negative" time="-1"/>'
        "</testsuite></testsuites>",
    )

    package = parse_run_dir(tmp_path).packages[0]
    assert package.duration_s is None
    assert all(case.duration_s is None for case in package.cases)


def test_nested_testsuites_are_parsed(tmp_path):
    write_report(tmp_path, [entry("artifacts/x", "pkg", has_junit=True)])
    write_junit(
        tmp_path,
        '<testsuites><testsuite name="outer" time="2.0">'
        '<testsuite name="inner" time="1.0">'
        '<testcase classname="t" name="nested_case" time="0.5"/>'
        "</testsuite></testsuite></testsuites>",
    )

    package = parse_run_dir(tmp_path).packages[0]
    parsed = [case.name for case in package.cases if case.classname != SUITE_CLASSNAME]
    assert parsed == ["nested_case"]
    assert package.duration_s == 2.0


def test_junit_symlink_escaping_the_report_is_rejected(tmp_path):
    write_report(tmp_path, [entry("artifacts/x", "pkg", has_junit=True)])
    directory = tmp_path / "artifacts" / "x"
    directory.mkdir(parents=True)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.xml"
    outside.write_text("<testsuites/>")
    (directory / "original_junit.xml").symlink_to(outside)

    package = parse_run_dir(tmp_path).packages[0]
    assert package.has_junit is False
    assert package.status is PackageStatus.ERROR
    [case] = suite_cases(package)
    assert "escapes" in case.message


def test_junit_size_limit(monkeypatch):
    monkeypatch.setattr(junit, "MAX_FILE_BYTES", 10)
    path = REAL / "artifacts" / "invenio-access" / "original_junit.xml"

    with pytest.raises(junit.JunitError, match="too large"):
        junit.read_junit(path)


def test_parsing_is_pure():
    assert not {"db", "models", "boto3", "httpx2"} & vars(parsing).keys()
    assert not {"db", "models", "boto3", "httpx2"} & vars(junit).keys()


def test_real_fixture_parses_quickly():
    started = time.perf_counter()
    parse_run_dir(REAL)
    assert time.perf_counter() - started < 5


@pytest.mark.parametrize(
    ("location", "value"),
    [
        ("actual_version", "1.0\x00"),
        ("commit", "ab\ud800cd"),
        ("package", "pkg\x00"),
        ("python_version", "3.14\x00"),
        ("artefact_dir", "artifacts/\x00x"),
    ],
)
def test_unstorable_text_in_data_json_is_rejected(tmp_path, location, value):
    # JSON allows \\u0000 and lone surrogates; Postgres/psycopg can't store them.
    package = entry("artifacts/x", "pkg")
    if location == "artefact_dir":
        package["artefact_dir"] = value
    elif location != "python_version":
        package["original"]["package"]["reference"][location] = value
    write_report(tmp_path, [package])
    if location == "python_version":
        data = json.loads((tmp_path / "data.json").read_text())
        data["python_version"] = value
        (tmp_path / "data.json").write_text(json.dumps(data))

    with pytest.raises(InvalidResults, match=location):
        parse_run_dir(tmp_path)


def test_failure_and_error_in_one_case_takes_the_error(tmp_path):
    # pytest writes both when a test fails and its teardown errors.
    write_report(tmp_path, [entry("artifacts/x", "pkg", status="failed", has_junit=True)])
    write_junit(
        tmp_path,
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="t" name="both">'
        '<failure message="assert 1 == 2"/><error message="teardown crashed"/>'
        "</testcase></testsuite></testsuites>",
    )

    (case,) = [c for c in parse_run_dir(tmp_path).packages[0].cases if c.name == "both"]
    assert case.status == Status.ERROR
    assert case.message == "teardown crashed"
