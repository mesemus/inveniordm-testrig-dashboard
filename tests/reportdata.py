"""Reusable report generators for the slower tests (not a test module)."""

import json
from pathlib import Path


def write_volume_report(root: Path, packages: int = 55, cases: int = 50_000) -> None:
    root.mkdir(parents=True)
    per_package, extra = divmod(cases, packages)
    entries = []
    for index in range(packages):
        name = f"pkg-{index:02d}"
        package_cases = per_package + (1 if index < extra else 0)
        directory = root / "artifacts" / name
        directory.mkdir(parents=True)
        testcases = "".join(
            f'<testcase classname="tests.test_{index}" name="test_{case}" time="0.01"/>'
            for case in range(package_cases)
        )
        (directory / "original_junit.xml").write_text(
            f'<testsuites><testsuite name="pytest" time="1.0">{testcases}</testsuite></testsuites>'
        )
        package = {"reference": {"package": name}, "unpatched_reference": None}
        entries.append(
            {
                "artefact_dir": f"artifacts/{name}",
                "has_original_junit": True,
                "has_patched_junit": False,
                "original": {"status": "success", "package": package},
                "patched": {"status": "skipped", "package": package},
            }
        )

    (root / "data.json").write_text(
        json.dumps(
            {
                "started_at": "2026-11-13 16:07:21 UTC",
                "last_updated": "2026-11-13 16:20:33 UTC",
                "completed": True,
                "python_version": "3.14",
                "packages": entries,
            }
        )
    )
