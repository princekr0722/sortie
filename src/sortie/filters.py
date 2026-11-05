"""Rule-based pre-filters that run before any LLM call."""

from __future__ import annotations

import re

from sortie.config import Profile, SearchConfig
from sortie.models import Job

# place -> spellings used on job sites; countries list their main cities so "Makati" counts as Philippines
LOCATION_ALIASES = {
    "bengaluru": ["bengaluru", "bangalore"],
    "hyderabad": ["hyderabad", "hydrabad", "secunderabad"],
    "pune": ["pune"],
    "mumbai": ["mumbai", "bombay", "navi mumbai", "thane"],
    "chennai": ["chennai", "madras"],
    "kolkata": ["kolkata", "calcutta"],
    "delhi": ["delhi", "new delhi", "delhi ncr", "ncr"],
    "gurugram": ["gurugram", "gurgaon"],
    "noida": ["noida", "greater noida"],
    "ahmedabad": ["ahmedabad"],
    "kochi": ["kochi", "cochin"],
    "philippines": ["philippines", "manila", "makati", "taguig", "pasig", "cebu", "quezon city"],
    "singapore": ["singapore"],
    "uae": ["uae", "united arab emirates", "dubai", "abu dhabi"],
    "usa": ["usa", "united states", "u.s."],
    "uk": ["uk", "united kingdom", "london"],
}
COUNTRIES = {"india", "philippines", "singapore", "uae", "usa", "uk"}


def location_aliases(place: str) -> list[str]:
    key = place.strip().lower()
    for name, aliases in LOCATION_ALIASES.items():
        if key == name or key in aliases:
            return aliases
    return [key]
REMOTE_WORDS = ["remote", "work from home", "wfh", "anywhere"]
COMPANY_SUFFIXES = r"\b(private|pvt|limited|ltd|inc|llp|llc|technologies|technology|india)\b"


def _norm_company(name: str) -> str:
    name = re.sub(COMPANY_SUFFIXES, "", name.lower())
    return re.sub(r"[^a-z0-9]", "", name)


def parse_range(text: str) -> tuple[float, float] | None:
    """'2-5 Yrs' -> (2, 5); '12-18 Lacs PA' -> (12, 18); '3+ years' -> (3, 3)."""
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))]
    if not numbers:
        return None
    return (numbers[0], numbers[1]) if len(numbers) > 1 else (numbers[0], numbers[0])


def _location_ok(job: Job, search: SearchConfig) -> bool:
    if not job.locations:
        return True
    text = " ".join(job.locations).lower()
    if search.include_remote and any(w in text for w in REMOTE_WORDS):
        return True
    for wanted in search.locations:
        if any(a in text for a in location_aliases(wanted)):
            return True
    return False


def skip_reason(job: Job, search: SearchConfig, profile: Profile) -> str | None:
    """Return why the job should be skipped, or None if it passes."""
    company = _norm_company(job.company)
    for excluded in search.exclude_companies:
        if _norm_company(excluded) in company:
            return f"excluded company: {excluded}"

    title = job.title.lower()
    for keyword in search.exclude_keywords:
        if re.search(rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])", title):
            return f"excluded keyword in title: {keyword}"

    exp = parse_range(job.experience) if job.experience else None
    lo, hi = search.experience_range
    if exp and (exp[0] > hi or exp[1] < lo):
        return f"experience {job.experience} outside {lo}-{hi}"

    if not _location_ok(job, search):
        return f"location {', '.join(job.locations)} not wanted"

    salary = parse_range(job.salary) if job.salary and "lac" in job.salary.lower() else None
    if salary and salary[1] < profile.expected_ctc_lpa.min:
        return f"salary {job.salary} below expected {profile.expected_ctc_lpa.min} LPA"

    return None
