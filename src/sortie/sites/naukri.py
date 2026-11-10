"""Naukri.com adapter. Selectors live in SELECTORS so site changes are a one-place fix."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from urllib.parse import quote

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from sortie.browser import human_pause
from sortie.config import Profile, SearchConfig
from sortie.filters import COUNTRIES, location_aliases
from sortie.llm.answerer import Answerer, question_kind
from sortie.log import log
from sortie.models import Answer, ApplyResult, Job, Question, QuestionKind, Status
from sortie.sites.base import BlockedError, ConfirmSubmit, SiteAdapter

BASE = "https://www.naukri.com"
PAGE_SIZE = 20
MAX_PAGES = 5
JOB_AGE_DAYS = 7

# Naukri's URL slug / `l=` value where it differs from the city name
CITY_SLUGS = {"bengaluru": "bangalore", "gurugram": "gurgaon", "new delhi": "delhi", "delhi ncr": "delhi"}

SELECTORS = {
    "login_form": "form#loginForm, input#usernameField",
    # search results (verified 2026-11-05)
    "card": ".srp-jobtuple-wrapper[data-job-id]",
    # job page (verified 2026-11-05)
    "jd": "[class*='JDC__dang-inner-html']",
    "key_skills": "[class*='key-skill'] a, [class*='key-skill'] span",
    "salary": "[class*='jhc__salary__'] span",
    "apply_button": "#apply-button",
    "company_site_button": "#company-site-button",
    "already_applied": "#already-applied, [class*='already-applied']",
    # after a successful apply Naukri navigates to "Apply Confirmation" (verified 2026-11-05)
    "apply_confirmation": ".applied-job-content",
    # apply chatbot (verified 2026-11-05). Use exact classes: substring matches like
    # [class*='chatbot_Chip'] also hit the .chatbot_Chips container and read every option as one.
    "chat_drawer": ".chatbot_DrawerContentWrapper",
    # the "..." typing bubble is also a .botMsg (li.loader); reading it as the question was the bug of 2026-11-08/09
    "chat_bot_msg": ".chatbot_ListItem:not(.loader) .botMsg",
    "chat_typing": ".chatbot_ListItem.loader:visible",
    "chat_radio": ".ssrc__radio-btn-container",
    "chat_checkbox": ".multicheckboxes-container .mcc__label",
    "chat_chip": ".chatbot_Chip",
    "chat_text": "div.textArea[contenteditable='true']",
    # the clickable Save is the outer container; the inner .sendMsg text is covered by it
    "chat_send": ".sendMsgbtn_container",
    "chat_send_enabled": ".sendMsgbtn_container .send:not(.disabled)",
    "chat_close": ".chatbot_Nav .crossIcon",
}
# short-lived notices Naukri shows after clicking Apply (daily limit, per-company limit, errors)
NOTICE_SELECTOR = "[role='alert'], [class*='toast' i], [class*='snackbar' i], [class*='Toaster']"
# Apply clicks in a row that did nothing before Naukri is stopped for the run (likely a daily/company limit)
MAX_NO_EFFECT = 2
NO_EFFECT = "Apply did nothing (no questions, no confirmation)"
# check the page every POLL_MS for up to ~50 s; a new chatbot question gets QUESTION_SETTLE_MS to render
POLL_MS = 500
POLL_STEPS = 100
QUESTION_SETTLE_MS = 700
WALK_IN_BUTTON = re.compile(r"^\s*I am interested\s*$", re.M)
# the chatbot's own failure message; answering it like a question goes nowhere
CHAT_ERROR = re.compile(r"something went wrong", re.I)
SUCCESS_TEXT = re.compile(r"successfully applied|application (has been )?(submitted|sent)|you have applied", re.I)
OPTION_NOISE = {"load more", "show more", "skip", "skip this question"}
# Save every chatbot question's HTML to data/debug/ until the chatbot selectors are confirmed live
DUMP_CHATBOT = True

CARD_JS = """els => els.map(e => {
  const t = s => (e.querySelector(s)?.textContent || '').trim();
  return {id: e.dataset.jobId, url: e.querySelector('a.title')?.href || '', title: t('a.title'),
          company: t('.comp-name'), exp: t('.expwdth'), salary: t('.sal'), location: t('.locWdth')};
})"""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def search_url(role: str, location_slug: str, experience_years: int, page: int = 1) -> str:
    page_part = "" if page == 1 else f"-{page}"
    return (f"{BASE}/{_slug(role)}-jobs-in-{location_slug}{page_part}"
            f"?k={quote(role.lower())}&l={location_slug}&experience={experience_years}&jobAge={JOB_AGE_DAYS}")


def card_to_job(card: dict) -> Job | None:
    if not card.get("id") or not card.get("url"):
        return None
    locations = [l.strip() for l in card["location"].split(",") if l.strip()]
    salary = card["salary"] if "disclosed" not in card["salary"].lower() else ""
    return Job(site="naukri", site_job_id=card["id"], url=card["url"], title=card["title"],
               company=card["company"], locations=locations, experience=card["exp"], salary=salary)


def split_options(texts: list[str]) -> list[str]:
    """One element's text can hold a whole option list ("Bengaluru / Bangalore\nChennai\nLoad more")."""
    options = []
    for text in texts:
        for line in text.split("\n"):
            line = line.strip()
            if line and line.lower() not in OPTION_NOISE and line not in options:
                options.append(line)
    return options


