"""Wellfound (AngelList Talent) adapter. Search reads the Apollo state Wellfound embeds in its role pages (full
descriptions included); applying opens the job page's Apply dialog and sends a job-specific note to the hiring team,
filled only inside that dialog. See doc/features/site-wellfound.md (apply flow to verify live)."""

from __future__ import annotations

import ast
import re
from collections.abc import AsyncIterator
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError

from sortie.browser import human_pause
from sortie.config import Profile, SearchConfig
from sortie.filters import location_aliases
from sortie.llm.answerer import Answerer
from sortie.log import log
from sortie.models import Answer, ApplyResult, Job, Question, QuestionKind, Status
from sortie.sites.base import ConfirmSubmit, SiteAdapter
from sortie.sites.forms import (MAX_LLM_MOVES, fill_field, fill_step, find_button, fix_rejected_phone,
                                form_errors, llm_step_move,
                                needs_you_reason, read_fields)
from sortie.textutil import html_to_text

BASE = "https://wellfound.com"
CITY_SLUGS = {"bengaluru": "bangalore", "bangalore": "bangalore", "gurugram": "gurgaon", "new delhi": "delhi"}
MAX_PAGES = 5
JOB_PAGE_WAIT_S = 20
MAX_STEPS = 6

# the apply popup is a modal dialog; the Help Scout chat widget is also role=dialog but not modal (2026-11-12)
SELECTORS = {"dialog": "[role=dialog][aria-modal=true]:visible:not([aria-label*='Help Scout'])"}
DIALOG_LOAD_S = 15  # the popup opens with a spinner and fills in a moment later
APPLY_BUTTON = re.compile(r"apply( now)?|easy apply", re.I)
APPLIED_STATE = re.compile(r"applied|application sent|view application", re.I)
SEND_BUTTON = re.compile(r"send( application)?|submit( application)?|apply|send note", re.I)
NEXT_BUTTON = re.compile(r"continue|next", re.I)
# after Send the dialog switches to "APPLICATION SCHEDULED" (Wellfound queues it to the company) and offers an
# optional AI video interview, which Sortie never starts (verified 2026-11-12)
# "Applications to this job are managed outside of Wellfound. … Apply on website" (verified 2026-11-12)
# verified 2026-11-12: the popup says this, and Send stays disabled, when the company doesn't hire from your
# location (your Wellfound profile's location)
# Two wordings seen: "<Company> is not accepting applications from your current location…" and "This job does not
# support the locations [or timezones for remote work] on your profile" (both verified 2026-11-12)
LOCATION_BLOCKED = re.compile(r"not accepting applications from your (current )?location|"
                              r"does not support the locations (or timezones )?(for remote work )?on your profile", re.I)
EXTERNAL_TEXT = re.compile(r"managed outside of wellfound|apply on (the )?(company )?website", re.I)
APPLICATION_STATUS = re.compile(r"pending|reviewed|interviewing|in review|offer|rejected|declined|withdrawn|archived",
                                re.I)
SUCCESS_TEXT = re.compile(r"application (sent|submitted|scheduled)|you('ve| have) applied|applied successfully", re.I)
NOTE_BOX = re.compile(r"note|message|cover letter|introduc|hiring (manager|team)|why (are you|do you|this)|tell (us|them)",
                      re.I)

JOBS_JS = """() => {
  try {
    const data = JSON.parse(document.getElementById('__NEXT_DATA__').textContent).props.pageProps.apolloState.data;
    const company = {};
    for (const [key, s] of Object.entries(data)) {
      if (!key.startsWith('StartupResult')) continue;
      for (const ref of [...(s.highlightedJobListings || []), ...(s.jobListings || [])])
        if (ref && ref.__ref) company[ref.__ref] = s.name;
    }
    return Object.entries(data).filter(([k]) => k.startsWith('JobListingSearchResult')).map(([k, j]) => ({
      id: j.id, title: j.title, slug: j.slug, company: company[k] || '', description: j.description,
      locations: j.locationNames, remote: j.remote, compensation: j.compensation, jobType: j.jobType,
      expMin: j.yearsExperienceMin, expMax: j.yearsExperienceMax, ats: j.atsSource,
      remoteOnlyIn: j.acceptedRemoteLocationNames}));
  } catch (e) { return null; }
}"""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def search_url(role: str, location: str | None, page: int = 1) -> str:
    """A city's role page, or the remote role page when `location` is None."""
    path = f"role/r/{_slug(role)}" if location is None else \
        f"role/l/{_slug(role)}/{CITY_SLUGS.get(location.strip().lower(), _slug(location))}"
    return f"{BASE}/{path}" + (f"?page={page}" if page > 1 else "")


def is_role_page(url: str) -> bool:
    """Wellfound redirects a role slug it doesn't know to the general /location/<city> page."""
    return "/role/" in url


