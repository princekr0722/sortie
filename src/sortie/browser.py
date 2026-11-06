"""Persistent Chrome session with human-like pacing."""

from __future__ import annotations

import asyncio
import random

from playwright.async_api import BrowserContext, Locator, Page, async_playwright

from sortie.config import PROJECT_ROOT

PROFILE_DIR = PROJECT_ROOT / "browser-profile"


class Browser:
    """`async with Browser(headless) as browser:` — keeps logins in browser-profile/ between runs."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.context: BrowserContext | None = None

    async def __aenter__(self) -> Browser:
        self._pw = await async_playwright().start()
        self.context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            channel="chrome",  # installed Chrome, not bundled Chromium: fewer bot flags
            headless=self.headless,
            viewport=None,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        return self

    async def __aexit__(self, *exc) -> None:
        await self.context.close()
        await self._pw.stop()

    async def new_page(self) -> Page:
        return await self.context.new_page()


# seconds between page loads; slower looks more human, faster gets through more jobs
PACES = {"careful": (5, 12), "normal": (2, 6), "fast": (1, 3)}
_pace = PACES["normal"]


def set_pace(name: str) -> None:
    global _pace
    _pace = PACES[name]


async def human_pause(low: float | None = None, high: float | None = None) -> None:
    """Random pause; defaults to the run's pace, explicit bounds are scaled by it relative to "normal"."""
    if low is None or high is None:
        low, high = _pace
    else:
        factor = _pace[1] / PACES["normal"][1]
        low, high = low * factor, high * factor
    await asyncio.sleep(random.uniform(low, high))


async def human_type(field: Locator, text: str) -> None:
    await field.click()
    await field.fill("")
    await field.press_sequentially(text, delay=random.randint(40, 120))
