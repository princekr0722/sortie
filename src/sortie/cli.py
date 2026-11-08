"""sortie init | run | login | report"""

from __future__ import annotations

import argparse
import asyncio
import shutil
from datetime import date

from dotenv import load_dotenv

from sortie import terminal
from sortie.browser import Browser, set_pace
from sortie.config import CONFIG_DIR, PROJECT_ROOT, load_profile, load_search
from sortie.db import Database
from sortie.llm.client import LLMClient
from sortie.log import log, setup_logging
from sortie.models import Mode
from sortie.runner import Runner
from sortie.sites import ADAPTERS, DISABLED


def _sites(value: str) -> list[str]:
    names = [s.strip().lower() for s in value.split(",") if s.strip()]
    for name in names:
        if name in DISABLED:
            raise argparse.ArgumentTypeError(f"{name} is disabled: {DISABLED[name][1]}")
    unknown = [n for n in names if n not in ADAPTERS]
    if unknown:
        available = ", ".join(ADAPTERS) or "none yet"
        raise argparse.ArgumentTypeError(f"unknown site(s) {', '.join(unknown)}; available: {available}")
    return names


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sortie")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create your config files from the examples")

    run = sub.add_parser("run", help="search and apply")
    run.add_argument("--sites", type=_sites, default=None, help="comma-separated, default: all")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--review", dest="mode", action="store_const", const=Mode.REVIEW, help="ask before each submit")
    mode.add_argument("--auto", dest="mode", action="store_const", const=Mode.AUTO, help="submit without asking")
    mode.add_argument("--dry-run", dest="mode", action="store_const", const=Mode.DRY_RUN, help="never submit")
    headless = run.add_mutually_exclusive_group()
    headless.add_argument("--headless", dest="headless", action="store_true", default=None)
    headless.add_argument("--visible", dest="headless", action="store_false")
    run.add_argument("--limit", type=int, help="max applications per site this run")
    run.add_argument("--pace", choices=["careful", "normal", "fast"], help="pauses between pages (default: search.yaml)")

    login = sub.add_parser("login", help="open a site to log in manually once")
    login.add_argument("site", choices=sorted(ADAPTERS) or None)

    retry = sub.add_parser("retry", help="give a site's failed jobs fresh attempts (after a fix)")
    retry.add_argument("site", choices=sorted(ADAPTERS) or None)

    report = sub.add_parser("report", help="application counts")
    report.add_argument("--date", type=date.fromisoformat, default=None, help="YYYY-MM-DD, default: today")
    return parser


async def _run(args) -> None:
    search = load_search()
    mode = args.mode or Mode(search.mode)
    headless = search.headless if args.headless is None else args.headless
    set_pace(args.pace or search.pace)
    log_path = setup_logging()
    db = Database()
    try:
        runner = Runner(load_profile(), search, db, LLMClient(), mode, headless, args.limit)
        await runner.run(args.sites or list(ADAPTERS))
    finally:
        db.close()
        log.info(f"  full log: {log_path.relative_to(PROJECT_ROOT)}")


async def _login(args) -> None:
    async with Browser(headless=False) as browser:
        page = await browser.new_page()
        await page.goto(ADAPTERS[args.site].login_url)
        await terminal.prompt(f"Log in to {args.site} in the browser window, then press Enter to save the session: ")


def _report(args) -> None:
    day = args.date or date.today()
    db = Database()
    rows = db.status_counts(day)
    db.close()
    print(f"Applications on {day}:")
    if not rows:
        print("  none")
    for site, status, count in rows:
        print(f"  {site:<10} {status:<15} {count}")


EXAMPLES = {"profile.example.yaml": "profile.yaml", "search.example.yaml": "search.yaml",
            "about.example.md": "about.md", ".env.example": ".env"}


def _init() -> None:
    for example, target in EXAMPLES.items():
        path = CONFIG_DIR / target
        if path.exists():
            print(f"  keep    config/{target} (already exists)")
        else:
            shutil.copy(CONFIG_DIR / example, path)
            print(f"  created config/{target}")
    (PROJECT_ROOT / "resumes").mkdir(exist_ok=True)
    print("""
Next:
  1. Fill in config/profile.yaml (your details, skill start dates) and config/search.yaml (roles, cities).
     Optional: config/about.md (projects, achievements, preferences) for open-ended questions.
  2. Put your resume PDF in resumes/ and set resume_pdf in profile.yaml.
  3. Optional: set LLM_MODEL / LLM_API_KEY in config/.env for fit scoring and smarter answers.
  4. sortie login naukri      (log in once in the browser window)
  5. sortie run --sites naukri --review --limit 3""")


def main() -> None:
    load_dotenv(CONFIG_DIR / ".env")
    args = build_parser().parse_args()
    if args.command == "init":
        return _init()
    if args.command in ("run", "login") and not ADAPTERS:
        raise SystemExit("No site adapters built yet.")
    if args.command == "run":
        asyncio.run(_run(args))
    elif args.command == "login":
        asyncio.run(_login(args))
    elif args.command == "retry":
        db = Database()
        try:
            print(f"  {db.reset_failures(args.site)} failed {args.site} jobs will be retried on the next run")
        finally:
            db.close()
    else:
        _report(args)


if __name__ == "__main__":
    main()
