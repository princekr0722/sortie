"""CutShort adapter. Search reads the job JSON CutShort embeds in its listing pages (Next.js __NEXT_DATA__), which
includes each job's full description; applying uses the logged-in apply view and the generic form filler.
Selectors live in SELECTORS; the apply flow is mapped from live review runs (see doc/features/site-cutshort.md)."""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError

from sortie.browser import human_pause
from sortie.config import Profile, SearchConfig
from sortie.filters import COUNTRIES, location_aliases
from sortie.llm.answerer import Answerer
from sortie.log import log
from sortie.models import Answer, ApplyResult, Job, Question, QuestionKind, Status
from sortie.sites.base import ConfirmSubmit, SiteAdapter
from sortie.sites.forms import (MAX_LLM_MOVES, Scope, fill_field, fill_step, find_button, form_errors,
                                llm_step_move, needs_you_reason, read_fields)
from sortie.sites.cutshort_inbox import CutshortInbox
from sortie.textutil import html_to_text

BASE = "https://cutshort.io"
# CutShort's listing slug where it differs from the city name (verified 2026-11-10; other cities as written)
CITY_SLUGS = {"bengaluru": "bangalore-bengaluru", "bangalore": "bangalore-bengaluru"}
MAX_SLUGS_PER_ROLE = 4
# words that don't make a listing on their own ("software-engineer" pages exist but match everything)
GENERIC_WORDS = {"software", "engineer", "developer", "sde", "senior", "junior", "lead", "and", "with", "in", "the"}
MAX_STEPS = 8
QUESTIONNAIRE_DELAY_S = 60  # CutShort's questionnaire arrives a moment after applying

SELECTORS = {
    # apply view, logged in: to verify on the first review run
    "dialog": "[role=dialog]:visible, .modal:visible",
}
APPLY_BUTTON = re.compile(r"apply( now| for this job)?|i'?m interested|i am interested|express interest", re.I)
JOB_VIEW_WAIT_S = 25  # the logged-in job view renders its buttons after a loading spinner (2026-11-10)
# after applying, the job's card button turns into "View conversation" (verified 2026-11-10)
APPLIED_STATE = re.compile(r"applied|application sent|withdraw application|view conversation", re.I)
# popup when you apply to a job you already have a conversation for (2026-11-10)
ALREADY_TEXT = re.compile(r"already have an active conversation|already applied", re.I)
# "Apply now" opens a message box to the hiring team; that message *is* the application (verified 2026-11-10)
MESSAGE_BOX = re.compile(r"type (your|a) message|write (your|a) message|message to|add a (note|message)|your message",
                         re.I)
NEXT_BUTTON = re.compile(r"continue|next|proceed", re.I)
SUBMIT_BUTTON = re.compile(r"submit( application)?|send( application)?|apply|confirm", re.I)
SUCCESS_TEXT = re.compile(r"applied successfully|successfully applied|application (sent|submitted)|you('ve| have) applied",
                          re.I)

# A job card's action button: "Apply now", or "View conversation" once applied (verified 2026-11-10). The listing
# re-builds its cards while scrolling, so the button is located by description at click time (a Playwright locator),
# never tagged in advance: a tagged element can be replaced before the click lands.
_LOWER = "translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')"
CARD_ACTION = f'contains("|apply now|apply|view conversation|applied|", concat("|", {_LOWER}, "|"))'


def card_action_xpath(job_url: str) -> str:
    """This job's card (the nearest block around its link that has an action button) and that button."""
    path = urlparse(job_url).path.replace('"', "")
    # "Apply now" is a <button>; once applied, "View conversation" is an <a> to the thread (verified 2026-11-10)
    action = f"*[(self::button or self::a) and {CARD_ACTION}]"
    return f'xpath=//a[contains(@href, "{path}")]/ancestor::div[.//{action}][1]//{action}'


# popup when you apply to a job you already have a conversation for (2026-11-10)
ALREADY_TEXT = re.compile(r"already have an active conversation|already applied", re.I)
# "Apply now" opens a message box to the hiring team; that message *is* the application (verified 2026-11-10)
MESSAGE_BOX = re.compile(r"type (your|a) message|write (your|a) message|message to|add a (note|message)|your message",
                         re.I)
NEXT_BUTTON = re.compile(r"continue|next|proceed", re.I)
SUBMIT_BUTTON = re.compile(r"submit( application)?|send( application)?|apply|confirm", re.I)
SUCCESS_TEXT = re.compile(r"applied successfully|successfully applied|application (sent|submitted)|you('ve| have) applied",
                          re.I)

