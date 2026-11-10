"""Instahyre inbox: answer recruiter questionnaires, leave real conversations to the user.

Looks at the latest conversations; one is pending when its latest message isn't yours ("You: ..." in the
preview). Conversations are classified from the inbox list's preview text, which doesn't mark anything read;
only questionnaire messages are opened (to get the link). Everything else is left alone and just logged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from sortie.llm.answerer import Answerer, question_kind
from sortie.log import log
from sortie.models import Answer, Job, NeedsYou, Question, QuestionKind
from sortie.sites.base import ConfirmSubmit

BASE = "https://www.instahyre.com"
RECENT_CONVERSATIONS = 10

# previews are cut off mid-sentence; a match is confirmed by finding the questionnaire link
QUESTIONNAIRE_PREVIEW = re.compile(r"questionnaire|thanks for showing interest in working with|as part of the applica", re.I)
QUESTIONNAIRE_LINK = re.compile(r"instahyre\.com/questionnaire/(\d+)/(\d+)")
SUBMITTED_TEXT = re.compile(r"has been sent|submitted|thank you|already (been )?(answered|submitted|filled)", re.I)

SELECTORS = {
    # questionnaire page (verified 2026-11-05)
    "question_block": "div[ng-repeat='question in questionnaire.questions']",
    "question_text": ".question-label span",
    "text_field": ".answer-container textarea, .answer-container input[type='text'], .answer-container input[type='number']",
    "choice": ".answer-container .choice-options input[type='radio'], .answer-container .choice-options input[type='checkbox']",
    "dropdown": ".answer-container select",
    "submit": "button[ng-click='submitQuestionnaire()']",
}


@dataclass
class Questionnaire:
    questionnaire_id: str
    opportunity_id: str
    job: Job  # the job it belongs to; used for answering and the review prompt

    @property
    def url(self) -> str:
        return f"{BASE}/questionnaire/{self.questionnaire_id}/{self.opportunity_id}"


def inbox_url(limit: int = RECENT_CONVERSATIONS, offset: int = 0) -> str:
    return f"{BASE}/api/v1/inbox_page/candidate_conversation?limit={limit}&offset={offset}"


def awaiting_reply(conversation: dict) -> bool:
    """The latest message isn't yours; the inbox preview prefixes your own messages with "You:"."""
    return not (conversation.get("latest_message") or "").lstrip().startswith("You:")


def is_questionnaire_preview(conversation: dict) -> bool:
    return bool(QUESTIONNAIRE_PREVIEW.search(conversation.get("latest_message") or ""))


def questionnaire_link(messages: list[dict]) -> tuple[str, str] | None:
    """(questionnaire id, opportunity id) from the newest message that links a questionnaire."""
    for message in reversed(messages):
        if match := QUESTIONNAIRE_LINK.search(message.get("content_html") or ""):
            return match.group(1), match.group(2)
    return None


INBOX_PAGE = f"{BASE}/candidate/inbox/"  # for links in the needs-you list (opened by you, not the bot)


def conversation_job(conversation: dict) -> Job:
    """Inbox titles look like "Acme Labs - Java Developer"."""
    company, _, title = (conversation.get("job_title") or "").partition(" - ")
    return Job(site="instahyre", site_job_id=str(conversation.get("job_id") or ""),
               url=f"{BASE}/job-{conversation.get('job_id')}/", title=title or company, company=company)


def to_question(api_question: dict) -> Question:
    options = [o["label"] for o in api_question.get("choice_options") or []]
    text = (api_question.get("text") or "").strip()
    return Question(text, question_kind(text, options, multi=False), options)


