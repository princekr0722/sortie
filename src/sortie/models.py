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


@dataclass
class Question:
    text: str
    kind: QuestionKind = QuestionKind.TEXT
    options: list[str] = field(default_factory=list)


@dataclass
class Answer:
    value: str | list[str]
    source: str  # profile | cache | llm | user
    duration: Duration | None = None  # set for experience answers, for years + months dropdowns


@dataclass
class ApplyResult:
    status: Status
    reason: str = ""
    answers: dict[str, Answer] = field(default_factory=dict)  # question -> answer given
    screenshot_path: str | None = None
