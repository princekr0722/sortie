from datetime import datetime, timezone

from sortie.models import QuestionKind
from sortie.sites.cutshort_inbox import conversation_job, is_recent, pending_questionnaire, submitted


def _q(title, rtype="mcq", options=(), mandatory=True):
    return {"question": {"title": title, "responseType": rtype, "isMandatory": mandatory,
                         "options": [{"_id": str(i), "label": o} for i, o in enumerate(options)]},
            "responseStringArray": [], "responseNumberArray": []}


THREAD = {"thread": {"messages": [{"message": "[Questionnaire]", "screeningSubmitted": False, "questions": [
    _q("The location of this job will be Jaipur. Are you okay with this?",
       options=["I am currently in this location and okay with it", "I am not in this location but can relocate",
                "I am not okay with this location"]),
    _q("What is your official notice period (as per your offer letter)?",
       options=["Served notice, available immediately", "15 days", "30 days", "45 days", "60 days", "Above 60 days"]),
    _q("Record a short intro", rtype="video", mandatory=False),
]}]}}


def test_pending_questionnaire():
    qs = pending_questionnaire(THREAD)
    assert [q.response_type for q in qs] == ["mcq", "mcq", "video"]
    assert qs[0].to_question().kind == QuestionKind.CHOICE and len(qs[1].options) == 6
    assert not qs[2].supported and not qs[2].mandatory
    assert not submitted(THREAD)
    done = {"thread": {"messages": [{**THREAD["thread"]["messages"][0], "screeningSubmitted": True}]}}
    assert pending_questionnaire(done) == [] and submitted(done)


def test_conversation_job_and_age():
    c = {"lastMsgDate": "2026-11-10T10:18:50.823Z", "companyDetails": {"name": "Acme"},
         "jobSignal": {"_id": "j1", "headline": "Java Developer", "publicUrl": "https://cutshort.io/job/x",
                       "salaryRangeText": "₹7L - ₹15L / yr", "allSkills": ["Java"]}}
    job = conversation_job(c)
    assert (job.title, job.company, job.salary) == ("Java Developer", "Acme", "₹7L - ₹15L / yr")
    assert is_recent(c, now=datetime(2026, 11, 20, tzinfo=timezone.utc))
    assert not is_recent(c, now=datetime(2026, 12, 20, tzinfo=timezone.utc))


def test_text_questions_stay_text():
    from sortie.sites.cutshort_inbox import ScreeningQuestion
    q = ScreeningQuestion("What is your current annual CTC and expected CTC?", "text").to_question()
    assert q.kind == QuestionKind.TEXT
