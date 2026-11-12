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


def test_followups_lifecycle(tmp_path):
    from sortie.db import Database
    from sortie.models import Job, Status
    db = Database(tmp_path / "t.db")
    db.record(Job("cutshort", "j1", "u", "Dev", "Acme"), Status.APPLIED)
    db.record(Job("cutshort", "j2", "u", "Dev", "Initech"), Status.SKIPPED)
    assert db.backfill_followups("cutshort") == 1  # only the applied job
    assert db.backfill_followups("cutshort") == 0  # once
    db.open_followup("cutshort", "j1", conversation_id="c1")  # fills the missing conversation id
    [f] = db.open_followups("cutshort")
    assert (f.site_job_id, f.conversation_id, f.company) == ("j1", "c1", "Acme")
    db.update_followup("cutshort", "j1", "answered", "questionnaire sent")
    assert db.open_followups("cutshort") == [] and db.followup_counts("cutshort") == {"answered": 1}
    db.open_followup("cutshort", "j3")
    db.conn.execute("UPDATE followups SET created_at = '2026-01-01T00:00:00' WHERE site_job_id = 'j3'")
    assert db.expire_followups("cutshort") == 1 and db.followup_counts("cutshort")["none"] == 1
    db.close()


def test_done_questionnaires(tmp_path):
    from sortie.db import Database
    db = Database(tmp_path / "t.db")
    db.mark_questionnaire_done("instahyre", "q1", "sent")
    db.mark_questionnaire_done("instahyre", "q1", "already")  # once
    assert db.done_questionnaires("instahyre") == {"q1"} and db.done_questionnaires("cutshort") == set()
    db.close()
