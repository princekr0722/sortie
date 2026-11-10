"""Load and validate config/profile.yaml and config/search.yaml."""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from sortie.durations import ZERO, Duration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"

# normalised alias -> normalised canonical name
SKILL_ALIASES = {
    "js": "javascript",
    "ts": "typescript",
    "golang": "go",
    "googlecloud": "gcp",
    "googlecloudplatform": "gcp",
    "amazonwebservices": "aws",
    "k8s": "kubernetes",
    "postgres": "postgresql",
    "reactjs": "react",
    "node": "nodejs",
    "spring": "springboot",
    "springframework": "springboot",
    "mongo": "mongodb",
    "rest": "restapis",
    "restapi": "restapis",
    "restful": "restapis",
    "restfulapi": "restapis",
    "restfulapis": "restapis",
    "restfulservices": "restapis",
    "restservices": "restapis",
    "webservices": "restapis",
    "github": "git",
    "jpa": "hibernate",
    "springdatajpa": "hibernate",
    "datastructures": "dsa",
    "datastructuresandalgorithms": "dsa",
    "algorithms": "dsa",
    "eventdriven": "eventdrivenarchitecture",
    "rdbms": "sql",
}


def normalize_skill(name: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    return SKILL_ALIASES.get(key, key)


class ExpectedCtc(BaseModel):
    min: float
    max: float
    single_value: float


class Profile(BaseModel):
    name: str
    email: str
    phone: str
    current_location: str
    current_company: str
    current_title: str
    career_start: date
    current_ctc_lpa: float
    expected_ctc_lpa: ExpectedCtc
    notice_period_days: int
    serving_notice: bool = False  # resigned and serving your notice period now
    last_working_day: date | None = None  # when serving_notice: your last day at the current job
    willing_to_relocate: bool
    work_authorization: dict[str, bool]
    education: str
    # how your degree may appear in dropdowns, most specific first (e.g. [B.Tech, Bachelor, Graduate])
    education_aliases: list[str] = Field(default_factory=list)
    home_country: str = "India"  # used for work-authorization questions when the job doesn't name a country
    skills_since: dict[str, date | None]
    resume_pdf: Path

    def days_to_join(self, today: date | None = None) -> int:
        """Days until you can join: what's left of the notice you're serving, else your notice period."""
        if self.serving_notice and self.last_working_day:
            return max(0, (self.last_working_day - (today or date.today())).days + 1)
        return self.notice_period_days

    def joining_date(self, today: date | None = None) -> date:
        if self.serving_notice and self.last_working_day:
            return self.last_working_day + timedelta(days=1)
        return (today or date.today()) + timedelta(days=self.notice_period_days)

    def total_experience(self, today: date | None = None) -> Duration:
        return Duration.since(self.career_start, today)

    def skill_experience(self, skill: str, today: date | None = None) -> Duration | None:
        """Duration for a skill; ZERO if listed without a date, None if not in the profile at all."""
        wanted = normalize_skill(skill)
        for name, since in self.skills_since.items():
            if normalize_skill(name) == wanted:
                return Duration.since(since, today) if since else ZERO
        return None


class SearchConfig(BaseModel):
    roles: list[str]
    locations: list[str]
    include_remote: bool = True
    experience_range: tuple[int, int]
    min_fit_score: int = 65
    exclude_companies: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    daily_caps: dict[str, int] = Field(default_factory=dict)
    headless: bool = False
    mode: Literal["review", "auto"] = "review"
    pace: Literal["careful", "normal", "fast"] = "normal"  # pauses between page loads, see browser.PACES
    ai_roles: int = 4  # extra search titles the LLM may add each day (0 = off), see llm/roles.py


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Missing {path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path}. "
                         "Run `sortie init` to create it from the example, then fill it in.")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_profile(path: Path = CONFIG_DIR / "profile.yaml") -> Profile:
    profile = Profile.model_validate(_read_yaml(path))
    if not profile.resume_pdf.is_absolute():
        profile.resume_pdf = PROJECT_ROOT / profile.resume_pdf
    return profile


ABOUT_MAX_CHARS = 8000  # keeps prompts small; the notes are context, not a second resume


def load_about(path: Path = CONFIG_DIR / "about.md") -> str:
    """Your free-form notes (projects, achievements, preferences); "" when missing or still the bare template."""
    if not path.exists():
        return ""
    strip = lambda raw: re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL).strip()
    text = strip(path.read_text(encoding="utf-8"))
    example = path.with_name("about.example.md")
    if path != example and example.exists() and text == strip(example.read_text(encoding="utf-8")):
        return ""  # untouched copy of the template: its sample projects aren't yours
    return text[:ABOUT_MAX_CHARS]


def load_search(path: Path = CONFIG_DIR / "search.yaml") -> SearchConfig:
    return SearchConfig.model_validate(_read_yaml(path))
