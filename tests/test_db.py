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


def test_reset_failures(tmp_path):
    from sortie.db import Database
    from sortie.models import Job, Status
    db = Database(tmp_path / "t.db")
    jobs = [Job("indeed", str(i), f"https://x/{i}", "Dev", "Acme") for i in range(3)]
    for status in (Status.FAILED, Status.FAILED):
        db.record(jobs[0], status)
    db.record(jobs[1], Status.FAILED)
    db.record(jobs[1], Status.APPLIED)
    db.record(jobs[2], Status.SKIPPED)
    assert db.is_seen("indeed", "0")
    assert db.reset_failures("indeed") == 1
    assert not db.is_seen("indeed", "0") and db.is_seen("indeed", "1") and db.is_seen("indeed", "2")
    db.close()


def test_needs_you_list(tmp_path):
    from sortie.db import Database
    from sortie.models import NeedsYou
    db = Database(tmp_path / "t.db")
    item = NeedsYou("cutshort", "voice recording", "Backend Engineer", "Acme", "https://cutshort.io/profile/view/j/1")
    db.add_needs_you(item)
    db.add_needs_you(item)  # the same thing twice is listed once
    db.add_needs_you(NeedsYou("instahyre", 'reply to Recruiter: "are you available Monday?"'))
    items = {i.what: i for i in db.needs_you()}
    assert set(items) == {"voice recording", 'reply to Recruiter: "are you available Monday?"'}
    assert items["voice recording"].company == "Acme" and items["voice recording"].url.endswith("/j/1")
    db.close()