def is_place_page(url: str, wanted: str) -> bool:
    """Wellfound redirects a place it doesn't know to the worldwide role page (/role/<role>, verified 2026-11-13):
    the page must still be the one asked for, or worldwide jobs would pass as local ones."""
    return urlparse(url).path.rstrip("/") == urlparse(wanted).path.rstrip("/")


def _names(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    try:  # Apollo may hand lists over as their string form: "['Dubai', 'Bengaluru']"
        parsed = ast.literal_eval(value) if isinstance(value, str) and value.startswith("[") else value
    except (ValueError, SyntaxError):
        parsed = value
    return [str(v) for v in parsed] if isinstance(parsed, list) else ([str(value)] if value else [])


def data_to_job(item: dict) -> Job | None:
    if not item.get("id"):
        return None
    locations = list(dict.fromkeys(_names(item.get("locations"))))
    if str(item.get("remote")).lower() == "true":
        locations.append("Remote")
    lo, hi = item.get("expMin"), item.get("expMax")
    experience = (f"{lo}-{hi} Yrs" if lo not in (None, "None") and hi not in (None, "None")
                  else f"{lo}+ Yrs" if lo not in (None, "None") else "")
    slug = item.get("slug") or _slug(item.get("title") or "")
    return Job(site="wellfound", site_job_id=str(item["id"]), url=f"{BASE}/jobs/{item['id']}-{slug}",
               title=(item.get("title") or "").strip(), company=(item.get("company") or "").strip(), locations=locations,
               experience=experience, salary=item.get("compensation") or "",
               description=html_to_text(item.get("description") or ""))


OPEN_EVERYWHERE = {"worldwide", "anywhere", "everywhere", "global", "remote"}


def open_to(item: dict, places: list[str]) -> bool:
    """False for a job whose remote hiring is limited to other countries (acceptedRemoteLocationNames, e.g.
    ["United States"]) and that isn't in one of your cities either. Wellfound refuses those applications ("not
    accepting applications from your current location"), so they're dropped before scoring (verified 2026-11-12)."""
    accepted = [n.lower() for n in _names(item.get("remoteOnlyIn"))]
    if not accepted or any(n in OPEN_EVERYWHERE for n in accepted):
        return True
    yours = {alias for place in places for alias in location_aliases(place)}
    listed = " | ".join(accepted + [n.lower() for n in _names(item.get("locations"))])
    return any(re.search(rf"\b{re.escape(alias)}\b", listed) for alias in yours)


class WellfoundAdapter(SiteAdapter):
    name = "wellfound"
    login_url = f"{BASE}/login"

    def __init__(self, page):
        super().__init__(page)
        self.applied: set[tuple[str, str]] | None = None  # (company, title) from your Applied list, read once

    async def _applied_jobs(self) -> set[tuple[str, str]]:
        """(company, title) of every application in Wellfound's Applied list: rows read "<company>", "<title>",
        "<status>" (verified 2026-11-12). Includes applications you made yourself."""
        if self.applied is None:
            self.applied = set()
            try:
                await self.page.goto(f"{BASE}/jobs/applications", wait_until="domcontentloaded")
                await self.page.wait_for_timeout(4000)
                lines = [line.strip() for line in (await self.page.locator("body").inner_text()).split("\n")
                         if line.strip()]
                for i in range(len(lines) - 2):
                    if APPLICATION_STATUS.fullmatch(lines[i + 2]):
                        self.applied.add((lines[i].lower(), lines[i + 1].lower()))
            except PlaywrightError as e:
                log.info(f"  couldn't read the Applied list ({str(e).splitlines()[0][:80]})", extra={"detail": True})
        return self.applied

    async def ensure_logged_in(self) -> bool:
        await self.page.goto(f"{BASE}/jobs", wait_until="domcontentloaded")
        await self.check_blocked()
        await self.page.wait_for_timeout(2500)
        body = await self.page.locator("body").inner_text()
        return not re.search(r"\b(log in|sign up|create profile)\b", body[:600], re.I)

    async def search(self, search: SearchConfig, profile: Profile) -> AsyncIterator[Job]:
        yielded: set[str] = set()
        yours = [profile.home_country, *search.locations]
        unknown_roles: set[str] = set()  # role phrases Wellfound has no role page for
        # cities and countries both have pages (verified 2026-11-13)
        places: list[str | None] = list(search.locations)
        if search.include_remote:
            places.append(None)
        for place in places:
            label = place or "remote"
            unknown_place = False
            for role in search.roles:
                if role in unknown_roles or unknown_place:
                    continue
                for page in range(1, MAX_PAGES + 1):
                    url = search_url(role, place, page)
                    try:
                        await self.page.goto(url, wait_until="domcontentloaded")
                    except PlaywrightError as e:
                        log.info(f"  search: {role} · {label} didn't load ({str(e).splitlines()[0][:80]})",
                                 extra={"detail": True})
                        break
                    await self.check_blocked()
                    if not is_role_page(self.page.url):
                        # verified 2026-11-12: an unknown role slug redirects to the general city page
                        unknown_roles.add(role)
                        log.info(f"  search: Wellfound has no \"{role}\" role page; skipping it here "
                                 f"(use a common title, e.g. Backend Engineer)", extra={"detail": True})
                        break
                    if not is_place_page(self.page.url, url):
                        unknown_place = True
                        log.info(f"  search: Wellfound has no page for {label}; skipping it (check the spelling "
                                 f"in search.yaml locations)", extra={"detail": True})
                        break
                    items = await self.page.evaluate(JOBS_JS) or []
                    new = closed = 0
                    before = self.handled_before
                    for item in items:
                        if (item.get("jobType") or "full-time") != "full-time":
                            continue
                        if not open_to(item, yours):
                            if str(item.get("id")) not in yielded:  # unseen: this page still had something new
                                yielded.add(str(item.get("id")))
                                closed += 1
                            continue
                        job = data_to_job(item)
                        if job and job.site_job_id not in yielded:
                            yielded.add(job.site_job_id)
                            new += 1
                            yield job
                    log.info(f"  search: {role} · {label} · page {page} → {len(items)} jobs, {self.fresh(new, before)}"
                             + (f", {closed} not hiring from {profile.home_country}" if closed else ""),
                             extra={"detail": True})
                    if not new and not closed:
                        break  # past the last page, Wellfound repeats or returns nothing
                    await human_pause(2, 5)

    async def get_details(self, job: Job) -> Job:
        return job  # the listing data has the full description, experience and compensation

    async def apply(self, job: Job, answerer: Answerer, confirm: ConfirmSubmit) -> ApplyResult:
        result = await self._apply(job, answerer, confirm)
        if result.status != Status.APPLIED:
            await self._close_dialog()  # never leave a half-written application open
        return result

    async def _apply(self, job: Job, answerer: Answerer, confirm: ConfirmSubmit) -> ApplyResult:
        if (job.company.strip().lower(), job.title.strip().lower()) in await self._applied_jobs():
            return ApplyResult(Status.SKIPPED, "already applied (in your Wellfound Applied list)")
        await self.page.goto(job.url, wait_until="domcontentloaded")
        await self.check_blocked()
        if "wellfound.com" not in self.page.url:
            return ApplyResult(Status.EXTERNAL, "applies on company site")
        button = None
        for _ in range(JOB_PAGE_WAIT_S * 2):
            await self.page.wait_for_timeout(500)
            if await find_button(self.page, APPLIED_STATE):
                return ApplyResult(Status.SKIPPED, "already applied")
            if button := await find_button(self.page, APPLY_BUTTON):
                break
        if not button:
            return ApplyResult(Status.FAILED, "no apply button", screenshot_path=await self.debug_dump("no-apply"))

        pages_before = set(self.page.context.pages)
        await button.click()
        dialog = None
        for _ in range(20):
            await self.page.wait_for_timeout(500)
            if [p for p in self.page.context.pages if p not in pages_before] or "wellfound.com" not in self.page.url:
                return ApplyResult(Status.EXTERNAL, "applies on company site (Apply opened another site)")
            if await self.page.locator(SELECTORS["dialog"]).count():
                dialog = self.page.locator(SELECTORS["dialog"]).first
                break
        if dialog is None:
            return ApplyResult(Status.FAILED, "no apply dialog after Apply; nothing filled",
                               screenshot_path=await self.debug_dump("no-dialog"))
        text = ""
        for _ in range(DIALOG_LOAD_S * 2):  # wait out the spinner: loaded = it has something to fill or press
            if await dialog.locator("textarea, input:not([type=hidden]), button").count():
                await self.page.wait_for_timeout(500)
                text = (await dialog.inner_text()).lower()
                break
            await self.page.wait_for_timeout(500)
        if not text:
            return ApplyResult(Status.FAILED, "apply dialog didn't load; nothing filled",
                               screenshot_path=await self.debug_dump("dialog-not-loaded"))
        if LOCATION_BLOCKED.search(text):
            return ApplyResult(Status.SKIPPED, "company isn't accepting applications from your location")
        if EXTERNAL_TEXT.search(text):
            return ApplyResult(Status.EXTERNAL, "applies on company site (managed outside Wellfound)")
        if job.company.strip().lower() not in text and job.title.strip().lower() not in text:
            return ApplyResult(Status.FAILED, "apply dialog doesn't mention this job; not filled",
                               screenshot_path=await self.debug_dump("wrong-dialog"))
        return await self._fill_and_send(dialog, job, answerer, confirm)

    async def _fill_and_send(self, dialog, job: Job, answerer: Answerer, confirm: ConfirmSubmit) -> ApplyResult:
        answers: dict[str, Answer] = {}
        llm_moves = 0
        for _ in range(MAX_STEPS):
            if reason := await needs_you_reason(dialog):
                return ApplyResult(Status.SKIPPED, f"needs you: {reason}", answers,
                                   await self.debug_dump("needs-you"), needs_you=reason)
            if not await self._write_note(dialog, job, answerer, answers):
                return ApplyResult(Status.SKIPPED, "couldn't write the note to the hiring team", answers)
            _, unanswered = await fill_step(dialog, answerer, job, answers)
            if unanswered:
                return ApplyResult(Status.SKIPPED, f"couldn't answer: {unanswered.text}", answers,
                                   await self.debug_dump("unanswered"))
            if problem := await fix_rejected_phone(dialog, answers):
                return ApplyResult(Status.FAILED, problem, answers, await self.debug_dump("phone"))
            empty = [f.label for f in await read_fields(dialog) if f.required and not f.filled]
            if empty:
                return ApplyResult(Status.FAILED, f"required field left empty: {empty[0][:80]}", answers,
                                   await self.debug_dump("incomplete"))
            if send := await find_button(dialog, SEND_BUTTON):
                if await send.is_disabled():  # never wait out a 30 s click timeout on a button that can't be pressed
                    errors = await form_errors(dialog)
                    return ApplyResult(Status.FAILED, "send button is disabled"
                                       + (f": {errors[0][:100]}" if errors else ""), answers,
                                       await self.debug_dump("send-disabled"))
                if not await confirm(job, answers):
                    return ApplyResult(Status.SKIPPED, "not submitted", answers)
                await send.click()
                result = await self._await_success(answers)
                if result.status == Status.APPLIED and self.applied is not None:
                    self.applied.add((job.company.strip().lower(), job.title.strip().lower()))
                return result
            if next_button := await find_button(dialog, NEXT_BUTTON):
                await next_button.click()
                await self.page.wait_for_timeout(1200)
                continue
            if llm_moves >= MAX_LLM_MOVES or not (move := await llm_step_move(
                    answerer.llm, dialog, job, await form_errors(dialog))):
                return ApplyResult(Status.FAILED, "unknown apply step", answers, await self.debug_dump("unknown-step"))
            llm_moves += 1
            log.info(f"  🤖 form step: {move.action}" + (f' "{move.label}"' if move.label else ""), extra={"detail": True})
            if move.action == "skip_job":
                return ApplyResult(Status.SKIPPED, f"Wellfound form: {move.why or 'cannot be completed'}", answers)
        return ApplyResult(Status.FAILED, f"apply dialog had more than {MAX_STEPS} steps", answers,
                           await self.debug_dump("too-many-steps"))

    async def _write_note(self, dialog, job: Job, answerer: Answerer, answers: dict[str, Answer]) -> bool:
        """The note to the hiring team: an empty textarea labelled as a note / message / cover letter. Written for
        this job by Answerer.compose. False only when such a box is empty and nothing could be written."""
        for f in await read_fields(dialog):
            if f.filled or f.kind != "textarea" or not NOTE_BOX.search(f"{f.label} {f.placeholder}"):
                continue
            q = Question(f"Write a short note to the hiring team for your application to {job.title} at "
                         f"{job.company}: 3-5 first-person sentences on why you're a good fit, citing the candidate's "
                         f"work and skills that match this job. No greeting line, no sign-off.", QuestionKind.TEXT)
            answer = await answerer.compose(q, job)
            if answer is None:
                return False
            await fill_field(dialog, f, answer.value)
            answers["Note to the hiring team"] = answer
        return True

    async def _await_success(self, answers: dict[str, Answer]) -> ApplyResult:
        for _ in range(20):
            await self.page.wait_for_timeout(1000)
            try:
                body = await self.page.locator("body").inner_text()
                dialog_open = await self.page.locator(SELECTORS["dialog"]).count()
                if SUCCESS_TEXT.search(body) or (not dialog_open and await find_button(self.page, APPLIED_STATE)):
                    shot = await self.screenshot("applied")
                    await self._close_dialog()  # the follow-up offer (AI video interview) is left alone
                    return ApplyResult(Status.APPLIED, answers=answers, screenshot_path=shot)
            except PlaywrightError:
                continue
        return ApplyResult(Status.FAILED, "send did not complete", answers, await self.debug_dump("send"))

    async def _close_dialog(self) -> None:
        try:
            dialog = self.page.locator(SELECTORS["dialog"])
            if await dialog.count():
                close = await find_button(dialog.first, re.compile(r"cancel|close|discard", re.I))
                await (close.click(timeout=3000) if close else self.page.keyboard.press("Escape"))
        except PlaywrightError:
            pass
