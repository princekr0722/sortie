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
    questions = [to_question(q) for q in json.loads(FIXTURE.read_text())["questions"]]
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
    assert inbox_url().endswith("candidate_conversation?limit=10&offset=0")
