from sortie.db import Database
from sortie.models import Job, Status


def test_dedupe_and_counts(tmp_path):
    db = Database(tmp_path / "t.db")
    j = Job(site="naukri", site_job_id="42", url="u", title="t", company="c")
    assert not db.is_seen("naukri", "42")
    db.record(j, Status.APPLIED, fit_score=80)
    db.record(Job(site="naukri", site_job_id="43", url="u", title="t", company="c"), Status.SKIPPED, "x")
    assert db.is_seen("naukri", "42")
    assert db.applied_count("naukri") == 1
    assert db.applied_count("cutshort") == 0
    db.close()


def test_failed_jobs_are_retried_once(tmp_path):
    db = Database(tmp_path / "t.db")
    j = Job(site="naukri", site_job_id="7", url="u", title="t", company="c")
    db.record(j, Status.FAILED, "stuck")
    assert not db.is_seen("naukri", "7")
    db.record(j, Status.FAILED, "stuck again")
    assert db.is_seen("naukri", "7")
    db.close()
