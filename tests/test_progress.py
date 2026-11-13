import json
from pathlib import Path

from invenio_testrig_dashboard.progress import MAX_FAILED, MAX_NAME_LENGTH, progress_from_jobs

FIXTURE = Path(__file__).parent / "fixtures" / "github" / "run-43623789406-jobs-in-progress.json"


def job(name: object, status: str = "completed", conclusion: str | None = "success") -> dict:
    return {"name": name, "status": status, "conclusion": conclusion}


def test_the_real_mid_run_job_list():
    jobs = json.loads(FIXTURE.read_text())["jobs"]

    progress = progress_from_jobs(jobs)

    assert progress is not None
    # 58 package jobs; 31 jobs succeeded but two of them are prepare/small_wait, plus 1 failure.
    assert progress.total == 58
    assert progress.done == 30
    assert progress.failed == ("invenio-records-rest",)


def test_helper_jobs_are_not_package_jobs():
    assert progress_from_jobs([job("verify-patches / prepare")]) is None
    assert progress_from_jobs([job("verify-patches / small_wait")]) is None
    assert progress_from_jobs([job("verify-patches / report")]) is None

    progress = progress_from_jobs(
        [job("verify-patches / prepare"), job("verify-patches / test (pkg-a)")]
    )

    assert progress == (1, 1, ())


def test_shards_report_their_package_once():
    progress = progress_from_jobs(
        [
            job("verify-patches / test_slow (pkg#0)", conclusion="failure"),
            job("verify-patches / test_slow (pkg#1)", conclusion="cancelled"),
            job("verify-patches / test_slow (pkg#2)", conclusion="timed_out"),
            job("verify-patches / test_slow (pkg#3)", conclusion="success"),
        ]
    )

    assert progress is not None
    assert (progress.total, progress.done) == (4, 4)
    assert progress.failed == ("pkg",)


def test_repository_and_e2e_jobs_get_the_names_parsing_uses():
    progress = progress_from_jobs(
        [
            job("verify-patches / test_repository"),
            job("verify-patches / test_e2e", conclusion="failure"),
        ]
    )

    assert progress is not None
    assert (progress.total, progress.done) == (2, 2)
    assert progress.failed == ("e2e",)


def test_cancelled_counts_as_failed_and_skipped_does_not():
    progress = progress_from_jobs(
        [
            job("verify-patches / test (a)", conclusion="cancelled"),
            job("verify-patches / test (b)", conclusion="skipped"),
            job("verify-patches / test (c)", conclusion="neutral"),
        ]
    )

    assert progress is not None
    assert progress.failed == ("a",)


def test_unknown_job_names_are_ignored():
    assert progress_from_jobs([job("some-other-job"), job("also / unrelated (x)")]) is None

    progress = progress_from_jobs([job("other"), job("verify-patches / test (a)")])

    assert progress is not None
    assert progress.total == 1


def test_unusable_package_names_are_dropped():
    progress = progress_from_jobs(
        [
            job("verify-patches / test (ok)"),
            job("verify-patches / test (nul\x00pkg)"),
            job(f"verify-patches / test ({'x' * (MAX_NAME_LENGTH + 1)})"),
            job(None),  # not a string at all
        ]
    )

    assert progress is not None
    assert progress.total == 1


def test_failed_names_are_capped():
    jobs = [
        job(f"verify-patches / test (pkg-{index:03d})", conclusion="failure")
        for index in range(105)
    ]

    progress = progress_from_jobs(jobs)

    assert progress is not None
    assert len(progress.failed) == MAX_FAILED
    assert progress.total == 105


def test_in_progress_jobs_count_as_total_but_not_done():
    progress = progress_from_jobs(
        [
            job("verify-patches / test (done)"),
            job("verify-patches / test (busy)", status="in_progress", conclusion=None),
        ]
    )

    assert progress == (2, 1, ())
