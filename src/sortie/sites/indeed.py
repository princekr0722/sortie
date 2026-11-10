"""Indeed adapter. Search reads the job-card JSON Indeed embeds in its results page; applying walks the
Indeed Apply ("smartapply") form step by step with the generic form filler. Selectors live in SELECTORS."""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlencode

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from sortie.browser import human_pause
from sortie.config import Profile, SearchConfig
from sortie.filters import COUNTRIES, location_aliases
from sortie.llm.answerer import Answerer
from sortie.log import log
from sortie.terminal import notify
from sortie.models import Answer, ApplyResult, Job, Status
from sortie.sites.base import DEBUG_DIR, BlockedError, ConfirmSubmit, SiteAdapter
from sortie.textutil import html_to_text
from sortie.sites.forms import MAX_LLM_MOVES, fill_step, form_errors, llm_step_move
from sortie.sites.forms import find_button as _button

HOME = "in.indeed.com"
# Indeed has one site per country; one account works on all of them
COUNTRY_DOMAINS = {"india": "in.indeed.com", "philippines": "ph.indeed.com", "singapore": "sg.indeed.com",
                   "uae": "ae.indeed.com", "usa": "www.indeed.com", "uk": "uk.indeed.com"}
PAGE_STEP = 10  # `start` moves 10 per page
MAX_PAGES = 3
JOB_AGE_DAYS = 7
MAX_STEPS = 15  # Indeed Apply steps before giving up (contact, resume, questions, experience, review)

SELECTORS = {
    # job page (verified 2026-11-07, logged out). Easily apply = a link straight to the smartapply form;
    # external jobs have a button labelled "Apply on company site"
    "indeed_apply": "a[data-testid='viewjob-indeed-apply']",
    "apply_button": "a[data-testid='viewjob-indeed-apply'], [data-testid='viewjob-apply'], #indeedApplyButton",
    "main_content": "[data-testid='viewjob-main-content']",
    "next_page": "[data-testid='pagination-page-next']",
    # Indeed Apply form (from Indeed's markup conventions; confirm on the first logged-in run)
    "form_frame": "iframe[src*='indeedapply'], iframe[src*='smartapply']",
}
# A step gets up to STEP_CHECKS checks STEP_CHECK_S apart (1 min) to show its buttons; between checks Sortie
# still looks every half second, so a ready step moves on at once.
STEP_CHECK_S = 5
STEP_CHECKS = 12
STEP_MAX_S = STEP_CHECK_S * STEP_CHECKS
# anything that means the step has rendered (Sortie's own fields or the step's buttons)
# every step ends in one of these; while the questions load only a spinner (and the job card) shows
# step buttons, matched on their whole label: "Continue" / "Review your application" move on, Submit sends
NEXT_BUTTON = re.compile(r"continue|next|review( your application)?", re.I)
SUBMIT_BUTTON = re.compile(r"submit( your)? application|submit", re.I)
# checked only after Submit is clicked: the review page's own text ("…after you submit", the job card) must not count
SUBMITTED_TEXT = re.compile(r"your application has been (submitted|sent)|application (submitted|sent)!?$", re.I | re.M)
SUBMITTED_URL = "post-apply"
# Indeed can put a reCAPTCHA ("I'm not a robot" + image challenge) in front of Submit. Sortie never solves it:
# it beeps / notifies and waits for you to solve it in the bot's window, in any mode. If nobody does,
# an unattended (auto) run stops Indeed rather than wait on every job.
CAPTCHA_WAIT_S = 180
CAPTCHA_CHECKBOX = "iframe[title='reCAPTCHA']:not([src*='size=invisible'])"
CAPTCHA_CHALLENGE = "iframe[title*='challenge' i]"
ALREADY_APPLIED_TEXT = re.compile(r"already applied", re.I)
# Indeed's "intervention" step after screening answers that miss a hard requirement. Sortie doesn't "Apply anyway":
# the employer filters on it, so the application would most likely be wasted.
UNMET_TEXT = re.compile(r"don.t meet (these|the) employer(.?s)? requirements", re.I)
UNMET_ITEM = re.compile(r"^(.+?)\s*\(required\)\s*$", re.I | re.M)

