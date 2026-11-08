"""Terminal prompts: answering questions, approving submits, waiting for manual login."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys

try:
    import readline  # noqa: F401  (gives input() line editing: arrow keys move the cursor, not type "^[[D")
except ImportError:  # Windows
    pass

from sortie.llm.answerer import pick_option
from sortie.models import Answer, Job, Question

# arrow keys / other escape sequences that still slip into a reply
ESCAPES = re.compile(r"\x1b\[[0-9;]*[A-Za-z~]|\x1b.|[\x00-\x1f\x7f]")


def clean_reply(text: str) -> str:
    return ESCAPES.sub("", text).strip()


async def prompt(message: str) -> str:
    return clean_reply(await asyncio.to_thread(input, message))


async def ask_user(q: Question, job: Job, suggestion: Answer | None) -> str | None:
    print(f"\n  ? {job.title} @ {job.company}\n  Question: {q.text}")
    for i, option in enumerate(q.options, 1):
        print(f"     {i}. {option}")
    hint = f" [Enter = {suggestion.value}]" if suggestion else ""
    while True:
        reply = await prompt(f"  Answer{hint} (option number, text, or 's' to skip job): ")
        if reply.lower() == "s":
            return None
        if not reply:
            if suggestion:
                return str(suggestion.value)
            print("    (type an answer, or 's' to skip this job)")
            continue
        if not q.options:
            return reply
        if value := match_options(reply, q.options):
            return value
        print(f"    \"{reply}\" isn't one of the options; type its number (1-{len(q.options)}) or its text")


def match_options(reply: str, options: list[str]) -> str | None:
    """Option numbers ("2" or "1, 3") or option text; None if the reply matches none of them."""
    parts = [p.strip() for p in reply.split(",") if p.strip()]
    if parts and all(p.isdigit() and 1 <= int(p) <= len(options) for p in parts):
        return ", ".join(options[int(p) - 1] for p in parts)
    picked = [pick_option(p, options) for p in parts]
    return ", ".join(picked) if parts and all(picked) else None


def _show(job: Job, answers: dict) -> None:
    print(f"\n  ▶ {job.title} @ {job.company} — {', '.join(job.locations)}\n    {job.url}")
    for question, answer in answers.items():
        print(f"    • {question} → {getattr(answer, 'value', answer)}")


async def confirm_submit(job: Job, answers: dict) -> bool:
    _show(job, answers)
    return (await prompt("  Submit? [y/N]: ")).lower() == "y"


async def confirm_preview(job: Job, answers: dict) -> None:
    _show(job, answers)
    print("    (dry run — not submitted)")


def notify(title: str, message: str) -> None:
    """Get the user's attention: terminal bell, plus a desktop notification on macOS."""
    print("\a", end="", flush=True)
    if sys.platform == "darwin":
        script = f'display notification {json.dumps(message)} with title {json.dumps(title)} sound name "Glass"'
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


async def wait_for_login(site: str) -> bool:
    reply = await prompt(f"\n  Log in to {site} in the browser window, then press Enter ('s' to skip {site}): ")
    return reply.lower() != "s"
