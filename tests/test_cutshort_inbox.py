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


async def test_scan_pages_until_older_than_14_days():
    from datetime import datetime, timedelta, timezone
    from sortie.sites.cutshort_inbox import CutshortInbox

    def convo(i, days_ago):
        when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
        return {"_id": f"c{i}", "lastMsgText": "Hi, are you free tomorrow?", "lastMsgDate": when,
                "lastMsgSender": {"_id": "recruiter", "name": "R"}, "jobSignal": {"headline": "Dev"},
                "companyDetails": {"name": "Acme"}}

    pages = {1: [convo(i, 1) for i in range(8)], 2: [convo(8 + i, d) for i, d in enumerate([2, 3, 20, 25])],
             3: [convo(20, 30)]}
    asked = []

    class Response:
        def __init__(self, body): self.body, self.ok, self.status = body, True, 200
        async def json(self): return self.body

    class Request:
        async def get(self, url):
            if "auth/user" in url:
                return Response({"_id": "me"})
            n = int(url.rsplit("page=", 1)[1]); asked.append(n)
            return Response({"results": pages.get(n, []), "totalPages": 3})

    class Page:
        request = Request()

    inbox = CutshortInbox(Page(), [])
    await inbox.scan()
    assert asked == [1, 2]  # page 2 reached conversations older than 14 days, so page 3 is never read
    assert len(inbox.needs_you) == 10  # the 10 recent ones (8 + 2), each left for you


async def test_follow_up_checks_each_tracked_application():
    from sortie.models import Followup
    from sortie.sites.cutshort_inbox import CutshortInbox

    def thread(messages):
        return {"thread": {"messages": messages}}

    threads = {
        "c-done": thread([{"questions": [_q("Notice?", options=["30 days"])], "screeningSubmitted": True}]),
        "c-msg": thread([{"message": "Are you free for a call tomorrow?", "sender": {"_id": "rec", "name": "Asha"}}]),
        "c-q": thread([{"questions": [_q("Notice?", options=["30 days"])], "screeningSubmitted": False,
                        "sender": {"_id": "rec", "name": "Ravi"}}]),
    }
    listing = {"results": [{"_id": "c-q", "jobSignal": {"_id": "j3"}}], "totalPages": 1}

    class Response:
        def __init__(self, body): self.body, self.ok, self.status = body, True, 200
        async def json(self): return self.body

    class Request:
        async def get(self, url):
            if "auth/user" in url:
                return Response({"_id": "me"})
            if "loadthread-v2/" in url:
                return Response(threads[url.rsplit("/", 1)[1]])
            return Response(listing if url.endswith("page=1") else {"results": []})

    class Page:
        request = Request()

    inbox = CutshortInbox(Page(), [])
    answered = []

    async def fake_answer(qn, answerer, confirm, debug_dump):
        answered.append(qn.conversation_id)
        return "sent"
    inbox._answer = fake_answer
    followups = [Followup("cutshort", "j1", "Dev", "Acme", "c-done"), Followup("cutshort", "j2", "Dev", "Initech", "c-msg"),
                 Followup("cutshort", "j3", "Dev", "Globex", ""),  # conversation id found through the listing
                 Followup("cutshort", "j4", "Dev", "Umbrella", "")]  # no conversation yet
    updates = []
    await inbox.follow_up(followups, updates, answerer=None, confirm=None, debug_dump=None)
    assert answered == ["c-q"]
    assert sorted(updates) == [("j1", "answered", "questionnaire already submitted", "c-done"),
                               ("j2", "needs_you", "recruiter message left for you", "c-msg"),
                               ("j3", "answered", "questionnaire sent", "c-q")]
    assert [n.what for n in inbox.needs_you] == ['reply to Asha: "Are you free for a call tomorrow?"']
