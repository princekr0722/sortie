"""Terminal prompts: answering questions, approving submits, waiting for manual login."""

from __future__ import annotations

import asyncio

from sortie.models import Answer, Job, Question


async def prompt(message: str) -> str:
    return (await asyncio.to_thread(input, message)).strip()


async def ask_user(q: Question, job: Job, suggestion: Answer | None) -> str | None:
    print(f"\n  ? {job.title} @ {job.company}\n  Question: {q.text}")
    for i, option in enumerate(q.options, 1):
        print(f"     {i}. {option}")
    hint = f" [Enter = {suggestion.value}]" if suggestion else ""
    reply = await prompt(f"  Answer{hint} (option number, text, or 's' to skip job): ")
    if reply.lower() == "s":
        return None
    if not reply:
        return str(suggestion.value) if suggestion else None
    if q.options:
        numbers = [p.strip() for p in reply.split(",")]
        if all(n.isdigit() and 1 <= int(n) <= len(q.options) for n in numbers):
            return ", ".join(q.options[int(n) - 1] for n in numbers)
    return reply


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


async def wait_for_login(site: str) -> bool:
    reply = await prompt(f"\n  Log in to {site} in the browser window, then press Enter ('s' to skip {site}): ")
    return reply.lower() != "s"