CARDS_JS = """() => window.mosaic?.providerData?.['mosaic-provider-jobcards']
  ?.metaData?.mosaicProviderJobCardsModel?.results || null"""
DESCRIPTION_JS = """() => {
  const find = (o, depth) => {
    if (!o || typeof o !== 'object' || depth > 8) return null;
    if (typeof o.sanitizedJobDescription === 'string') return o.sanitizedJobDescription;
    for (const v of Object.values(o)) { const r = find(v, depth + 1); if (r) return r; }
    return null;
  };
  try { return find(window._initialData, 0); } catch (e) { return null; }
}"""


def search_targets(locations: list[str]) -> list[tuple[str, str, str]]:
    """(location as configured, Indeed site, `l=` value). Cities search India's site; a country searches
    its own site without a city."""
    targets = []
    for location in locations:
        place = location_aliases(location)[0]
        if place in COUNTRIES:
            if place in COUNTRY_DOMAINS:
                targets.append((location, COUNTRY_DOMAINS[place], ""))
            else:
                log.info(f"  no Indeed site known for {location}; not searching it", extra={"detail": True})
        else:
            targets.append((location, HOME, location))
    return targets


def search_url(domain: str, role: str, location: str, start: int = 0) -> str:
    # iafilter=1 is Indeed's "Easily apply on Indeed" filter: company-site jobs can't be applied to here
    query = {"q": role, "l": location, "fromage": JOB_AGE_DAYS, "iafilter": 1}
    if start:
        query["start"] = start
    return f"https://{domain}/jobs?{urlencode(query)}"


def card_to_job(card: dict, domain: str) -> Job | None:
    jk = card.get("jobkey")
    if not jk:
        return None
    locations = [card["formattedLocation"]] if card.get("formattedLocation") else []
    if card.get("remoteLocation"):
        locations.append("Remote")
    return Job(
        site="indeed",
        site_job_id=jk,
        url=f"https://{domain}/viewjob?jk={jk}",
        title=card.get("displayTitle") or card.get("title") or "",
        company=card.get("company") or "",
        locations=locations,
        salary=(card.get("salarySnippet") or {}).get("text") or "",
        description=html_to_text(card.get("snippet") or ""),
        external=not card.get("indeedApplyable", False),  # no "Easily apply": the company's own site
    )


