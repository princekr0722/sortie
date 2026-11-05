"""Naukri.com adapter. Selectors live in SELECTORS so site changes are a one-place fix."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from urllib.parse import quote

from playwright.async_api import TimeoutError as PlaywrightTimeout

from sortie.browser import human_pause
from sortie.config import Profile, SearchConfig
from sortie.filters import COUNTRIES, location_aliases
from sortie.llm.answerer import Answerer
from sortie.log import log
from sortie.models import Answer, ApplyResult, Job, Question, QuestionKind, Status
from sortie.sites.base import ConfirmSubmit, SiteAdapter

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
    # apply chatbot (verified 2026-11-05 except checkbox). Use exact classes: substring matches like
    # [class*='chatbot_Chip'] also hit the .chatbot_Chips container and read every option as one.
    "chat_drawer": ".chatbot_DrawerContentWrapper",
    "chat_bot_msg": ".chatbot_ListItem .botMsg",
    "chat_radio": ".ssrc__radio-btn-container",
    "chat_checkbox": ".mcc__checkbox-container",  # not seen live yet
    "chat_chip": ".chatbot_Chip",
    "chat_text": "div.textArea[contenteditable='true']",
    "chat_send": ".sendMsg",
    "chat_close": ".chatbot_Nav .crossIcon",
}
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
    """One element's text can hold a whole option list ("Bengaluru / Bangalore\nPune\nLoad more")."""
    options = []
    for text in texts:
        for line in text.split("\n"):
            line = line.strip()
            if line and line.lower() not in OPTION_NOISE and line not in options:
                options.append(line)
    return options


def question_kind(text: str, options: list[str], multi: bool) -> QuestionKind:
    if options:
        return QuestionKind.MULTI_CHOICE if multi else QuestionKind.CHOICE
    t = text.lower()
    if re.search(r"ctc|lakh|lpa|salary", t):
        return QuestionKind.DECIMAL
    if re.search(r"how many|years|days|number of", t):
        return QuestionKind.NUMBER
    return QuestionKind.TEXT


class NaukriAdapter(SiteAdapter):
    name = "naukri"
    login_url = f"{BASE}/nlogin/login"

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
        return await self._finish_application(job, answerer)

    async def _finish_application(self, job: Job, answerer: Answerer) -> ApplyResult:
        answers: dict[str, Answer] = {}
        seen_questions = 0
        for _ in range(20):
            await self.page.wait_for_timeout(2500)
            if await self._succeeded():
                return ApplyResult(Status.APPLIED, answers=answers, screenshot_path=await self.screenshot("applied"))
            drawer = self.page.locator(SELECTORS["chat_drawer"])
            if not await drawer.count() or not await drawer.first.is_visible():
                continue
            messages = await self.page.locator(SELECTORS["chat_bot_msg"]).all_inner_texts()
            if len(messages) <= seen_questions:
                continue
            seen_questions = len(messages)
            q = await self._read_question(messages[-1].strip())
            answer = await answerer.answer(q, job)
            if answer is None:
                path = await self.debug_dump("unanswered")
                await self._close_chat()
                return ApplyResult(Status.SKIPPED, f"couldn't answer: {q.text}", answers, path)
            await self._send_answer(q, answer.value)
            answers[q.text] = answer
        return ApplyResult(Status.FAILED, "apply did not complete", answers, await self.debug_dump("stuck"))

    async def _succeeded(self) -> bool:
        if await self.page.title() == "Apply Confirmation":
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
        send = self.page.locator(SELECTORS["chat_send"])
        if await send.count():
            await send.first.click()

    async def _close_chat(self) -> None:
        close = self.page.locator(SELECTORS["chat_close"])
        if await close.count():
            await close.first.click()
