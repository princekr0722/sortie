"""Interface every job site adapter implements."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime

from playwright.async_api import Page

from sortie.config import PROJECT_ROOT, Profile, SearchConfig
from sortie.llm.answerer import Answerer
from sortie.models import ApplyResult, Job

SCREENSHOT_DIR = PROJECT_ROOT / "data" / "screenshots"
DEBUG_DIR = PROJECT_ROOT / "data" / "debug"
SCREENSHOT_QUALITY = 60  # JPEG, 0-100

# (job, answers so far) -> submit? Built by the runner from the mode: dry-run says no, auto yes, review asks.
ConfirmSubmit = Callable[[Job, dict], Awaitable[bool]]

BLOCK_PATTERNS = re.compile(
    r"captcha|unusual (activity|traffic)|verify you are (a )?human|are you a robot|access denied|too many requests"
    r"|performing security verification|not a bot|additional verification required",
    re.IGNORECASE,
)


CHALLENGE_TITLE = re.compile(r"just a moment|attention required", re.IGNORECASE)
# footers like "This site is protected by reCAPTCHA" on normal pages (Indeed Apply) are not a challenge
CAPTCHA_FOOTER = re.compile(r"protected by recaptcha", re.IGNORECASE)


class BlockedError(Exception):
    """The site showed a captcha or rate-limit page; stop using it for this run.
    `result` is the outcome of the job in progress, so the runner can still record it."""

    def __init__(self, message: str, result: ApplyResult | None = None):
        super().__init__(message)
        self.result = result


class SiteAdapter(ABC):
    name: str
    login_url: str

    def __init__(self, page: Page):
        self.page = page
        self.attended = False  # someone is at the screen (review mode): CAPTCHAs can be handed to them

    @abstractmethod
    async def ensure_logged_in(self) -> bool: ...

    @abstractmethod
    def search(self, search: SearchConfig, profile: Profile) -> AsyncIterator[Job]: ...

    @abstractmethod
    async def get_details(self, job: Job) -> Job: ...

    @abstractmethod
    async def apply(self, job: Job, answerer: Answerer, confirm: ConfirmSubmit) -> ApplyResult: ...

    async def before_jobs(self) -> None:
        """Optional site work before applying (e.g. reading the inbox)."""

    async def after_jobs(self, answerer: Answerer, confirm: ConfirmSubmit) -> None:
        """Optional site work after applying (e.g. answering recruiter questionnaires)."""

    async def check_blocked(self) -> None:
        await self._wait_out_challenge()
        title = await self.page.title()
        body = CAPTCHA_FOOTER.sub("", await self.page.locator("body").inner_text(timeout=5000))
        if BLOCK_PATTERNS.search(title) or BLOCK_PATTERNS.search(body[:3000]):
            await self.screenshot("blocked")
            raise BlockedError(f"{self.name}: blocked ({title!r})")

    async def _wait_out_challenge(self, seconds: int = 20) -> None:
        """Cloudflare's "Just a moment..." check usually clears by itself in a real (visible) browser."""
        for _ in range(seconds):
            if not CHALLENGE_TITLE.search(await self.page.title()):
                return
            await self.page.wait_for_timeout(1000)

    async def screenshot(self, tag: str) -> str:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOT_DIR / f"{self.name}-{tag}-{datetime.now():%Y%m%d-%H%M%S}.jpg"
        # CSS-pixel scale (not 2x on retina) + JPEG: readable for checking a run, ~5x smaller than PNG
        await self.page.screenshot(path=path, full_page=False, type="jpeg", quality=SCREENSHOT_QUALITY, scale="css")
        return str(path)

    async def debug_dump(self, tag: str) -> str:
        """Save page HTML + screenshot so unknown page states can be mapped into the adapter's selectors."""
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        (DEBUG_DIR / f"{self.name}-{tag}-{datetime.now():%Y%m%d-%H%M%S}.html").write_text(
            f"<!-- url: {self.page.url} -->\n" + await self.page.content())
        return await self.screenshot(tag)
