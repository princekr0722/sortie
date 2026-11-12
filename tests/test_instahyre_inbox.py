import json
from pathlib import Path

from sortie.models import QuestionKind
from sortie.sites.instahyre_inbox import (Questionnaire, conversation_job, is_questionnaire_preview,
                                          questionnaire_link, to_question)

FIXTURE = Path(__file__).parent / "fixtures" / "instahyre_questionnaire.json"


def test_classifies_conversations_from_preview():
    assert is_questionnaire_preview({"latest_message": "Asha: Hi, Thanks for showing interest in working with "
                                                       "Acme. As part of the application proce"})
    assert is_questionnaire_preview({"latest_message": "Please fill the questionnaire"})
    assert not is_questionnaire_preview({"latest_message": "Asha: Can we talk tomorrow at 5?"})
    assert not is_questionnaire_preview({"latest_message": "You: done"})


def test_questionnaire_link_uses_newest_message():
    messages = [{"content_html": '<a href="https://www.instahyre.com/questionnaire/1/2">open</a>'},
                {"content_html": "<p>reminder</p>"},
                {"content_html": '<a href="https://www.instahyre.com/questionnaire/3/4">open</a>'}]
    assert questionnaire_link(messages) == ("3", "4")
    assert questionnaire_link([{"content_html": "hello"}]) is None


def test_conversation_job_and_url():
    job = conversation_job({"job_title": "Acme Labs - Java Developer", "job_id": 42})
    assert (job.company, job.title, job.site_job_id) == ("Acme Labs", "Java Developer", "42")
    assert Questionnaire("9", "8", job).url == "https://www.instahyre.com/questionnaire/9/8"


def test_question_types_from_saved_questionnaire():
    questions = [to_question(q) for q in json.loads(FIXTURE.read_text(encoding="utf-8"))["questions"]]
    by_text = {q.text: q for q in questions}
    assert by_text["How many years of work experience do you have with Java?"].kind == QuestionKind.NUMBER
    notice = by_text["Are you serving notice period?"]
    assert notice.kind == QuestionKind.CHOICE and notice.options == ["Yes", "No", "Completed"]
    assert by_text["Mention your current CTC."].kind == QuestionKind.DECIMAL


def test_awaiting_reply():
    from sortie.sites.instahyre_inbox import awaiting_reply, inbox_url
    assert awaiting_reply({"latest_message": "Asha: Hi, Thanks for showing interest"})
    assert not awaiting_reply({"latest_message": "You: done"})
    assert not awaiting_reply({"latest_message": "  You: I've filled the form."})
    assert inbox_url().endswith("candidate_conversation?limit=20&offset=0")
    assert inbox_url(20, 40).endswith("limit=20&offset=40")


def test_conversation_age():
    from datetime import date
    from sortie.sites.instahyre_inbox import conversation_age_ok, message_date
    today = date(2026, 11, 12)
    assert message_date("Nov 05", today) == date(2026, 11, 5)
    assert message_date("Dec 31, 2025", today) == date(2025, 12, 31)
    assert message_date("Dec 20", today) == date(2025, 12, 20)  # no year and in the future: last year
    assert message_date("10:45 AM", today) == today and message_date("Yesterday", today) == date(2026, 11, 11)
    assert conversation_age_ok({"latest_msg_at": "Nov 02"}, today)
    assert not conversation_age_ok({"latest_msg_at": "Oct 20"}, today)
    assert conversation_age_ok({"latest_msg_at": "sometime"}, today)  # unreadable: kept, never skipped blindly


async def test_finished_questionnaires_are_not_reopened():
    from sortie.sites.instahyre_inbox import InstahyreInbox
    convo = {"id": 7, "job_id": 42, "latest_message": "Thanks for showing interest in working with Acme. As part of",
             "conv_display_name": "Asha", "job_title": "Acme - Java Developer"}

    class Response:
        def __init__(self, body): self.body, self.ok, self.status = body, True, 200
        async def json(self): return self.body

    class Request:
        async def get(self, url):
            if "inbox_page" in url:
                return Response({"objects": [convo], "meta": {"next": None}})
            return Response({"objects": [{"content_html": "https://www.instahyre.com/questionnaire/111/222"}]})

    class Page:
        request = Request()

    inbox = InstahyreInbox(Page(), [])
    inbox.done = {"111"}
    seen = await inbox.scan({"42"})
    assert inbox.questionnaires == [] and seen == {"42": "done"}
    inbox.done = set()
    await inbox.scan()
    assert [q.questionnaire_id for q in inbox.questionnaires] == ["111"]
