"""Instahyre adapter. Search uses Instahyre's public JSON API; applying uses the job page.
Selectors live in SELECTORS so site changes are a one-place fix."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from urllib.parse import urlencode

from playwright.async_api import TimeoutError as PlaywrightTimeout

from sortie.browser import human_pause
from sortie.config import Profile, SearchConfig
from sortie.llm.answerer import Answerer
from sortie.log import log
from sortie.models import ApplyResult, Job, Status
from sortie.sites.base import ConfirmSubmit, SiteAdapter
from sortie.sites.instahyre_inbox import InstahyreInbox

BASE = "https://www.instahyre.com"
PAGE_SIZE = 35  # the API's fixed page size
MAX_PAGES = 3

SELECTORS = {
    # job page (verified 2026-11-05, logged out)
    "jd": ".profile-content.job-description",
    "experience": ".job-locations .experience",
    "location": ".job-locations .location-span",
    # logged in (verified 2026-11-05): "Apply now" calls submitChoice(); logged out it's #job_details_top
    "apply_button": ".apply-button button[ng-click^='submitChoice'], #job_details_top",
    "already_applied": ".apply-button .application-sent",  # "Application sent! on <date>"
    # after applying: "Application sent to <company>. Want to apply to other similar jobs?" (verified, logged out markup)
    "bulk_modal": ".apply-all-modal",
    "bulk_cancel": ".apply-all-modal button[ng-click='applyBulkCancel()']",
}
APPLIED_TEXT = re.compile(r"application sent|already applied|^applied$", re.I)


OPPORTUNITIES_LIMIT = 30


def opportunities_url(offset: int = 0) -> str:
    """Jobs Instahyre recommends to you (the Opportunities page); needs login. interest_facet=0 = not acted on yet."""
    query = {"companies": "", "company_size": "", "industry_type": "", "interest_facet": 0, "job_categories": "",
             "job_functions": "", "job_type": "", "limit": OPPORTUNITIES_LIMIT, "location": "", "offset": offset}
    return f"{BASE}/api/v1/candidate_opportunities/candidate_matching?{urlencode(query)}"


def opportunity_to_job(opp: dict) -> Job:
    """An Opportunities entry wraps the same job as search results (same id and job page)."""
    job = dict(opp.get("job") or {})
    job.setdefault("employer", opp.get("employer") or {})
    if not job.get("public_url") and job.get("opportunity_url"):
        job["public_url"] = BASE + job["opportunity_url"]
    if isinstance(job.get("keywords"), str):  # sent as a Python-style list string: "['Java', 'SQL']"
        job["keywords"] = re.findall(r"'([^']+)'", job["keywords"])
    return api_to_job(job)


def search_url(role: str, years: int, offset: int = 0) -> str:
    # `locations` is ignored by the API, so location filtering happens in filters.skip_reason
    query = {"skills": role.lower(), "years": years, "company_size": 0, "job_type": 0, "offset": offset}
    return f"{BASE}/api/v1/job_search?{urlencode(query)}"


def api_to_job(item: dict) -> Job:
    employer = item.get("employer") or {}
    keywords = item.get("keywords") or []
    return Job(
        site="instahyre",
        site_job_id=str(item["id"]),
        url=item.get("public_url") or f"{BASE}/job-{item['id']}/",
        title=item.get("candidate_title") or item.get("title") or "",
        company=employer.get("company_name", ""),
        locations=[loc.strip() for loc in (item.get("locations") or "").split(",") if loc.strip()],
        description="Key skills: " + ", ".join(keywords) if keywords else "",
    )


class InstahyreAdapter(SiteAdapter):
    name = "instahyre"
    login_url = f"{BASE}/login/"

    def __init__(self, page):
        super().__init__(page)
        self.external: dict[str, str] = {}  # job id -> company-site apply URL ("sourced" jobs)
        self.inbox = InstahyreInbox(page, self.needs_you)

    async def before_jobs(self) -> None:
        await self.inbox.scan()

    async def after_jobs(self, answerer: Answerer, confirm: ConfirmSubmit) -> None:
        await self.inbox.answer_all(answerer, confirm, self.debug_dump)

    async def ensure_logged_in(self) -> bool:
        await self.page.goto(f"{BASE}/candidate/opportunities/", wait_until="domcontentloaded")
        await self.page.wait_for_timeout(4000)
        await self.check_blocked()
        return "login" not in self.page.url

    async def search(self, search: SearchConfig, profile: Profile) -> AsyncIterator[Job]:
        """Instahyre's recommended opportunities first, then a search per role."""
        yielded: set[str] = set()
        async for item in self._opportunities():
            job = opportunity_to_job(item)
            if job.site_job_id not in yielded:
                yielded.add(job.site_job_id)
                if (item.get("job") or {}).get("is_sourced"):
                    self.external[job.site_job_id] = ""
                    job.external = True
                yield job

        years = profile.total_experience().years
        for role in search.roles:
            for page in range(MAX_PAGES):
                response = await self.page.request.get(search_url(role, years, page * PAGE_SIZE))
                if not response.ok:
                    log.warning(f"instahyre search failed ({response.status}) for {role}")
                    break
                items = (await response.json()).get("objects", [])
                log.info(f"  search: {role} · page {page + 1} → {len(items)} jobs", extra={"detail": True})
                for item in items:
                    job = api_to_job(item)
                    if job.site_job_id in yielded:
                        continue
                    yielded.add(job.site_job_id)
                    if item.get("is_sourced"):
                        self.external[job.site_job_id] = item.get("sourced_apply_url") or ""
                        job.external = True
                    yield job
                if len(items) < PAGE_SIZE:
                    break
                await human_pause(2, 5)

    async def _opportunities(self) -> AsyncIterator[dict]:
        for page in range(MAX_PAGES):
            response = await self.page.request.get(opportunities_url(page * OPPORTUNITIES_LIMIT))
            if not response.ok:
                log.warning(f"instahyre opportunities failed ({response.status})")
                return
            data = await response.json()
            items = data.get("objects", [])
            log.info(f"  opportunities (recommended by Instahyre) · page {page + 1} → {len(items)} jobs",
                     extra={"detail": True})
            for item in items:
                yield item
            if not (data.get("meta") or {}).get("next"):
                return

    async def get_details(self, job: Job) -> Job:
        await self.page.goto(job.url, wait_until="domcontentloaded")
        await self.check_blocked()  # waits out a Cloudflare check first
        try:
            await self.page.wait_for_selector(SELECTORS["jd"], timeout=10000)
        except PlaywrightTimeout:
            await self.check_blocked()
            return job
        skills = job.description
        job.description = (await self.page.locator(SELECTORS["jd"]).first.inner_text()).strip()
        if skills:
            job.description += "\n\n" + skills
        experience = self.page.locator(SELECTORS["experience"])
        if await experience.count():
            job.experience = (await experience.first.inner_text()).strip()
        return job

    async def apply(self, job: Job, answerer: Answerer, confirm: ConfirmSubmit) -> ApplyResult:
        if job.site_job_id in self.external:
            return ApplyResult(Status.EXTERNAL, "applies on company site")
        if not self.page.url.startswith(job.url.split("?")[0]):
            await self.get_details(job)

        if await self.page.locator(SELECTORS["already_applied"]).count():
            return ApplyResult(Status.SKIPPED, "already applied")
        button = self.page.locator(SELECTORS["apply_button"])
        if not await button.count():
            return ApplyResult(Status.FAILED, "no apply button", screenshot_path=await self.debug_dump("no-apply"))
        if APPLIED_TEXT.search((await button.first.inner_text()).strip()):
            return ApplyResult(Status.SKIPPED, "already applied")

        if not await confirm(job, {}):
            return ApplyResult(Status.SKIPPED, "not submitted")
        # a wrapping <a> covers the button and swallows real clicks, so fire Angular's ng-click directly
        await button.first.dispatch_event("click")

        for _ in range(10):
            await self.page.wait_for_timeout(1500)
            modal = self.page.locator(SELECTORS["bulk_modal"])
            if await modal.count() and await modal.first.is_visible():
                # never bulk-apply to the "similar jobs" it offers
                await self.page.locator(SELECTORS["bulk_cancel"]).first.click()
                return ApplyResult(Status.APPLIED, screenshot_path=await self.screenshot("applied"))
            if await self.page.locator(SELECTORS["already_applied"]).count():
                return ApplyResult(Status.APPLIED, screenshot_path=await self.screenshot("applied"))
        # an unknown step (questions, a resume prompt, ...): save it so it can be mapped
        return ApplyResult(Status.FAILED, "apply did not complete", screenshot_path=await self.debug_dump("stuck"))
