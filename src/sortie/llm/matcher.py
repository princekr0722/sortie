"""Score how well a job fits the candidate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from pypdf import PdfReader

from sortie.config import Profile
from sortie.llm.client import LLMClient
from sortie.log import log
from sortie.models import Job

SYSTEM = """You screen job postings for a candidate. Compare the job with the candidate's resume and profile.
Reply with only a JSON object: {"score": <0-100>, "reason": "<one sentence>", "missing_must_haves": ["..."]}.
Scoring:
- 80-100: the role's core stack and seniority match what the candidate already does.
- 65-79: mostly matching; at most one secondary requirement is new to the candidate.
- 40-64: the role is built around a tool, product or domain the candidate hasn't used (e.g. a specific
  ALM/ERP/CRM platform, mainframe, SAP, Salesforce, embedded), even if it mentions Java.
- 0-39: different job family or clearly wrong seniority.
A core requirement missing from the candidate's experience caps the score at 60. Generic Java alone is not a match."""


@dataclass
class FitResult:
    score: int
    reason: str
    missing_must_haves: list[str]


@lru_cache
def resume_text(path: Path) -> str:
    """Resume text for prompts; empty (with a warning) when the PDF is missing, so the profile alone is used."""
    if not path.exists():
        log.warning(f"resume not found at {path}; set resume_pdf in config/profile.yaml")
        return ""
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def profile_summary(profile: Profile, today: date | None = None) -> str:
    skills = []
    for name in profile.skills_since:
        d = profile.skill_experience(name, today)
        skills.append(f"{name}: {d.as_text()}")
    return (
        f"Total experience: {profile.total_experience(today).as_text()}\n"
        f"Current: {profile.current_title} at {profile.current_company}, {profile.current_location}\n"
        f"Current CTC: {profile.current_ctc_lpa} LPA; expected {profile.expected_ctc_lpa.min}-{profile.expected_ctc_lpa.max} LPA\n"
        f"Notice period: {profile.notice_period_days} days; willing to relocate: {profile.willing_to_relocate}\n"
        f"Skill experience: {'; '.join(skills)}"
    )


async def score_fit(llm: LLMClient, profile: Profile, job: Job) -> FitResult | None:
    """None when no LLM is configured."""
    if not llm.enabled:
        return None
    user = (
        f"RESUME:\n{resume_text(profile.resume_pdf)}\n\nPROFILE:\n{profile_summary(profile)}\n\n"
        f"JOB: {job.title} at {job.company}\nLocation: {', '.join(job.locations)}\n"
        f"Experience: {job.experience}\nSalary: {job.salary}\n\n{job.description}"
    )
    data = await llm.complete_json(SYSTEM, user)
    return FitResult(
        score=int(data["score"]),
        reason=str(data.get("reason", "")),
        missing_must_haves=list(data.get("missing_must_haves", [])),
    )
