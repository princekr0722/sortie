"""CutShort conversations: answer the screening questionnaire recruiters send after you apply, and leave every
other message for you (needs-you list). Reads through CutShort's JSON endpoints (no read receipts); only a thread
being answered is opened in the page. Verified 2026-11-10, see doc/features/site-cutshort.md."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from sortie.llm.answerer import Answerer, question_kind
from sortie.log import log
from sortie.models import Job, NeedsYou, Question, QuestionKind
from sortie.sites.base import ConfirmSubmit

BASE = "https://cutshort.io"
CONVERSATIONS = f"{BASE}/conversations-v2/candidate?page=1&user_role=candidate&convo_status=awaiting"
RECENT_CONVERSATIONS = 15
MAX_AGE_DAYS = 14
QUESTIONNAIRE = "[Questionnaire]"  # lastMsgText of a screening message
SUPPORTED = {"mcq", "text", "number", "multi", "mcq_multi"}  # anything else (audio, video, file) needs you


def thread_url(conversation_id: str) -> str:
    return f"{BASE}/profile/candidate-conversations?i=all&stage=awaiting&thread={conversation_id}"


@dataclass
class ScreeningQuestion:
    title: str
    response_type: str
    options: list[str] = field(default_factory=list)
    mandatory: bool = True

    @property
    def supported(self) -> bool:
        return self.response_type in SUPPORTED

    def to_question(self) -> Question:
        if self.options:
            multi = "multi" in self.response_type
            return Question(self.title, QuestionKind.MULTI_CHOICE if multi else QuestionKind.CHOICE, self.options)
        if self.response_type == "number":
            return Question(self.title, QuestionKind.NUMBER)
        if self.response_type == "text":  # a free-text box: never guess a number type from the wording ("CTC")
            return Question(self.title, QuestionKind.TEXT)
        return Question(self.title, question_kind(self.title, [], multi=False))


@dataclass
class Questionnaire:
    conversation_id: str
    job: Job
    recruiter: str
    questions: list[ScreeningQuestion]


def conversation_job(conversation: dict) -> Job:
    signal = conversation.get("jobSignal") or {}
    company = (conversation.get("companyDetails") or {}).get("name") or (signal.get("companyDetails") or {}).get("name")
    return Job(site="cutshort", site_job_id=str(signal.get("_id") or ""), url=signal.get("publicUrl") or "",
               title=signal.get("headline") or "", company=company or "", salary=signal.get("salaryRangeText") or "",
               locations=[signal["locationsText"]] if signal.get("locationsText") else [],
               description="Key skills: " + ", ".join(s for s in signal.get("allSkills") or [] if isinstance(s, str)))


def is_recent(conversation: dict, now: datetime | None = None) -> bool:
    try:
        when = datetime.fromisoformat((conversation.get("lastMsgDate") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    return (now or datetime.now(timezone.utc)) - when <= timedelta(days=MAX_AGE_DAYS)


def pending_questionnaire(thread: dict) -> list[ScreeningQuestion]:
    """Questions of the latest unsubmitted screening message in a thread (loadthread-v2), else []."""
    for message in reversed((thread.get("thread") or {}).get("messages") or []):
        if message.get("questions") and not message.get("screeningSubmitted"):
            return [ScreeningQuestion(q["question"].get("title", "").strip(), q["question"].get("responseType", ""),
                                      [o.get("label", "") for o in q["question"].get("options") or []],
                                      bool(q["question"].get("isMandatory", True)))
                    for q in message["questions"] if q.get("question")]
    return []


def submitted(thread: dict) -> bool:
    messages = [m for m in (thread.get("thread") or {}).get("messages") or [] if m.get("questions")]
    return bool(messages) and all(m.get("screeningSubmitted") for m in messages)


class ServerDown(Exception):
    """CutShort answered with a 5xx page (e.g. Cloudflare's 504 "Gateway time-out")."""


class CutshortInbox:
    def __init__(self, page: Page, needs_you: list[NeedsYou]):
        self.page = page
        self.needs_you = needs_you
        self.questionnaires: list[Questionnaire] = []
        self.me = ""

    async def scan(self) -> None:
        """Conversations awaiting your reply: queue unsubmitted questionnaires, leave everything else for you."""
        self.questionnaires.clear()
        if not self.me:
            user = await self.page.request.get(f"{BASE}/api/auth/user")
            self.me = (await user.json()).get("_id", "") if user.ok else ""
        response = await self.page.request.get(CONVERSATIONS)
        if not response.ok:
            log.warning(f"cutshort inbox unavailable ({response.status})")
            return
        conversations = [c for c in (await response.json()).get("results", [])[:RECENT_CONVERSATIONS]
                         if is_recent(c) and (c.get("lastMsgSender") or {}).get("_id") != self.me]
        log.info(f"  inbox: {len(conversations)} recent conversations await your reply", extra={"detail": True})
        for conversation in conversations:
            job = conversation_job(conversation)
            recruiter = (conversation.get("lastMsgSender") or {}).get("name") or "recruiter"
            if conversation.get("lastMsgText") == QUESTIONNAIRE:
                thread = await self.page.request.get(f"{BASE}/loadthread-v2/{conversation['_id']}")
                questions = pending_questionnaire(await thread.json()) if thread.ok else []
                if questions:
                    self.questionnaires.append(Questionnaire(conversation["_id"], job, recruiter, questions))
                    log.info(f"  📝 questionnaire from {recruiter} ({job.title} @ {job.company}): "
                             f"{len(questions)} questions pending", extra={"detail": True})
                continue
            preview = " ".join((conversation.get("lastMsgText") or "").split())[:120]
            log.info(f"  💬 message from {recruiter} ({job.title} @ {job.company}): \"{preview}\" (left for you)",
                     extra={"detail": True})
            self.needs_you.append(NeedsYou("cutshort", f"reply to {recruiter}: \"{preview[:80]}\"", job.title,
                                           job.company, thread_url(conversation["_id"])))

    async def answer_all(self, answerer: Answerer, confirm: ConfirmSubmit, debug_dump) -> None:
        for questionnaire in self.questionnaires:
            try:
                await self._answer(questionnaire, answerer, confirm, debug_dump)
            except ServerDown as e:
                log.warning(f"  CutShort is having server trouble ({e}); the remaining questionnaires stay open "
                            f"for the next run")
                return
            except PlaywrightError as e:
                log.warning(f"  questionnaire for {questionnaire.job.title} failed: {str(e).splitlines()[0][:120]}")
                await debug_dump("questionnaire-error")

    async def _answer(self, qn: Questionnaire, answerer: Answerer, confirm: ConfirmSubmit, debug_dump) -> None:
        job, label = qn.job, f"{qn.job.title} @ {qn.job.company}"
        blocking = [q for q in qn.questions if not q.supported and q.mandatory]
        if blocking:
            what = f"questionnaire with a {blocking[0].response_type} question"
            log.info(f"  🎙  {label}: {what}; left for you", extra={"detail": True})
            self.needs_you.append(NeedsYou("cutshort", what, job.title, job.company, thread_url(qn.conversation_id)))
            return

        answers = {}
        for sq in qn.questions:
            if not sq.supported:
                continue  # optional and not something Sortie can give: left blank
            q = sq.to_question()
            answer = await answerer.answer(q, job)
            if answer is None:
                if sq.mandatory:
                    log.info(f"  ⏭  {label}: couldn't answer \"{sq.title[:80]}\"; questionnaire not sent",
                             extra={"detail": True})
                    self.needs_you.append(NeedsYou("cutshort", f"questionnaire: \"{sq.title[:60]}\"", job.title,
                                                   job.company, thread_url(qn.conversation_id)))
                    return
                continue
            answers[sq.title] = answer

        response = await self.page.goto(thread_url(qn.conversation_id), wait_until="domcontentloaded")
        if response and response.status >= 500:
            raise ServerDown(f"HTTP {response.status}")
        # a question is one list item: choices are a fieldset titled by its legend, text questions a label with a
        # textarea under it (verified 2026-11-10)
        title_el = "legend, label"
        first = next(sq.title for sq in qn.questions)
        form = self.page.locator("form").filter(has=self.page.locator(title_el, has_text=first[:60]))
        await form.first.wait_for(timeout=20000)
        for title, answer in answers.items():
            block = form.first.locator("li").filter(has=self.page.locator(title_el, has_text=title[:60])).first
            values = answer.value if isinstance(answer.value, list) else [answer.value]
            if next(sq for sq in qn.questions if sq.title == title).options:
                for value in values:  # options are clickable blocks (div[tabindex=0]) holding the option text
                    await block.locator("[tabindex='0']").filter(
                        has_text=re.compile(rf"^\s*{re.escape(value)}\s*$")).first.click()
            else:
                await block.locator("textarea, input").first.fill(", ".join(values))

        if not await confirm(job, answers):
            log.info(f"  📝 {label}: questionnaire not sent", extra={"detail": True})
            return
        await form.first.locator("button[type=submit]").click()
        # CutShort then asks "Responses cannot be changed after submission. Are you sure?" [Yes] [No] inside the
        # same form, and Yes is the new submit button (verified 2026-11-10); the answers were already confirmed above
        await self.page.wait_for_timeout(800)
        sure = form.first.locator("button[type=submit]", has_text=re.compile(r"^\s*yes\s*$", re.I))
        if await sure.count():
            await sure.first.click()
        for _ in range(15):  # confirmed from CutShort's own data, not the page
            await self.page.wait_for_timeout(1000)
            thread = await self.page.request.get(f"{BASE}/loadthread-v2/{qn.conversation_id}")
            if thread.ok and submitted(await thread.json()):
                log.info(f"  ✅ questionnaire sent: {label}", extra={"detail": True})
                for title, answer in answers.items():
                    value = ", ".join(answer.value) if isinstance(answer.value, list) else answer.value
                    log.info(f"        Q: {title} → {value} ({answer.source})", extra={"detail": True})
                return
        log.warning(f"  questionnaire for {label} may not have been sent; check {thread_url(qn.conversation_id)}")
        await debug_dump("questionnaire-unconfirmed")
