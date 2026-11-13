# Real testrig run: 2026-11-13

A trimmed copy of a **real** invenio-testrig report. It's the ground truth for the parser and the
ingest tests. Don't edit the data files by hand. If the format changes, add a new fixture
directory from a newer run instead.

## Provenance

| | |
|---|---|
| Workflow run | https://github.com/mesemus/invenio-testrig-client/actions/runs/43459288882 (`workflow_dispatch`, conclusion `failure`, 2026-11-13 16:06–16:20 UTC) |
| Published report | https://mesemus.github.io/invenio-testrig-client/invenio-testrig-2026-11-13-16-08-19/ |
| Source artifact | `final-report` (the report directory: `data.json` at the zip root, per-package files under `artifacts/`) |
| testrig version | `oarepo/invenio-testrig` master at `b7dfe5b2` ("scaling down large junit.xml files", 2026-11-13) |
| Seed repository | `oarepo/inveniordm-reference-repo@master` (`23ef074b`), e2e from `oarepo/invenio-e2e@main` |
| Result | 53 packages, 52 passed, 1 failed (`invenio-records-rest`: `test_index_prefixing.py::test_index_creation`), e2e passed, repository tests skipped. 10,932 JUnit test cases. |

The GitHub API responses for the run and its artifact list are saved next door in
`tests/fixtures/github/run-43459288882*.json` (for mocking GitHub in the ingest tests).

## What was kept

- `data.json`, `config.json`, `warnings.json`: unmodified.
- `artifacts/<package>/`: for **every** package, `original_status.json`, `patched_status.json`, and `original_junit.xml` (if it exists), unmodified.
- `artifacts/invenio-records-rest/` (the failing package) also has `original_freeze.txt`, `original_warnings.json`,
  and **the last 40 lines** of `original_log.log` / `original_simplified_log.log`. That's enough for the upload tests to have real log files to upload.
- `artifacts/invenio-rdm-records-0/`: one **shard** directory (status, junit, `selected_tests.txt`). See the quirks below.
- `artifacts/e2e/`: the status, an (empty) simplified log, and warnings. `artifacts/repo/`: status only.
- `top_commits/`: 2 of 53 files.

## What was removed (size)

All other logs and freeze files, shard directories `invenio-rdm-records-1..3`, the e2e Playwright report (~1 MB,
including screenshots) and server log (1.8 MB), `index.html`, `warnings.html`, and 51 `top_commits` files.
The full report is still on GitHub Pages (link above). The GitHub artifacts expire: the per-package artifacts after 1 day,
and `final-report`/`config` on 2027-02-11.

## Format quirks found in this run (the parser must handle them)

- **No `results.json` / `schema_version` yet.** The structured data is `data.json`, the same file the HTML report renders from.
- Each package has an **`original`** (unpatched) and a **`patched`** side, each with a `status`
  (seen: `success`, `failed`, `skipped`), plus `has_original_junit` / `has_patched_junit`. Without patches, packages run
  on the `original` side (`patched` = `skipped`), but **e2e is reversed** (`patched` = `success`, `original` = `skipped`).
- Files are prefixed with the side: `original_junit.xml`, `patched_status.json`, …
- `artefact_dir` (British spelling) is relative to the report root.
- **Sharded packages:** `invenio-rdm-records` ran as 4 shards (`artifacts/invenio-rdm-records-0..3`, each with
  `selected_tests.txt`). `data.json` lists only `invenio-rdm-records`, whose `original_junit.xml` contains all 4 `<testsuite>` elements.
  The shard directories aren't referenced and must be ignored.
- `repository` (repository tests) and `repository_e2e` are separate top-level entries, not in `packages`.
- **No per-package duration** in `data.json`. The sum of `<testsuite time=…>` in the JUnit file is the best source.
- **e2e has no JUnit file yet** (only the Playwright HTML report).
- Codestyle checks appear as normal JUnit test cases (`name="black"`, `"PYDOCSTYLE"`, `"ISORT"`, one per module).
- **Very long test names:** `invenio-s3` has parametrized names of ~10,000 characters, generated from random bytes
  (different in every run). testrig truncates some names and marks them with `truncated` (e.g. `test_invalid_zip_metadata[truncated]`).
- Timestamps in `data.json` are strings like `"2026-11-13 16:07:21 UTC"`. The JUnit timestamps are ISO 8601.
