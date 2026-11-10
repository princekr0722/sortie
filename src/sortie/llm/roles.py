"""AI-suggested search roles: titles you'd fit that aren't in search.yaml, suggested once a day."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

from sortie.config import PROJECT_ROOT, Profile, SearchConfig
from sortie.llm.client import LLMClient
from sortie.llm.matcher import resume_text
from sortie.log import log

CACHE = PROJECT_ROOT / "data" / "ai_roles.json"
MAX_CHARS, MAX_WORDS = 60, 6
ROLE_WORDS = {"engineer", "developer", "sde", "programmer", "specialist", "consultant", "architect"}
# what may stand before a bare role word: "Backend Engineer" is a job family, "Kafka Engineer" is a tool name
DISCIPLINES = {"software", "backend", "back-end", "frontend", "front-end", "fullstack", "full-stack", "java", "python",
               "javascript", "typescript", "golang", "go", "node", "nodejs", "react", "web", "api", "cloud", "devops",
               "platform", "infrastructure", "systems", "application", "applications", "mobile", "android", "ios",
               "data", "ml", "ai", "llm", "genai", "integration", "middleware", "microservices", "j2ee", "sre"}

SYSTEM = """You help a job seeker search job portals (Naukri, Instahyre, ...). They already search for the roles listed.
Suggest OTHER job titles that recruiters commonly post and that this candidate is a strong fit for, based on their
resume, skills and notes: same seniority, their core stack and domain. Use short, recruiter-style titles of 2-5 words
as typed into a job portal's search box (e.g. "Spring Boot Developer", "Java Microservices Engineer").
Each title must name a real job family (developer, engineer, SDE, ...) that companies hire for; a tool is only a
qualifier ("Java Kafka Developer" is fine, "Redis Engineer" or "Kafka Engineer" is not).
Include adjacent roles the candidate's notes say they want or are moving towards, if they're qualified for them.
Don't repeat or trivially reword the listed roles, and avoid titles containing any excluded word.
Reply with only a JSON object: {"roles": ["<title>", ...]} with at most the requested number."""


def _fingerprint(search: SearchConfig) -> str:
    key = json.dumps([search.roles, search.exclude_keywords, search.experience_range, search.ai_roles])
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _core(title: str) -> frozenset[str]:
    """Title words without generic role words: "Backend Engineer Java" and "Python Backend Developer" match."""
    return frozenset(w for w in re.findall(r"[a-z0-9+#.]+", title.lower()) if w not in ROLE_WORDS)


def _tool_title(title: str) -> bool:
    """A bare "<tool> Engineer/Developer" ("Kafka Engineer"), not a job family."""
    words = title.lower().split()
    return len(words) == 2 and words[1] in ROLE_WORDS and words[0] not in DISCIPLINES


def clean_roles(suggested: list, search: SearchConfig) -> list[str]:
    """Keep short, new, real job titles without excluded words; at most search.ai_roles."""
    seen = {r.strip().lower() for r in search.roles}
    cores = {_core(r) for r in search.roles}
    excluded = [k.lower() for k in search.exclude_keywords]
    out = []
    for item in suggested:
        title = " ".join(str(item).split()).strip(" .,-")
        low = title.lower()
        if (not title or len(title) > MAX_CHARS or len(title.split()) > MAX_WORDS or low in seen
                or _core(title) in cores or _tool_title(title)
                or any(re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", low) for k in excluded)):
            continue
        seen.add(low)
        cores.add(_core(title))
        out.append(title)
    return out[: search.ai_roles]


async def suggest_roles(llm: LLMClient, profile: Profile, search: SearchConfig, about: str = "",
                        cache: Path = CACHE, today: date | None = None) -> list[str]:
    """Extra roles for today: from the cache when it's today's and the settings are unchanged, else one LLM call.
    [] when turned off or the LLM is unavailable."""
    if search.ai_roles <= 0 or not llm.enabled:
        return []
    today = today or date.today()
    fingerprint = _fingerprint(search)
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("date") == today.isoformat() and cached.get("fingerprint") == fingerprint:
            return clean_roles(cached.get("roles", []), search)
    except (OSError, ValueError):
        pass

    skills = ", ".join(f"{name} ({profile.skill_experience(name, today).as_text()})" for name in profile.skills_since)
    user = (
        f"RESUME:\n{resume_text(profile.resume_pdf)[:6000]}\n\n"
        + (f"CANDIDATE'S NOTES:\n{about[:3000]}\n\n" if about else "")
        + f"Total experience: {profile.total_experience(today).as_text()}; current title: {profile.current_title}\n"
        f"Skills: {skills}\n"
        f"Already searching: {json.dumps(search.roles)}\n"
        f"Experience range wanted: {search.experience_range[0]}-{search.experience_range[1]} years\n"
        f"Excluded words: {json.dumps(search.exclude_keywords)}\n"
        f"Suggest {search.ai_roles + 2} roles."  # a few spare: code filters out rewordings and tool titles
    )
    try:
        reply = await llm.complete_json(SYSTEM, user)
    except Exception as e:  # never block a run on this; search the configured roles only
        log.warning(f"AI role suggestions failed ({type(e).__name__}); searching your roles only")
        return []
    roles = clean_roles(reply.get("roles") or [], search)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"date": today.isoformat(), "fingerprint": fingerprint, "roles": roles}, indent=2),
                     encoding="utf-8")
    return roles