class NaukriAdapter(SiteAdapter):
    name = "naukri"
    login_url = f"{BASE}/nlogin/login"

    def __init__(self, page):
        super().__init__(page)
        self.no_effect_streak = 0

    async def ensure_logged_in(self) -> bool:
        await self.page.goto(f"{BASE}/mnjuser/homepage", wait_until="domcontentloaded")
        await self.page.wait_for_timeout(4000)
        await self.check_blocked()
        if "nlogin" in self.page.url:
            return False
        return await self.page.locator(SELECTORS["login_form"]).count() == 0

    async def search(self, search: SearchConfig, profile: Profile) -> AsyncIterator[Job]:
        years = profile.total_experience().years
        yielded: set[str] = set()  # the same job shows up under several roles / cities
        for location in search.locations:
            key = location.strip().lower()
            if location_aliases(key)[0] in COUNTRIES - {"india"}:
                log.info(f"  not searching {location} on Naukri (India only)", extra={"detail": True})
                continue
            slug = CITY_SLUGS.get(key, _slug(key))
            for role in search.roles:
                for page in range(1, MAX_PAGES + 1):
                    await self.page.goto(search_url(role, slug, years, page), wait_until="domcontentloaded")
                    try:
                        await self.page.wait_for_selector(SELECTORS["card"], timeout=10000)
                    except PlaywrightTimeout:
                        await self.check_blocked()
                        break  # no results
                    cards = await self.page.locator(SELECTORS["card"]).evaluate_all(CARD_JS)
                    log.info(f"  search: {role} · {location} · page {page} → {len(cards)} jobs", extra={"detail": True})
                    for card in cards:
                        job = card_to_job(card)
                        if job and job.site_job_id not in yielded:
                            yielded.add(job.site_job_id)
                            yield job
                    if len(cards) < PAGE_SIZE:
                        break  # last page; later pages ignore the freshness filter
                    await human_pause(2, 5)

    async def get_details(self, job: Job) -> Job:
        await self.page.goto(job.url, wait_until="domcontentloaded")
        try:
            await self.page.wait_for_selector(SELECTORS["jd"], timeout=10000)
        except PlaywrightTimeout:
            await self.check_blocked()
            return job
        job.description = await self.page.locator(SELECTORS["jd"]).first.inner_text()
        skills = await self.page.locator(SELECTORS["key_skills"]).all_inner_texts()
        if skills:
            job.description += "\n\nKey skills: " + ", ".join(dict.fromkeys(s.strip() for s in skills if s.strip()))
        if await self.page.locator(SELECTORS["company_site_button"]).count():
            job.external = True
        else:
            button = self.page.locator(SELECTORS["apply_button"])
            if await button.count() and "company" in (await button.first.inner_text()).lower():
                job.external = True
        salary = self.page.locator(SELECTORS["salary"])
        if not job.salary and await salary.count():
            text = (await salary.first.inner_text()).strip()
            job.salary = "" if "disclosed" in text.lower() else text
        return job

    async def apply(self, job: Job, answerer: Answerer, confirm: ConfirmSubmit) -> ApplyResult:
        if not self.page.url.startswith(job.url.split("?")[0]):
            await self.get_details(job)

        if await self.page.locator(SELECTORS["already_applied"]).count():
            return ApplyResult(Status.SKIPPED, "already applied")
        if await self.page.locator(SELECTORS["company_site_button"]).count():
            return ApplyResult(Status.EXTERNAL, "applies on company site")
        button = self.page.locator(SELECTORS["apply_button"])
        if not await button.count():
            if walk_in := await self._walk_in():
                return ApplyResult(Status.SKIPPED, walk_in)
            return ApplyResult(Status.FAILED, "no apply button", screenshot_path=await self.debug_dump("no-apply"))
        label = (await button.first.inner_text()).strip().lower()
        if "applied" in label:
            return ApplyResult(Status.SKIPPED, "already applied")
        if "company" in label:
            return ApplyResult(Status.EXTERNAL, "applies on company site")

        # Naukri submits on click (or after the chatbot), so this is the point of no return
        if not await confirm(job, {}):
            return ApplyResult(Status.SKIPPED, "not submitted")
        await button.first.click()
        notice = await self._read_notice()
        try:
            result = await self._finish_application(job, answerer)
        except PlaywrightError:
            # the last answer often submits and leaves the chat at once, so a click aimed at the chat fails;
            # what counts is whether Naukri shows the apply confirmation (seen 2026-11-10)
            await self.page.wait_for_timeout(1500)
            if not await self._succeeded():
                raise
            result = ApplyResult(Status.APPLIED, screenshot_path=await self.screenshot("applied"))
        if result.status == Status.APPLIED:
            self.no_effect_streak = 0
        elif result.reason == NO_EFFECT:
            self.no_effect_streak += 1
            if notice:
                result.reason = f"{NO_EFFECT}: Naukri said \"{notice}\""
            if self.no_effect_streak >= MAX_NO_EFFECT:
                raise BlockedError(f"naukri: Apply did nothing on {self.no_effect_streak} jobs in a row"
                                   + (f" (\"{notice}\")" if notice else "")
                                   + "; likely Naukri's daily or per-company apply limit", result)
        return result

    async def _walk_in(self) -> str | None:
        """Walk-in drives have "I am interested" instead of Apply and a "Time and Venue" block (verified 2026-11-09).
        They mean showing up in person, so Sortie leaves them to you and puts the details in the reason."""
        body = await self.page.locator("body").inner_text()
        if not (WALK_IN_BUTTON.search(body) and ("Time and Venue" in body or "walk-in" in body.lower())):
            return None
        venue = body.split("Time and Venue", 1)[1].strip().split("\n")[:2] if "Time and Venue" in body else []
        return "walk-in drive, in person" + (f": {' · '.join(v.strip() for v in venue)}" if venue else "")

    async def _read_notice(self) -> str:
        """Text of a notice that flashes up right after clicking Apply, if any (gone within seconds).
        Stops as soon as the chatbot or a confirmation shows up, since then there's nothing to catch."""
        for _ in range(12):
            await self.page.wait_for_timeout(500)
            try:
                for text in await self.page.locator(NOTICE_SELECTOR).all_inner_texts():
                    if text.strip():
                        return " ".join(text.split())[:200]
                drawer = self.page.locator(SELECTORS["chat_drawer"])
                if await drawer.count() and await drawer.first.is_visible():
                    return ""
                if await self._succeeded():
                    return ""
            except PlaywrightError:  # navigating to the apply confirmation
                return ""
        return ""

    async def _finish_application(self, job: Job, answerer: Answerer) -> ApplyResult:
        answers: dict[str, Answer] = {}
        seen_questions = 0
        chat_seen = False
        for _ in range(POLL_STEPS):
            await self.page.wait_for_timeout(POLL_MS)
            try:
                if await self._succeeded():
                    await self.page.wait_for_load_state("domcontentloaded")
                    return ApplyResult(Status.APPLIED, answers=answers, screenshot_path=await self.screenshot("applied"))
                drawer = self.page.locator(SELECTORS["chat_drawer"])
                if not await drawer.count() or not await drawer.first.is_visible():
                    continue
                chat_seen = True
                if await self.page.locator(SELECTORS["chat_typing"]).count():
                    continue  # the bot is still typing: the question and its options aren't there yet
                messages = [m.strip() for m in await self.page.locator(SELECTORS["chat_bot_msg"]).all_inner_texts()]
            except PlaywrightError:  # page is navigating (usually to the apply confirmation); look again
                continue
            if len(messages) <= seen_questions or not messages[-1]:
                continue
            if CHAT_ERROR.search(messages[-1]):
                path = await self.debug_dump("chat-error")
                await self._close_chat()
                return ApplyResult(Status.FAILED, f"Naukri chatbot said: {messages[-1][:120]}", answers, path)
            seen_questions = len(messages)
            await self.page.wait_for_timeout(QUESTION_SETTLE_MS)  # let the new question's options render
            q = await self._read_question(messages[-1])
            answer = await answerer.answer(q, job)
            if answer is None:
                path = await self.debug_dump("unanswered")
                await self._close_chat()
                return ApplyResult(Status.SKIPPED, f"couldn't answer: {q.text}", answers, path)
            await self._send_answer(q, answer.value)
            answers[q.text] = answer
        reason = "apply did not complete" if chat_seen else NO_EFFECT
        return ApplyResult(Status.FAILED, reason, answers, await self.debug_dump("stuck"))

    async def _succeeded(self) -> bool:
        if "/myapply/saveApply" in self.page.url or await self.page.title() == "Apply Confirmation":
            return True
        if await self.page.locator(f'{SELECTORS["apply_confirmation"]}, {SELECTORS["already_applied"]}').count():
            return True
        body = await self.page.locator("body").inner_text()
        return bool(SUCCESS_TEXT.search(body))

    async def _read_question(self, text: str) -> Question:
        radios = await self.page.locator(SELECTORS["chat_radio"]).all_inner_texts()
        checks = await self.page.locator(SELECTORS["chat_checkbox"]).all_inner_texts()
        chips = await self.page.locator(SELECTORS["chat_chip"]).all_inner_texts()
        options = split_options(radios or checks or chips)
        if DUMP_CHATBOT:
            await self.debug_dump("chatbot")
        return Question(text, question_kind(text, options, multi=bool(checks)), options)

    async def _clear_text_box(self) -> None:
        """Naukri pre-fills the text box from your Naukri profile (e.g. current location); left there, it gets
        sent along with the chosen option, or the typed answer gets appended to it."""
        box = self.page.locator(SELECTORS["chat_text"])
        if not await box.count() or not await box.first.is_visible():
            return
        if (await box.first.inner_text()).strip():
            await box.first.click()
            await self.page.keyboard.press("ControlOrMeta+A")
            await self.page.keyboard.press("Backspace")

    async def _send_answer(self, q: Question, value: str | list[str]) -> None:
        await self._clear_text_box()
        if q.options:
            drawer = self.page.locator(SELECTORS["chat_drawer"]).first
            for item in value if isinstance(value, list) else [value]:
                # click the option's own text, not the centre of a container holding every option
                option = drawer.get_by_text(item, exact=True)
                if not await option.count():
                    raise RuntimeError(f"option {item!r} not found in chatbot")
                await option.last.click()
        else:
            box = self.page.locator(SELECTORS["chat_text"]).first
            await box.click()
            await self.page.keyboard.type(str(value), delay=60)
        await self._click_save()

    async def _click_save(self) -> None:
        """Save stays disabled until the answer registers. Submitting may navigate away mid-click
        (to the apply confirmation), so a failed click is only an error if the apply didn't go through."""
        send = self.page.locator(SELECTORS["chat_send"])
        if not await send.count():
            return
        try:
            await self.page.locator(SELECTORS["chat_send_enabled"]).first.wait_for(timeout=5000)
        except PlaywrightTimeout:
            pass
        try:
            await send.first.click(timeout=8000)
        except PlaywrightError:
            if await self._succeeded():
                return
            await send.first.click(force=True, timeout=5000)

    async def _close_chat(self) -> None:
        close = self.page.locator(SELECTORS["chat_close"])
        if await close.count():
            await close.first.click()
