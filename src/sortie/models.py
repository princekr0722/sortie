from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from sortie.durations import Duration


class Status(StrEnum):
    APPLIED = "applied"
    SKIPPED = "skipped"
    FAILED = "failed"
    EXTERNAL = "external"  # apply happens on the company's own site (phase 3 queue)
    PENDING_REVIEW = "pending_review"


class Mode(StrEnum):
    DRY_RUN = "dry_run"  # full flow, never submits
    REVIEW = "review"  # asks before each submit
    AUTO = "auto"  # submits without asking


@dataclass
class Job:
    site: str
    site_job_id: str
    url: str
    title: str
    company: str
    locations: list[str] = field(default_factory=list)
    experience: str = ""  # as shown on the site, e.g. "2-5 Yrs"
    salary: str = ""  # as shown on the site, e.g. "12-18 Lacs PA"
    description: str = ""
    external: bool = False  # applied to on the company's own site, not on the job board


class QuestionKind(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    DECIMAL = "decimal"
    CHOICE = "choice"  # pick one of options
    MULTI_CHOICE = "multi_choice"  # pick any of options
    DATE = "date"  # typed in date_format (strftime), e.g. "%d/%m/%Y"


@dataclass
class Question:
    text: str
    kind: QuestionKind = QuestionKind.TEXT
    options: list[str] = field(default_factory=list)
    date_format: str = ""  # for DATE questions: strftime format the field expects
    context: dict[str, str] = field(default_factory=dict)  # earlier answers on the same application form


@dataclass
class Answer:
    value: str | list[str]
    source: str  # profile | cache | llm | user | rule (e.g. a required "Agree" box ticked)
    duration: Duration | None = None  # set for experience answers, for years + months dropdowns


@dataclass
class NeedsYou:
    """Something Sortie deliberately left for you (a voice recording, a message to reply to, ...)."""
    site: str
    what: str
    title: str = ""
    company: str = ""
    url: str = ""


@dataclass
class Followup:
    """An application whose recruiter follow-up (questionnaire) Sortie still has to handle."""
    site: str
    site_job_id: str
    title: str = ""
    company: str = ""
    conversation_id: str = ""
    created_at: str = ""


@dataclass
class ApplyResult:
    status: Status
    reason: str = ""
    answers: dict[str, Answer] = field(default_factory=dict)  # question -> answer given
    screenshot_path: str | None = None
    needs_you: str = ""  # set when the job was left for you: what it needs (e.g. "voice recording")
    conversation_id: str = ""  # the site's conversation for this application, when known (follow-ups)
