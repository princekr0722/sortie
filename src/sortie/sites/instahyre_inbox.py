"""Instahyre inbox: answer recruiter questionnaires, leave real conversations to the user.

Looks at the latest conversations; one is pending when its latest message isn't yours ("You: ..." in the
preview). Conversations are classified from the inbox list's preview text, which doesn't mark anything read;
only questionnaire messages are opened (to get the link). Everything else is left alone and just logged.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from sortie.llm.answerer import Answerer, question_kind
from sortie.log import log
from sortie.models import Answer, Followup, Job, NeedsYou, Question, QuestionKind
from sortie.sites.base import ConfirmSubmit

BASE = "https://www.instahyre.com"
# every conversation awaiting your reply from the last MAX_AGE_DAYS, however many there are (the first version
# looked at only the last 10). SAFETY_PAGES only guards against a paging loop that never ends.
PAGE_SIZE = 20
MAX_EXTRA_PAGES = 10  # paging on, past the window, to find a tracked application's conversation
MAX_AGE_DAYS = 14
SAFETY_PAGES = 200
UNDATED_PAGES = 10  # if conversations carry no readable date, the 14-day window can't apply: read this many pages
DATE_FIELDS = ("latest_message_time", "last_message_time", "modified", "updated_at", "last_modified", "created")

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


def has_date(conversation: dict) -> bool:
    return any(conversation.get(name) for name in DATE_FIELDS)


def conversation_age_ok(conversation: dict, now: datetime | None = None) -> bool:
    """Within MAX_AGE_DAYS by whichever date field the conversation has; True when it has none (the cap still
    bounds the scan)."""
    for name in DATE_FIELDS:
        if value := conversation.get(name):
            try:
                when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return (now or datetime.now(timezone.utc)) - when <= timedelta(days=MAX_AGE_DAYS)
    return True


def inbox_url(limit: int = PAGE_SIZE, offset: int = 0) -> str:
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
        self.done: set[str] = set()  # questionnaire ids finished in earlier runs: never opened again
        self.newly_done: list[tuple[str, str]] = []  # (questionnaire id, outcome) finished now; saved by the runner

    async def follow_up(self, followups: list[Followup], updates: list, answerer: Answerer,
                        confirm: ConfirmSubmit, debug_dump) -> None:
        """Scan the inbox until every tracked application's conversation has been seen (or the window is passed),
        answer questionnaires, leave other messages for you, and report each tracked application's outcome."""
        tracked = {f.site_job_id: f for f in followups}
        seen = await self.scan(set(tracked))
        outcomes = await self.answer_all(answerer, confirm, debug_dump)
        for job_id, f in tracked.items():
            if job_id in outcomes:
                status = {"sent": "answered", "already": "answered", "closed": "none",
                          "needs_you": "needs_you"}.get(outcomes[job_id], "open")
                if status != "open":
                    updates.append((job_id, status, f"questionnaire {outcomes[job_id]}", ""))
            elif seen.get(job_id) == "message":
                updates.append((job_id, "needs_you", "recruiter message left for you", ""))
            elif seen.get(job_id) == "done":
                updates.append((job_id, "answered", "questionnaire finished earlier", ""))

    async def scan(self, tracked_jobs: set[str] = frozenset()) -> dict[str, str]:
        """Conversations you haven't replied to: queue questionnaires, leave the rest for you. Pages on past the
        usual window while a tracked application's conversation hasn't been seen yet. Returns job id ->
        "questionnaire" / "message" for the conversations awaiting you."""
        self.questionnaires.clear()
        recent: list[dict] = []
        unseen = set(tracked_jobs)
        extra_pages = 0
        for page_number in range(SAFETY_PAGES):
            if page_number == SAFETY_PAGES - 1:
                log.warning(f"  instahyre inbox: stopped at the {SAFETY_PAGES}-page safety limit")
            response = await self.page.request.get(inbox_url(PAGE_SIZE, page_number * PAGE_SIZE))
            if not response.ok:
                log.warning(f"instahyre inbox failed ({response.status})")
                break
            data = await response.json()
            page_items = data.get("objects", [])
            recent += [c for c in page_items if conversation_age_ok(c)]
            unseen -= {str(c.get("job_id") or "") for c in page_items}
            more = bool((data.get("meta") or {}).get("next"))
            past_window = not page_items or not conversation_age_ok(page_items[-1])
            if page_items and not has_date(page_items[-1]) and page_number + 1 >= UNDATED_PAGES:
                log.warning(f"  instahyre inbox: conversations have no readable date; read the newest "
                            f"{UNDATED_PAGES * PAGE_SIZE} instead of the last {MAX_AGE_DAYS} days")
                past_window = True
            extra_pages += past_window
            if not more or (past_window and (not unseen or extra_pages > MAX_EXTRA_PAGES)):
                break  # newest first: past the window (and every tracked conversation seen), or no more pages
        pending = [c for c in recent if awaiting_reply(c)]
        seen: dict[str, str] = {}
        finished = 0
        log.info(f"  inbox: {len(pending)} conversations from the last {MAX_AGE_DAYS} days await your reply",
                 extra={"detail": True})

        for conversation in pending:
            who = f"{conversation.get('conv_display_name', '?')} ({conversation.get('job_title', '')})"
            if is_questionnaire_preview(conversation):
                link = await self._find_link(conversation)
                if link and link[0] in self.done:
                    finished += 1  # sent / submitted / closed earlier: the chat still shows the recruiter last
                    seen[str(conversation.get("job_id") or "")] = "done"
                    continue
                if link:
                    self.questionnaires.append(Questionnaire(*link, conversation_job(conversation)))
                    seen[str(conversation.get("job_id") or "")] = "questionnaire"
                    log.info(f"  📝 questionnaire from {who}: found", extra={"detail": True})
                    continue
            preview = " ".join((conversation.get("latest_message") or "").split())[:120]
            unread = "" if conversation.get("is_latest_msg_read") else ", unread"
            log.info(f"  💬 message from {who}{unread}: \"{preview}\" (left for you to reply)", extra={"detail": True})
            job = conversation_job(conversation)
            self.needs_you.append(NeedsYou("instahyre", f"reply to {conversation.get('conv_display_name', 'recruiter')}: "
                                                         f"\"{preview[:80]}\"", job.title, job.company, INBOX_PAGE))
            seen[str(conversation.get("job_id") or "")] = "message"
        if finished:
            log.info(f"  ({finished} questionnaires in those conversations were finished in earlier runs)",
                     extra={"detail": True})
        return seen

    async def _find_link(self, conversation: dict) -> tuple[str, str] | None:
        response = await self.page.request.get(f"{BASE}/api/v1/resume_modal/emails/message?conv_id={conversation['id']}")
        if not response.ok:
            return None
        return questionnaire_link((await response.json()).get("objects", []))

    async def answer_all(self, answerer: Answerer, confirm: ConfirmSubmit, debug_dump) -> dict[str, str]:
        """job id -> outcome ("sent", "already", "closed", "not_sent", "failed") for each queued questionnaire."""
        outcomes: dict[str, str] = {}
        for questionnaire in self.questionnaires:
            try:
                outcome = await self._answer(questionnaire, answerer, confirm, debug_dump)
            except PlaywrightError as e:
                log.warning(f"  questionnaire for {questionnaire.job.company} failed: {str(e).splitlines()[0]}")
                await debug_dump("questionnaire-error")
                outcome = "failed"
            outcomes[questionnaire.job.site_job_id] = outcome
            if outcome in ("sent", "already", "closed"):
                self.newly_done.append((questionnaire.questionnaire_id, outcome))
        return outcomes

    async def _answer(self, q: Questionnaire, answerer: Answerer, confirm: ConfirmSubmit, debug_dump) -> str:
        label = f"{q.job.company} — {q.job.title}"
        api = await self.page.request.get(f"{BASE}/api/v1/questionnaires/candidate_questionnaire/{q.questionnaire_id}")
        api_questions = {(x.get("text") or "").strip(): x for x in (await api.json()).get("questions", [])}

        await self.page.goto(q.url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(2000)
        if "/questionnaire/" not in self.page.url:  # closed questionnaires redirect to the inbox
            log.info(f"  📝 questionnaire for {label}: no longer available", extra={"detail": True})
            return "closed"
        blocks = self.page.locator(SELECTORS["question_block"])
        try:
            await blocks.first.wait_for(timeout=15000)
        except PlaywrightError:
            body = await self.page.locator("body").inner_text()
            if SUBMITTED_TEXT.search(body):
                log.info(f"  📝 questionnaire for {label}: already submitted", extra={"detail": True})
                return "already"
            else:
                log.warning(f"  📝 questionnaire for {label}: form didn't load ({q.url})")
                await debug_dump("questionnaire-empty")
            return "failed"

        answers: dict[str, Answer] = {}
        for i in range(await blocks.count()):
            block = blocks.nth(i)
            text = (await block.locator(SELECTORS["question_text"]).first.inner_text()).strip()
            question = to_question(api_questions.get(text, {"text": text}))
            answer = await answerer.answer(question, q.job)
            if answer is None:
                log.warning(f"  📝 questionnaire for {label}: skipped, couldn't answer \"{text}\" ({q.url})")
                self.needs_you.append(NeedsYou("instahyre", f"questionnaire: \"{text[:60]}\"", q.job.title,
                                               q.job.company, q.url))
                return "needs_you"
            if not await self._fill(block, question, answer):
                log.warning(f"  📝 questionnaire for {label}: skipped, unsupported field for \"{text}\" ({q.url})")
                await debug_dump("questionnaire-field")
                self.needs_you.append(NeedsYou("instahyre", f"questionnaire field: \"{text[:60]}\"", q.job.title,
                                               q.job.company, q.url))
                return "needs_you"
            answers[text] = answer
            rejecting = [o for o in api_questions.get(text, {}).get("choice_options") or []
                         if o.get("reject_action") and o.get("label") == answer.value]
            if rejecting:
                log.warning(f"  ⚠ \"{text}\" → {answer.value}: the recruiter marked this answer as a rejection")

        if not await confirm(q.job, answers):
            log.info(f"  📝 questionnaire for {label}: not submitted", extra={"detail": True})
            return "not_sent"
        submit = self.page.locator(SELECTORS["submit"])
        await submit.first.click()
        await self.page.wait_for_timeout(3000)
        still_open = await submit.count() and await submit.first.is_visible()
        if still_open and not SUBMITTED_TEXT.search(await self.page.locator("body").inner_text()):
            log.warning(f"  📝 questionnaire for {label}: Submit didn't go through (a required answer may be invalid); "
                        f"check {q.url}")
            await debug_dump("questionnaire-not-submitted")
            return "failed"
        log.info(f"  📝 questionnaire for {label}: submitted ({len(answers)} answers)", extra={"status": "applied"})
        for text, answer in answers.items():
            log.info(f"        Q: {text} → {answer.value} ({answer.source})", extra={"detail": True})
        return "sent"

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