# After Apply: find the message box by its placeholder, then the smallest container around it that also holds a
# Send / Submit button; tag it so the form filler only ever sees that (never the listing behind it).
MESSAGE_FORM_JS = """() => {
  document.querySelectorAll('[data-sortie-form]').forEach(e => e.removeAttribute('data-sortie-form'));
  const visible = e => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  const box = [...document.querySelectorAll('textarea, input, [contenteditable=true]')].find(e => visible(e) &&
    /type (your|a) message|write (your|a) message|message to|add a (note|message)|your message/i
      .test(e.getAttribute('placeholder') || e.getAttribute('data-placeholder') || e.getAttribute('aria-label') || ''));
  if (!box) return false;
  let el = box;
  for (let i = 0; i < 10 && el; i++) {
    el = el.parentElement;
    if (el && [...el.querySelectorAll('button')].some(b => /^\\s*(send|submit|apply|send application)\\s*$/i.test(b.innerText))) {
      el.dataset.sortieForm = '1';
      return true;
    }
  }
  return false;
}"""

# Does the apply form belong to this job? Look up from the form for the job's title / company, but never into a
# container that holds more than one job link (that's the listing, which mentions every job).
FORM_MATCHES_JS = """([needles]) => {
  let el = document.querySelector('[data-sortie-form], [role=dialog]');
  for (let i = 0; i < 8 && el; i++, el = el.parentElement) {
    if (el.querySelectorAll('a[href*="/job/"]').length > 1) return false;
    const text = (el.innerText || '').toLowerCase();
    if (needles.some(n => n && text.includes(n))) return true;
  }
  return false;
}"""

JOBS_JS = """() => {
  try {
    const d = JSON.parse(document.getElementById('__NEXT_DATA__').textContent);
    const jobs = d.props.pageProps.dehydratedState.queries[0].state.data.data.pageData.jobs || [];
    return jobs.map(j => ({id: j._id, headline: j.headline, company: (j.companyDetails || {}).name,
      locations: j.locationsText, remote: j.remoteType, exp: j.expRange, salary: j.salaryRangeText,
      skills: j.allSkills, description: j.sanitizedComment, publicUrl: j.publicUrl, applyUrl: j.authApplyUrl}));
  } catch (e) { return null; }
}"""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


ROLE_NOUNS = {"developer", "engineer"}
KNOWN_PHRASES = {"full-stack", "spring-boot", "machine-learning", "data-engineer"}  # skills / families spanning 2 words


def role_slugs(role: str, skills: list[str]) -> list[str]:
    """Listing keywords for a role, most specific first. CutShort only has pages for common keywords (java,
    spring-boot, backend-developer), not whole phrases (verified 2026-11-10), so a role becomes: the whole phrase,
    job-family pairs ("backend-developer"), then the skills it names ("java", "spring-boot")."""
    words = _slug(role).split("-")
    text = f"-{'-'.join(words)}-"
    skill_slugs = {_slug(s) for s in skills} | KNOWN_PHRASES
    candidates = ["-".join(words)]
    pairs = [(i, a, b) for i, (a, b) in enumerate(zip(words, words[1:])) if b in ROLE_NOUNS and a not in GENERIC_WORDS]
    # not the second half of a phrase ("stack-developer" from "full-stack developer")
    candidates += [f"{a}-{b}" for i, a, b in pairs if not (i and f"{words[i - 1]}-{a}" in skill_slugs)]
    candidates += [f"{w}-developer" for w in words if w in ("backend", "frontend") and not pairs]  # "SWE Backend"
    candidates += sorted((s for s in skill_slugs if "-" in s and f"-{s}-" in text), key=text.index)
    candidates += [w for w in words if w in skill_slugs]
    out = []
    for slug in candidates:
        if slug not in out and not set(slug.split("-")) <= GENERIC_WORDS:
            out.append(slug)
    return out[:MAX_SLUGS_PER_ROLE]


def listing_url(keyword: str, city: str) -> str:
    key = city.strip().lower()
    return f"{BASE}/jobs/{keyword}-jobs-in-{CITY_SLUGS.get(key, _slug(key))}"