class InstahyreInbox:
    def __init__(self, page: Page, needs_you: list[NeedsYou] | None = None):
        self.page = page
        self.questionnaires: list[Questionnaire] = []
        self.needs_you = needs_you if needs_you is not None else []  # the adapter's list, saved by the runner

    async def scan(self) -> None:
        """Check the latest conversations you haven't replied to: queue questionnaires, log the rest."""
        response = await self.page.request.get(inbox_url())
        if not response.ok:
            log.warning(f"instahyre inbox failed ({response.status})")
            return
        pending = [c for c in (await response.json()).get("objects", []) if awaiting_reply(c)]
        log.info(f"  inbox: {len(pending)} of the last {RECENT_CONVERSATIONS} conversations await your reply",
                 extra={"detail": True})

        for conversation in pending:
            who = f"{conversation.get('conv_display_name', '?')} ({conversation.get('job_title', '')})"
            if is_questionnaire_preview(conversation):
                link = await self._find_link(conversation)
                if link:
                    self.questionnaires.append(Questionnaire(*link, conversation_job(conversation)))
                    log.info(f"  📝 questionnaire from {who}: will answer after applying", extra={"detail": True})
                    continue
            preview = " ".join((conversation.get("latest_message") or "").split())[:120]
            unread = "" if conversation.get("is_latest_msg_read") else ", unread"
            log.info(f"  💬 message from {who}{unread}: \"{preview}\" (left for you to reply)", extra={"detail": True})
            job = conversation_job(conversation)
            self.needs_you.append(NeedsYou("instahyre", f"reply to {conversation.get('conv_display_name', 'recruiter')}: "
                                                         f"\"{preview[:80]}\"", job.title, job.company, INBOX_PAGE))

    async def _find_link(self, conversation: dict) -> tuple[str, str] | None:
        response = await self.page.request.get(f"{BASE}/api/v1/resume_modal/emails/message?conv_id={conversation['id']}")
        if not response.ok:
            return None
        return questionnaire_link((await response.json()).get("objects", []))

    async def answer_all(self, answerer: Answerer, confirm: ConfirmSubmit, debug_dump) -> None:
        for questionnaire in self.questionnaires:
            try:
                await self._answer(questionnaire, answerer, confirm, debug_dump)
            except PlaywrightError as e:
                log.warning(f"  questionnaire for {questionnaire.job.company} failed: {str(e).splitlines()[0]}")
                await debug_dump("questionnaire-error")

    async def _answer(self, q: Questionnaire, answerer: Answerer, confirm: ConfirmSubmit, debug_dump) -> None:
        label = f"{q.job.company} — {q.job.title}"
        api = await self.page.request.get(f"{BASE}/api/v1/questionnaires/candidate_questionnaire/{q.questionnaire_id}")
        api_questions = {(x.get("text") or "").strip(): x for x in (await api.json()).get("questions", [])}

        await self.page.goto(q.url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(2000)
        if "/questionnaire/" not in self.page.url:  # closed questionnaires redirect to the inbox
            log.info(f"  📝 questionnaire for {label}: no longer available", extra={"detail": True})
            return
        blocks = self.page.locator(SELECTORS["question_block"])
        try:
            await blocks.first.wait_for(timeout=15000)
        except PlaywrightError:
            body = await self.page.locator("body").inner_text()
            if SUBMITTED_TEXT.search(body):
                log.info(f"  📝 questionnaire for {label}: already submitted", extra={"detail": True})
            else:
                log.warning(f"  📝 questionnaire for {label}: form didn't load ({q.url})")
                await debug_dump("questionnaire-empty")
            return

        answers: dict[str, Answer] = {}
        for i in range(await blocks.count()):
            block = blocks.nth(i)
            text = (await block.locator(SELECTORS["question_text"]).first.inner_text()).strip()
            question = to_question(api_questions.get(text, {"text": text}))
            answer = await answerer.answer(question, q.job)
            if answer is None:
                log.warning(f"  📝 questionnaire for {label}: skipped, couldn't answer \"{text}\" ({q.url})")
                return
            if not await self._fill(block, question, answer):
                log.warning(f"  📝 questionnaire for {label}: skipped, unsupported field for \"{text}\" ({q.url})")
                await debug_dump("questionnaire-field")
                return
            answers[text] = answer
            rejecting = [o for o in api_questions.get(text, {}).get("choice_options") or []
                         if o.get("reject_action") and o.get("label") == answer.value]
            if rejecting:
                log.warning(f"  ⚠ \"{text}\" → {answer.value}: the recruiter marked this answer as a rejection")

        if not await confirm(q.job, answers):
            log.info(f"  📝 questionnaire for {label}: not submitted", extra={"detail": True})
            return
        submit = self.page.locator(SELECTORS["submit"])
        await submit.first.click()
        await self.page.wait_for_timeout(3000)
        still_open = await submit.count() and await submit.first.is_visible()
        if still_open and not SUBMITTED_TEXT.search(await self.page.locator("body").inner_text()):
            log.warning(f"  📝 questionnaire for {label}: Submit didn't go through (a required answer may be invalid); "
                        f"check {q.url}")
            await debug_dump("questionnaire-not-submitted")
            return
        log.info(f"  📝 questionnaire for {label}: submitted ({len(answers)} answers)", extra={"status": "applied"})
        for text, answer in answers.items():
            log.info(f"        Q: {text} → {answer.value} ({answer.source})", extra={"detail": True})

    async def _fill(self, block, question: Question, answer: Answer) -> bool:
        if question.kind in (QuestionKind.CHOICE, QuestionKind.MULTI_CHOICE):
            values = answer.value if isinstance(answer.value, list) else [answer.value]
            for value in values:
                await block.locator(".choice-options label", has_text=re.compile(rf"^\s*{re.escape(value)}\s*$")).first.click()
            return True
        dropdown = block.locator(SELECTORS["dropdown"])
        if await dropdown.count():
            await dropdown.first.select_option(label=str(answer.value))
            return True
        field = block.locator(SELECTORS["text_field"])
        if await field.count():
            await field.first.fill(str(answer.value))  # fill fires input events, so Angular's ng-model updates
            return True
        return False  # rating scale, video, file upload, ...

