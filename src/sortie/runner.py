"""One run: for each site, search → filter → score → apply → record."""

from __future__ import annotations

import re
import time
from collections import Counter

from sortie import terminal
from sortie.browser import Browser, human_pause
from sortie.config import Profile, SearchConfig, load_about
from sortie.db import Database
from sortie.filters import skip_reason
from sortie.llm.answer_book import AnswerBook
from sortie.llm.answerer import Answerer
from sortie.llm.client import LLMClient
from sortie.llm.matcher import score_fit, screen_title
from sortie.llm.roles import suggest_roles
from sortie.log import log
from sortie.models import ApplyResult, Job, Mode, NeedsYou, Status
from sortie.sites import ADAPTERS
from sortie.sites.base import BlockedError, SiteAdapter

LABELS = {
    Status.APPLIED: ("✅", "APPLIED"),
    Status.SKIPPED: ("· ", "SKIPPED"),
    Status.EXTERNAL: ("↗ ", "EXTERNAL"),
    Status.FAILED: ("❌", "FAILED"),
    Status.PENDING_REVIEW: ("⏸ ", "PENDING"),
}
SUMMARY_ORDER = [Status.APPLIED, Status.EXTERNAL, Status.SKIPPED, Status.FAILED, Status.PENDING_REVIEW]


def display_title(job: Job) -> str:
    """Drop a trailing '@ Company' that some postings put in the title."""
    return re.sub(rf"\s*@\s*{re.escape(job.company)}\s*$", "", job.title, flags=re.I) or job.title