def data_to_job(item: dict) -> Job | None:
    if not item.get("id"):
        return None
    exp = item.get("exp") or {}
    experience = f"{exp['min']}-{exp['max']} Yrs" if exp.get("min") is not None and exp.get("max") is not None else ""
    locations = [p.strip() for p in re.split(r",|\|", item.get("locations") or "") if p.strip()]
    remote = (item.get("remote") or "").lower()
    if "remote" in remote and "not" not in remote:
        locations.append("Remote")
    description = html_to_text(item.get("description") or "")
    skills = [s for s in item.get("skills") or [] if isinstance(s, str)]
    if skills:
        description += "\n\nKey skills: " + ", ".join(skills)
    return Job(
        site="cutshort",
        site_job_id=item["id"],
        # the public job page: the listing's authApplyUrl (/profile/view/j/<id>) never loads, not even for a person
        # (2026-11-10)
        url=item.get("publicUrl") or f"{BASE}/job/{item['id']}",
        title=item.get("headline") or "",
        company=item.get("company") or "",
        locations=locations,
        experience=experience,
        salary=item.get("salary") or "",
        description=description,
    )


class CutshortAdapter(SiteAdapter):
    name = "cutshort"
    login_url = BASE
    FOLLOWUPS = True  # recruiters send a screening questionnaire after you apply

    def __init__(self, page):
        super().__init__(page)
        self.found_on: dict[str, str] = {}  # job id -> listing URL it was found on (applying happens from its card)
        self.inbox = CutshortInbox(page, self.needs_you)
        self.last_applied = 0.0  # monotonic time of the last application this run

    async def after_jobs(self, answerer: Answerer, confirm: ConfirmSubmit) -> None:
        """CutShort sends its questionnaire shortly after you apply: give the newest application a minute before
        the runner's second follow-up pass."""
        if self.last_applied and (wait := QUESTIONNAIRE_DELAY_S - (time.monotonic() - self.last_applied)) > 0:
            log.info(f"  waiting {wait:.0f} s for questionnaires on this run's applications", extra={"detail": True})
            await self.page.wait_for_timeout(wait * 1000)

    async def follow_up(self, answerer: Answerer, confirm: ConfirmSubmit) -> None:
        self.inbox.page = self.page
        await self.inbox.follow_up(self.followups, self.followup_updates, answerer, confirm, self.debug_dump)

    async def ensure_logged_in(self) -> bool:
        await self.page.goto(BASE, wait_until="domcontentloaded")
        await self.check_blocked()
        # logged out, the session endpoint redirects (302) to the home page (verified 2026-11-10)
        response = await self.page.request.get(f"{BASE}/api/auth/user", max_redirects=0)
        return response.status == 200

    async def search(self, search: SearchConfig, profile: Profile) -> AsyncIterator[Job]:
        skills = list(profile.skills_since)
        yielded: set[str] = set()
        visited: set[str] = set()
        for location in search.locations:
            if location_aliases(location)[0] in COUNTRIES - {"india"}:
                log.info(f"  not searching {location} on CutShort (India only)", extra={"detail": True})
                continue
            for role in search.roles:
                for keyword in role_slugs(role, skills):
                    url = listing_url(keyword, location)
                    if url in visited:
                        continue
                    visited.add(url)
                    try:
                        await self.page.goto(url, wait_until="domcontentloaded")
                    except PlaywrightError as e:  # e.g. ERR_ABORTED by the site's own redirect: skip this keyword
                        log.info(f"  search: {keyword} · {location} didn't load ({str(e).splitlines()[0][:80]})",
                                 extra={"detail": True})
                        continue
                    await self.check_blocked()
                    items = await self.page.evaluate(JOBS_JS)
                    if not items:  # no listing page for this keyword
                        continue
                    new = 0
                    for item in items:
                        job = data_to_job(item)
                        if job and job.site_job_id not in yielded:
                            yielded.add(job.site_job_id)
                            self.found_on[job.site_job_id] = url
                            new += 1
                            yield job
                    log.info(f"  search: {keyword} · {location} → {len(items)} jobs, {new} new", extra={"detail": True})
                    await human_pause(2, 5)

    async def get_details(self, job: Job) -> Job:
        return job  # the listing data already has the full description, experience and salary

    async def apply(self, job: Job, answerer: Answerer, confirm: ConfirmSubmit) -> ApplyResult:
        main_page = self.page
        try:
            result = await self._apply(job, answerer, confirm)
            if result.status == Status.APPLIED:
                self.last_applied = time.monotonic()
            else:
                await self._close_dialog()  # never leave a half-written application open
            return result
        finally:
            if self.page is not main_page:  # Apply opened a new tab: close it, back to the listing tab
                await self.page.close()
                self.page = main_page

    async def _apply(self, job: Job, answerer: Answerer, confirm: ConfirmSubmit) -> ApplyResult:
        button = await self._card_button(job)
        if button == "applied":
            return ApplyResult(Status.SKIPPED, "already applied")
        if button is None:  # not on its listing any more: try the job's own page (some load, some redirect)
            await self.page.goto(job.url, wait_until="domcontentloaded")
            await self.check_blocked()
            if not self.page.url.split("?")[0].rstrip("/").startswith(job.url.split("?")[0].rstrip("/")):
                # e.g. a listing full of other jobs' Apply buttons: never click anything there
                return ApplyResult(Status.FAILED, f"job page redirected to {self.page.url.split('?')[0]}",
                                   screenshot_path=await self.debug_dump("redirected"))
            for _ in range(JOB_VIEW_WAIT_S * 2):  # wait out the loading dots until the job view has its buttons
                await self.page.wait_for_timeout(500)
                if await find_button(self.page, APPLIED_STATE):
                    return ApplyResult(Status.SKIPPED, "already applied")
                if button := await find_button(self.page, APPLY_BUTTON):
                    break
            if not button:
                return ApplyResult(Status.FAILED, "no apply button",
                                   screenshot_path=await self.debug_dump("no-apply"))
        tabs_before = set(self.page.context.pages)
        await button.click()
        form = None
        for _ in range(30):  # the message box / dialog takes a moment to open, possibly in a new tab
            await self.page.wait_for_timeout(500)
            if new_tabs := [p for p in self.page.context.pages if p not in tabs_before]:
                self.page = new_tabs[-1]  # follow it; screenshots and dumps now show it too
                await self.page.wait_for_load_state("domcontentloaded")
            if ALREADY_TEXT.search(await self.page.locator("body").inner_text()):
                await self.page.keyboard.press("Escape")
                return ApplyResult(Status.SKIPPED, "already applied (active conversation on this job)")
            if form := await self._scope():
                break
        if form is None:
            return ApplyResult(Status.FAILED, "no message box or form after Apply; nothing filled",
                               screenshot_path=await self.debug_dump("no-form"))
        # the form must be for this job (a page can also list similar jobs with their own Apply buttons)
        if not await self.page.evaluate(FORM_MATCHES_JS, [[job.company.lower(), job.title.lower()]]):
            await self.page.keyboard.press("Escape")
            return ApplyResult(Status.FAILED, "apply form doesn't mention this job; not filled",
                               screenshot_path=await self.debug_dump("wrong-form"))
        return await self._fill_and_submit(job, answerer, confirm)

    async def _card_button(self, job: Job):
        """This job's own card button on the listing it was found on: a locator for "Apply now", "applied", or
        None if the card isn't there."""
        listing = self.found_on.get(job.site_job_id)
        if not listing:
            return None
        if self.page.url.split("?")[0] != listing:
            await self.page.goto(listing, wait_until="domcontentloaded")
            await self.check_blocked()
        button = self.page.locator(card_action_xpath(job.url))
        for _ in range(20):
            await self.page.wait_for_timeout(500)
            if await button.count():
                await button.first.scroll_into_view_if_needed()
                await self.page.wait_for_timeout(800)  # cards re-render around the scroll; the locator re-resolves
                label = (await button.first.inner_text()).strip()
                return "applied" if APPLIED_STATE.fullmatch(label) else button.first
        return None

    async def _close_dialog(self) -> None:
        dialog = self.page.locator(SELECTORS["dialog"])
        try:
            if await dialog.count():
                cancel = await find_button(dialog.first, re.compile(r"cancel|close", re.I))
                if cancel:
                    await cancel.click(timeout=3000)
                else:
                    await self.page.keyboard.press("Escape")
        except PlaywrightError:
            pass

    async def _scope(self) -> Scope | None:
        """The apply form: an open dialog, or the box around the message field. None if neither is there, and
        then nothing is filled (the page behind it is a listing full of other jobs)."""
        dialog = self.page.locator(SELECTORS["dialog"])
        if await dialog.count():
            return dialog.first
        if await self.page.evaluate(MESSAGE_FORM_JS):
            return self.page.locator("[data-sortie-form='1']").first
        return None

    async def _fill_and_submit(self, job: Job, answerer: Answerer, confirm: ConfirmSubmit) -> ApplyResult:
        answers: dict[str, Answer] = {}
        llm_moves = 0
        for _ in range(MAX_STEPS):
            await self.page.wait_for_timeout(1500)
            if SUCCESS_TEXT.search(await self.page.locator("body").inner_text()):
                return ApplyResult(Status.APPLIED, answers=answers, screenshot_path=await self.screenshot("applied"))
            target = await self._scope()
            if target is None:  # the form closed without a success message
                return await self._await_success(answers, job)
            if reason := await needs_you_reason(target):
                path = await self.debug_dump("needs-you")
                return ApplyResult(Status.SKIPPED, f"needs you: {reason}", answers, path, needs_you=reason)

            if not await self._write_message(target, job, answerer, answers):
                return ApplyResult(Status.SKIPPED, "couldn't write the message to the hiring team", answers,
                                   await self.debug_dump("no-message"))
            _, unanswered = await fill_step(target, answerer, job, answers, fill_optional=frozenset({"textarea"}))
            if unanswered:
                return ApplyResult(Status.SKIPPED, f"couldn't answer: {unanswered.text}", answers,
                                   await self.debug_dump("unanswered"))
            # never submit with a required field still empty
            empty = [f.label for f in await read_fields(target) if f.required and not f.filled]
            if empty:
                return ApplyResult(Status.FAILED, f"required field left empty: {empty[0][:80]}", answers,
                                   await self.debug_dump("incomplete"))

            submit = await find_button(target, SUBMIT_BUTTON)
            if submit and not await find_button(target, NEXT_BUTTON):
                if not await confirm(job, answers):
                    return ApplyResult(Status.SKIPPED, "not submitted", answers)
                await submit.click()
                return await self._await_success(answers, job)
            next_button = await find_button(target, NEXT_BUTTON)
            if next_button:
                await next_button.click()
                continue
            if llm_moves >= MAX_LLM_MOVES or not (move := await llm_step_move(
                    answerer.llm, target, job, await form_errors(target))):
                return ApplyResult(Status.FAILED, "unknown apply step", answers, await self.debug_dump("unknown-step"))
            llm_moves += 1
            log.info(f"  🤖 form step: {move.action}" + (f' "{move.label}"' if move.label else ""), extra={"detail": True})
            if move.action == "skip_job":
                return ApplyResult(Status.SKIPPED, f"CutShort form: {move.why or 'cannot be completed'}", answers)
        return ApplyResult(Status.FAILED, f"apply form had more than {MAX_STEPS} steps", answers,
                           await self.debug_dump("too-many-steps"))

    async def _write_message(self, target: Scope, job: Job, answerer: Answerer, answers: dict[str, Answer]) -> bool:
        """Fill CutShort's message box (its placeholder is not a question) with a note written for this job.
        False only when there is an empty message box and no message could be written."""
        for f in await read_fields(target):
            if f.filled or f.kind not in ("textarea", "text") or not MESSAGE_BOX.search(f.label or f.placeholder):
                continue
            q = Question(f"Write a short message to the hiring team for your application to {job.title} at "
                         f"{job.company}: 3-5 first-person sentences on why you're a good fit, citing the candidate's "
                         f"work and skills that match this job. No greeting line, no sign-off.", QuestionKind.TEXT)
            answer = await answerer.compose(q, job)
            if answer is None:
                return False
            await fill_field(target, f, answer.value)
            answers["Message to the hiring team"] = answer
        return True

    async def _await_success(self, answers: dict[str, Answer], job: Job | None = None) -> ApplyResult:
        """After Send: CutShort shows "Sending...", then a success strip, closes the dialog and turns this job's
        card button into "View conversation" (verified 2026-11-10). Only this job's card counts: other cards on the
        listing may already say "View conversation"."""
        card = self.page.locator(card_action_xpath(job.url)) if job else None
        for _ in range(25):
            await self.page.wait_for_timeout(1000)
            try:
                if card is not None and await card.count() and \
                        APPLIED_STATE.fullmatch((await card.first.inner_text()).strip()):
                    # "View conversation" links to the thread (…&thread=<id>&…): kept for the follow-up
                    href = await card.first.get_attribute("href") or ""
                    thread = re.search(r"[?&]thread=([0-9a-f]+)", href)
                    return ApplyResult(Status.APPLIED, answers=answers, screenshot_path=await self.screenshot("applied"),
                                       conversation_id=thread.group(1) if thread else "")
                if SUCCESS_TEXT.search(await self.page.locator("body").inner_text()):
                    return ApplyResult(Status.APPLIED, answers=answers, screenshot_path=await self.screenshot("applied"))
            except PlaywrightError:
                continue
        return ApplyResult(Status.FAILED, "submit did not complete", answers, await self.debug_dump("submit"))