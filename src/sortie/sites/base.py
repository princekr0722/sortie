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

# (job, answers so far) -> submit? Built by the runner from the mode: dry-run says no, auto yes, review asks.
ConfirmSubmit = Callable[[Job, dict], Awaitable[bool]]

BLOCK_PATTERNS = re.compile(
    r"captcha|unusual (activity|traffic)|verify you are (a )?human|are you a robot|access denied|too many requests",
    re.IGNORECASE,
)


class BlockedError(Exception):
    """The site showed a captcha or rate-limit page; stop using it for this run."""


class SiteAdapter(ABC):
    name: str
    login_url: str

    def __init__(self, page: Page):
        self.page = page

    @abstractmethod
    async def ensure_logged_in(self) -> bool: ...

    @abstractmethod
    def search(self, search: SearchConfig, profile: Profile) -> AsyncIterator[Job]: ...

    @abstractmethod
    async def get_details(self, job: Job) -> Job: ...

    @abstractmethod
    async def apply(self, job: Job, answerer: Answerer, confirm: ConfirmSubmit) -> ApplyResult: ...

    async def check_blocked(self) -> None:
        title = await self.page.title()
        body = await self.page.locator("body").inner_text(timeout=5000)
        if BLOCK_PATTERNS.search(title) or BLOCK_PATTERNS.search(body[:3000]):
            await self.screenshot("blocked")
            raise BlockedError(f"{self.name}: blocked ({title!r})")

    async def screenshot(self, tag: str) -> str:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOT_DIR / f"{self.name}-{tag}-{datetime.now():%Y%m%d-%H%M%S}.png"
        await self.page.screenshot(path=path, full_page=False)
        return str(path)

    async def debug_dump(self, tag: str) -> str:
        """Save page HTML + screenshot so unknown page states can be mapped into the adapter's selectors."""
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        (DEBUG_DIR / f"{self.name}-{tag}-{datetime.now():%Y%m%d-%H%M%S}.html").write_text(await self.page.content())
        return await self.screenshot(tag)