class Runner:
    def __init__(self, profile: Profile, search: SearchConfig, db: Database, llm: LLMClient,
                 mode: Mode, headless: bool, limit: int | None = None, book: AnswerBook | None = None):
        self.profile = profile
        self.search = search
        self.db = db
        self.llm = llm
        self.mode = mode
        self.headless = headless
        self.limit = limit
        self.answerer = Answerer(profile, book or AnswerBook(), llm, terminal.ask_user, about=load_about())
        self.summary: dict[str, Counter] = {}
        self.reached_submit = False  # dry runs count a job once it gets as far as the submit step
        self.title_cache: dict[str, str | None] = {}
        self.job_number = 0
        self.run_target = 0  # applications wanted from the current site this run (cap/limit)

    async def run(self, sites: list[str]) -> None:
        started = time.monotonic()
        log.info(f"Mode: {self.mode.value} · LLM: {self.llm.model or 'off (rules + your answers only)'}")
        if extra := await suggest_roles(self.llm, self.profile, self.search, self.answerer.about):
            log.info(f"🤖 AI-suggested roles: {', '.join(extra)}  (ai_roles in search.yaml; 0 = off)")
            self.search = self.search.model_copy(update={"roles": self.search.roles + extra})
        async with Browser(self.headless) as browser:
            for name in sites:
                adapter = ADAPTERS[name](await browser.new_page())
                adapter.attended = self.mode == Mode.REVIEW
                self.summary[name] = Counter()
                try:
                    await self.run_site(adapter)
                except BlockedError as e:
                    log.warning(f"⛔ {e} — stopping {name} for this run")
                    self.summary[name]["blocked"] += 1
        self.log_summary(time.monotonic() - started)

    async def run_site(self, adapter: SiteAdapter) -> None:
        name = adapter.name
        if not await adapter.ensure_logged_in():
            if not await terminal.wait_for_login(name) or not await adapter.ensure_logged_in():
                log.warning(f"skipping {name}: not logged in")
                return

        await adapter.before_jobs()
        self.save_needs_you(adapter)
        cap = self.search.daily_caps.get(name, 20)
        used = self.db.applied_count(name)
        remaining = cap - used
        if self.limit is not None:
            remaining = min(remaining, self.limit)
        log.info(f"\n=== {name} · {used}/{cap} applied today · up to {max(remaining, 0)} this run ===")
        if remaining <= 0:
            log.info("daily cap reached")
        else:
            self.run_target = remaining
            await self.apply_jobs(adapter, remaining)
        await adapter.after_jobs(self.answerer, self.confirm)
        self.save_needs_you(adapter)

    async def apply_jobs(self, adapter: SiteAdapter, remaining: int) -> None:
        name = adapter.name
        self.job_number = 0
        async for job in adapter.search(self.search, self.profile):
            if self.db.is_seen(name, job.site_job_id):
                continue
            if reason := skip_reason(job, self.search, self.profile):
                self.record(job, ApplyResult(Status.SKIPPED, reason))
                continue

            if job.external:  # known from search: no need to screen, open or score it
                self.record(job, ApplyResult(Status.EXTERNAL, "applies on company site"))
                continue
            if reason := await self.title_screen(job):
                self.record(job, ApplyResult(Status.SKIPPED, reason))
                continue

            await human_pause()
            job = await adapter.get_details(job)
            if reason := skip_reason(job, self.search, self.profile):
                self.record(job, ApplyResult(Status.SKIPPED, reason))
                continue
            if job.external:  # found on the job page; scoring it would be wasted
                self.record(job, ApplyResult(Status.EXTERNAL, "applies on company site"))
                continue

            try:
                fit = await score_fit(self.llm, self.profile, job, self.answerer.about)
            except Exception as e:  # bad key / provider down: carry on unscored rather than abort the run
                log.warning(f"LLM fit scoring failed ({type(e).__name__}); continuing without a score")
                fit = None
            if fit and fit.score < self.search.min_fit_score:
                self.record(job, ApplyResult(Status.SKIPPED, fit.reason), fit.score)
                continue

            self.reached_submit = False
            try:
                result = await adapter.apply(job, self.answerer, self.confirm)
            except BlockedError as e:
                if e.result:
                    self.record(job, e.result, fit.score if fit else None)
                raise
            except Exception as e:
                first_line = str(e).strip().splitlines()[0] if str(e).strip() else ""  # not Playwright's call log
                result = ApplyResult(Status.FAILED, f"{type(e).__name__}: {first_line}",
                                     screenshot_path=await adapter.screenshot("error"))
            self.record(job, result, fit.score if fit else None)

            if result.status == Status.APPLIED or (self.mode == Mode.DRY_RUN and self.reached_submit):
                remaining -= 1
                if remaining <= 0:
                    log.info("cap reached for this run")
                    return
            await human_pause()

    async def title_screen(self, job: Job) -> str | None:
        """Skip obviously off-target titles before loading the job page (cached per title for the run)."""
        key = job.title.strip().lower()
        if key not in self.title_cache:
            try:
                self.title_cache[key] = await screen_title(self.llm, self.profile, self.search.roles, job)
            except Exception as e:  # never block a job on a flaky screen; the full fit score still runs
                log.warning(f"title screen failed ({type(e).__name__}); checking the job page instead")
                self.title_cache[key] = None
        return self.title_cache[key]

    async def confirm(self, job: Job, answers: dict) -> bool:
        self.reached_submit = True
        if self.mode == Mode.AUTO:
            return True
        if self.mode == Mode.DRY_RUN:
            await terminal.confirm_preview(job, answers)
            return False
        return await terminal.confirm_submit(job, answers)

    def save_needs_you(self, adapter: SiteAdapter) -> None:
        if self.mode != Mode.DRY_RUN:
            for item in adapter.needs_you:
                self.db.add_needs_you(item)
        adapter.needs_you.clear()

    def record(self, job: Job, result: ApplyResult, fit_score: int | None = None) -> None:
        self.summary[job.site][result.status.value] += 1
        self.job_number += 1
        icon, label = LABELS[result.status]
        fit = f"  [fit {fit_score}]" if fit_score is not None else ""
        applied = self.summary[job.site][Status.APPLIED.value]
        progress = f"{applied}/{self.run_target} ✅" if self.run_target else f"{applied} ✅"
        log.info(f"[{self.job_number:>3} · {progress}] {icon} {label:<8} {job.company} — {display_title(job)}{fit}",
                 extra={"status": result.status.value})
        details = [result.reason] if result.reason else []
        details += [f"Q: {q} → {a.value} ({a.source})" for q, a in result.answers.items()]
        if result.screenshot_path and result.status == Status.FAILED:
            details.append(f"screenshot: {result.screenshot_path}")
        for line in details:
            log.info(f"        {line}", extra={"detail": True})

        if self.mode == Mode.DRY_RUN:
            return  # dry runs leave no trace, so the real run sees the same jobs
        reason = f"fit {fit_score}: {result.reason}" if fit_score is not None and result.reason else result.reason
        answers = {q: a.value for q, a in result.answers.items()}
        self.db.record(job, result.status, reason, fit_score, answers, result.screenshot_path)
        if result.needs_you:
            self.db.add_needs_you(NeedsYou(job.site, result.needs_you, job.title, job.company, job.url))

    def log_summary(self, seconds: float) -> None:
        minutes, secs = divmod(int(seconds), 60)
        log.info(f"\n=== summary · {minutes}m {secs:02d}s ===")
        for site, counts in self.summary.items():
            parts = [f"{LABELS[s][0].strip()} {s.value} {counts[s.value]}" for s in SUMMARY_ORDER if counts[s.value]]
            if counts["blocked"]:
                parts.append("⛔ blocked")
            log.info(f"  {site:<10} " + (" · ".join(parts) or "nothing new"))