class IndeedAdapter(SiteAdapter):
    name = "indeed"
    login_url = "https://secure.indeed.com/auth"

    async def ensure_logged_in(self) -> bool:
        await self.page.goto("https://myjobs.indeed.com/applied", wait_until="domcontentloaded")
        await self.page.wait_for_timeout(3000)
        await self.check_blocked()
        return "secure.indeed.com/auth" not in self.page.url

    async def search(self, search: SearchConfig, profile: Profile) -> AsyncIterator[Job]:
        yielded: set[str] = set()
        for location, domain, where in search_targets(search.locations):
            for role in search.roles:
                for page in range(MAX_PAGES):
                    await self.page.goto(search_url(domain, role, where, page * PAGE_STEP),
                                         wait_until="domcontentloaded")
                    await self.check_blocked()
                    if "secure.indeed.com/auth" in self.page.url:  # logged out: Indeed shows only page 1
                        log.warning("indeed: sign-in needed for more result pages (run `sortie login indeed`)")
                        break
                    cards = await self._cards()
                    if cards is None:
                        log.warning(f"indeed: no job list on the results page for {role} · {location}")
                        await self.debug_dump("no-cards")
                        break
                    new = applied = external = 0
                    for card in cards:
                        if card.get("appliedOrGreater"):
                            applied += 1
                            continue
                        job = card_to_job(card, domain)
                        if job and job.external:  # iafilter doesn't remove them all; company-site applying is phase 3
                            external += 1
                            continue
                        if job and job.site_job_id not in yielded:
                            yielded.add(job.site_job_id)
                            new += 1
                            yield job
                    notes = [f"{applied} already applied"] * bool(applied) + [f"{external} on company sites, left out"] * bool(external)
                    note = f" ({', '.join(notes)})" if notes else ""
                    log.info(f"  search: {role} · {location} · page {page + 1} → {len(cards)} jobs, {new} new{note}",
                             extra={"detail": True})
                    # Indeed repeats results past the last page, so a page with nothing new ends the search
                    if not new and not external or not await self.page.locator(SELECTORS["next_page"]).count():
                        break
                    await human_pause(2, 5)

    async def _cards(self) -> list[dict] | None:
        for _ in range(10):
            cards = await self.page.evaluate(CARDS_JS)
            if cards is not None:
                return cards
            await self.page.wait_for_timeout(1000)
        return None

    async def get_details(self, job: Job) -> Job:
        await self.page.goto(job.url, wait_until="domcontentloaded")
        await self.check_blocked()
        try:
            await self.page.wait_for_selector(SELECTORS["apply_button"], timeout=10000)
        except PlaywrightTimeout:
            await self.check_blocked()
        description = await self.page.evaluate(DESCRIPTION_JS)
        if description:
            job.description = html_to_text(description)
        elif await self.page.locator(SELECTORS["main_content"]).count():
            job.description = (await self.page.locator(SELECTORS["main_content"]).first.inner_text()).strip()
        if not await self.page.locator(SELECTORS["indeed_apply"]).count():
            button = self.page.locator(SELECTORS["apply_button"])
            if await button.count() and "company" in (await button.first.inner_text()).lower():
                job.external = True
        return job

    async def apply(self, job: Job, answerer: Answerer, confirm: ConfirmSubmit) -> ApplyResult:
        if job.external:
            return ApplyResult(Status.EXTERNAL, "applies on company site")
        if not self.page.url.startswith(job.url):
            await self.get_details(job)
        button = self.page.locator(SELECTORS["apply_button"])
        if not await button.count():
            if await _button(self.page, re.compile(r"applied", re.I)):  # greyed-out "Applied" (verified 2026-11-07)
                return ApplyResult(Status.SKIPPED, "already applied")
            return ApplyResult(Status.FAILED, "no apply button", screenshot_path=await self.debug_dump("no-apply"))
        label = (await button.first.inner_text()).strip().lower()
        if "applied" in label:
            return ApplyResult(Status.SKIPPED, "already applied")
        if "company" in label:
            return ApplyResult(Status.EXTERNAL, "applies on company site")

        # Apply now opens the form; nothing is sent until its review page's Submit, which is confirmed
        job_page = self.page
        # failed requests and page errors while the form is open: the likely cause when a step never loads
        self.net_errors: list[str] = []
        def on_response(r):
            if r.status >= 400:
                self.net_errors.append(f"{r.status} {r.url.split('?')[0]}")
        def on_failed(r):
            self.net_errors.append(f"failed ({r.failure}) {r.url.split('?')[0]}")
        def on_console(m):
            if m.type == "error":
                self.net_errors.append(f"console: {m.text[:300]}")
        def on_page_error(e):
            self.net_errors.append(f"page error: {str(e)[:300]}")
        job_page.context.on("response", on_response)
        job_page.context.on("requestfailed", on_failed)
        # click it like a person (opening its href directly left the form steps blank)
        try:
            async with job_page.context.expect_page(timeout=8000) as opened:
                await button.first.click()
            form_page = await opened.value
        except PlaywrightTimeout:
            form_page = job_page  # opened in the same tab (or an embedded frame)
        form_page.on("console", on_console)
        form_page.on("pageerror", on_page_error)
        self.page = form_page  # so screenshots / debug dumps capture the form
        try:
            await form_page.wait_for_load_state("domcontentloaded")
            return await self._fill_form(job, answerer, confirm)
        finally:
            job_page.context.remove_listener("response", on_response)
            job_page.context.remove_listener("requestfailed", on_failed)
            form_page.remove_listener("console", on_console)
            form_page.remove_listener("pageerror", on_page_error)
            self.page = job_page
            if form_page is not job_page:
                await form_page.close()

    async def _scope(self) -> Page | Frame:
        frame_el = self.page.locator(SELECTORS["form_frame"])
        if await frame_el.count():
            frame = await frame_el.first.element_handle()
            if frame and (content := await frame.content_frame()):
                return content
        return self.page

    async def _wait_for_step(self) -> tuple[Page | Frame, bool]:
        """Wait (up to STEP_MAX_S) until the step shows a button to press: Submit (even disabled, e.g. behind a
        CAPTCHA), or Continue / Review once no button is busy (spinner) loading the next step."""
        scope = await self._scope()
        for _ in range(STEP_MAX_S * 2):
            await self.page.wait_for_timeout(500)
            scope = await self._scope()
            try:
                if SUBMITTED_URL in self.page.url or await _button(scope, SUBMIT_BUTTON):
                    return scope, True
                if await _button(scope, NEXT_BUTTON) and not await self._loading(scope):
                    await self.page.wait_for_timeout(500)  # let the rest of the step render
                    return scope, True
            except PlaywrightError:  # navigating between steps
                continue
        return scope, False

    async def _loading(self, scope: Page | Frame) -> bool:
        # only a busy button: Indeed leaves <html class="loading"> on finished steps too, so that isn't a signal
        return bool(await scope.locator("button[aria-busy='true']:visible").count())

    async def _wait_while_busy(self, url: str) -> None:
        """After Continue, Indeed shows a spinner in the button (aria-busy) while it loads the next step.
        Wait until the page moves on or the button is idle again (e.g. a validation error)."""
        await self.page.wait_for_timeout(800)
        for _ in range(STEP_MAX_S * 2):
            if self.page.url != url:
                return
            try:
                if not await (await self._scope()).locator("button[aria-busy='true']").count():
                    return
            except PlaywrightError:  # navigating
                return
            await self.page.wait_for_timeout(500)

    async def _fill_form(self, job: Job, answerer: Answerer, confirm: ConfirmSubmit) -> ApplyResult:
        answers: dict[str, Answer] = {}
        last_step = None
        recheck = ""
        llm_moves = 0
        for _ in range(MAX_STEPS):
            await self.check_blocked()
            scope, ready = await self._wait_for_step()
            if not ready:
                step_name = self.page.url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
                path = await self.debug_dump("not-loading")
                (DEBUG_DIR / (Path(path).stem + "-errors.txt")).write_text("\n".join(self.net_errors) or "(none)")
                return ApplyResult(Status.FAILED, f"form step '{step_name}' never finished loading ({STEP_CHECKS} checks over {STEP_MAX_S} s); "
                                   f"{len(self.net_errors)} request/page errors saved next to the debug dump",
                                   answers, path)
            body = await scope.locator("body").inner_text()
            if SUBMITTED_URL in self.page.url:  # Indeed went to its confirmation page; never decided from text here
                return ApplyResult(Status.APPLIED, answers=answers, screenshot_path=await self.screenshot("applied"))
            if ALREADY_APPLIED_TEXT.search(body):
                return ApplyResult(Status.SKIPPED, "already applied", answers)
            if UNMET_TEXT.search(body):
                return ApplyResult(Status.SKIPPED, unmet_reason(body), answers)

            fields, unanswered = await fill_step(scope, answerer, job, answers)
            if unanswered:
                return ApplyResult(Status.SKIPPED, f"couldn't answer: {unanswered.text}", answers,
                                   await self.debug_dump("unanswered"))

            submit = await _button(scope, SUBMIT_BUTTON)
            if submit:
                if not await confirm(job, answers):
                    return ApplyResult(Status.SKIPPED, "not submitted", answers)
                return await self._submit(job, answers)

            next_button = await _button(scope, NEXT_BUTTON)
            step = (self.page.url, tuple(f.label for f in fields))
            if next_button and step != last_step:  # the usual case: rules know how to move on
                last_step = step
                await next_button.click()
                await self._wait_while_busy(step[0])
                continue
            if not next_button and recheck != self.page.url:  # caught mid-transition: look again first
                recheck = self.page.url
                continue

            # no recognised button, or clicking it didn't move on: let the LLM pick the next move
            errors = await form_errors(scope)
            problem = "unknown form step" if not next_button else "stuck on a form step"
            # stuck with the step's own button: clicking it again won't help, and one look is enough
            avoid = (await next_button.inner_text()).strip() if next_button else ""
            allowed = llm_moves < (1 if next_button else MAX_LLM_MOVES)
            move = await llm_step_move(answerer.llm, scope, job, errors, avoid) if allowed else None
            if move is None:
                reason = f"{problem} ({self.page.url.split('?')[0]})" + (f": {'; '.join(errors)}" if errors else "")
                return ApplyResult(Status.FAILED, reason, answers, await self.debug_dump(problem.split()[0]))
            llm_moves += 1
            log.info(f"  🤖 form step: {move.action}" + (f' "{move.label}"' if move.label else "")
                     + (f" ({move.why})" if move.why else ""), extra={"detail": True})
            if move.action == "skip_job":
                return ApplyResult(Status.SKIPPED, f"Indeed form: {move.why or 'cannot be completed'}", answers,
                                   await self.debug_dump("llm-skip"))
            if move.action == "click":
                await self._wait_while_busy(step[0])
            last_step = None  # judge the next step afresh
        return ApplyResult(Status.FAILED, f"form had more than {MAX_STEPS} steps", answers,
                           await self.debug_dump("too-many-steps"))

    async def _captcha_pending(self) -> bool:
        """An image challenge is open, or an "I'm not a robot" box is shown and not ticked yet."""
        page = self.page
        for i in range(await page.locator(CAPTCHA_CHALLENGE).count()):
            if await page.locator(CAPTCHA_CHALLENGE).nth(i).is_visible():
                return True
        boxes = page.locator(CAPTCHA_CHECKBOX)
        for i in range(await boxes.count()):
            if not await boxes.nth(i).is_visible():
                continue
            anchor = page.frame_locator(CAPTCHA_CHECKBOX).nth(i).locator("#recaptcha-anchor")
            try:
                if await anchor.get_attribute("aria-checked", timeout=2000) != "true":
                    return True
            except PlaywrightError:
                return True
        return False

    async def _submit(self, job: Job, answers: dict[str, Answer]) -> ApplyResult:
        """Click Submit (confirmed already) and wait for Indeed's confirmation. If a CAPTCHA is in the way,
        hand it to the person at the screen and click Submit once it's solved."""
        told = False
        last_click = -10
        job_label = f"{job.title} @ {job.company}"
        for tick in range(CAPTCHA_WAIT_S * 2):
            try:
                body = await (await self._scope()).locator("body").inner_text()
                if SUBMITTED_URL in self.page.url or SUBMITTED_TEXT.search(body):
                    return ApplyResult(Status.APPLIED, answers=answers,
                                       screenshot_path=await self.screenshot("applied"))
                if await self._captcha_pending():
                    if not told:
                        log.info("  🧩 Indeed wants a CAPTCHA: solve it in the browser window "
                                 f"(waiting up to {CAPTCHA_WAIT_S // 60} min; Sortie clicks Submit after)")
                        notify("Sortie: CAPTCHA", f"Solve it in the browser to submit {job_label}")
                        told = True
                elif tick - last_click >= 20:  # re-click at most every 10 s, e.g. after the CAPTCHA is solved
                    submit = await _button(await self._scope(), SUBMIT_BUTTON)
                    if submit and await submit.is_enabled():
                        last_click = tick
                        await submit.click(timeout=5000)
                    elif not submit and tick > 40:
                        break  # Submit is gone but no confirmation either
            except PlaywrightError:  # navigating to the confirmation, or the click was intercepted
                pass
            await self.page.wait_for_timeout(500)
        if told:
            result = ApplyResult(Status.FAILED, "CAPTCHA not solved in time", answers, await self.screenshot("captcha"))
            if not self.attended:  # probably nobody at the screen: don't wait on every job
                raise BlockedError(f"indeed: CAPTCHA not solved within {CAPTCHA_WAIT_S // 60} min", result)
            return result
        return ApplyResult(Status.FAILED, "submit did not complete", answers, await self.debug_dump("submit"))


def unmet_reason(body: str) -> str:
    items = ", ".join(UNMET_ITEM.findall(body)[:5])
    return "Indeed: doesn't meet employer requirements" + (f" ({items})" if items else "")
