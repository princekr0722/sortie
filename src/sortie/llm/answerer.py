"""Answer screening questions: profile rules, then saved answers, then the LLM, then the user."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Awaitable, Callable
from datetime import date

from sortie.config import Profile, normalize_skill
from sortie.durations import Duration
from sortie.filters import location_aliases
from sortie.llm.answer_book import AnswerBook
from sortie.llm.client import LLMClient
from sortie.llm.matcher import profile_summary, resume_text
from sortie.log import log
from sortie.models import Answer, Job, Question, QuestionKind

# (question, job, LLM suggestion) -> typed reply, or None to skip the job
AskUser = Callable[[Question, Job, Answer | None], Awaitable[str | None]]

MIN_LLM_CONFIDENCE = 0.7
# Experience in a skill missing from the profile: the LLM infers it from related skills / the resume,
# and only "not sure at all" goes to the user. The answer is capped at total experience.
MIN_SKILL_CONFIDENCE = 0.3

# Never left to the LLM: if the profile rules don't cover these, the user answers.
SENSITIVE = re.compile(
    r"\b(salary|ctc|compensation|package|stipend|bond|visa|sponsor\w*|authori[sz]\w*|criminal|"
    r"convict\w*|disabilit\w*|gender|caste|religion|background check|legal)\b"
)

# Words that don't name a specific skill in an experience question ("total years of IT experience?")
FILLER = set(
    "a an the how many much long years year yrs months of in on with using do does you your have has "
    "what is are please mention enter specify total overall relevant professional work working it "
    "industry software development field role domain experience exp hands handson as an in".split()
)

SYSTEM = """You fill in job application screening questions for the candidate described below.
Use only facts from the profile. Never invent experience, salary or legal facts; if the profile does not
support an answer, give your best guess with low confidence.
The answer is typed into the form as-is: keep it short and form-ready, never explain or mention the profile.
For a conditional question that doesn't apply (e.g. "if you worked at X, share your ID"), answer "Not applicable".
If the candidate's previous answers include the same or a reworded question, reuse that answer with
high confidence and set "from_previous_answer": true.
Reply with only a JSON object: {"answer": "<text>", "confidence": <0-1>, "from_previous_answer": <true|false>}.
For a choice question the answer must be exactly one of the options; for a multi-choice question
"answer" is a list of options."""

SKILL_SYSTEM = """You answer "how much experience do you have in X" questions on job applications for the candidate below.
X is not in the candidate's skill list, so infer it from their resume and listed skills:
- If X is part of, or used through, a listed skill (e.g. Spring MVC via Spring Boot, JUnit via Java backend work,
  CI/CD via Docker/Kubernetes deployments), use that skill's duration.
- If the resume clearly shows work with X, use the duration of the job where it was used.
- Never exceed the candidate's total experience.
- If nothing in the resume or skills relates to X, set confidence below 0.3.
If the candidate's previous answers include the same or a reworded question, reuse that answer with
high confidence and set "from_previous_answer": true.
Answer for the field type: number -> whole years; decimal -> years with one decimal; text -> e.g. "3 years";
choice -> exactly one of the options.
Reply with only a JSON object:
{"answer": "<answer>", "confidence": <0-1>, "based_on": "<listed skill or resume line>", "from_previous_answer": <true|false>}."""


class _AskUser:
    """Profile rules recognised the question but can't answer it safely; skip the LLM."""


ASK_USER = _AskUser()


class _UnknownSkill:
    """Experience question about a skill missing from the profile; the LLM infers it from related experience."""


UNKNOWN_SKILL = _UnknownSkill()


def _number(text: str) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return float(m.group(0)) if m else None


def _option_range(option: str) -> tuple[float, float] | None:
    o = option.lower()
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", o.replace(",", ""))]
    if not nums:
        return None
    if "+" in o or re.search(r"\b(more|above|over|greater|plus)\b", o):
        return nums[0], math.inf
    if "<" in o or re.search(r"\b(less|below|under|upto|up to)\b", o):
        return 0, nums[0]
    return (nums[0], nums[1]) if len(nums) > 1 else (nums[0], nums[0])


def pick_option(value: str, options: list[str]) -> str | None:
    """Map an answer onto one of the offered options (exact, numeric range, then substring)."""
    if not options:
        return value
    v = value.strip().lower()
    for o in options:
        if o.strip().lower() == v:
            return o
    num = _number(value)
    if num is not None:
        ranges = [(o, _option_range(o)) for o in options]
        for o, r in ranges:  # half-open first, so 3 picks "3-5" over "1-3"
            if r and (r[0] == r[1] == num or r[0] <= num < r[1]):
                return o
        for o, r in ranges:
            if r and r[0] <= num <= r[1]:
                return o
    if v:
        for o in options:
            if v in o.lower() or o.strip().lower() in v:
                return o
    return None


def _is_yes_no(q: Question) -> bool:
    return bool(q.options) and {o.strip().lower() for o in q.options} <= {"yes", "no"}


def _yes_no(flag: bool, q: Question) -> Answer:
    value = "Yes" if flag else "No"
    return Answer(pick_option(value, q.options) or value, "profile")


def _fmt(n: float) -> str:
    return f"{n:g}"


def _in_rupees(q: str) -> bool:
    return bool(re.search(r"\b(inr|rupees|rs)\b|₹|per annum", q)) and not re.search(r"lakh|lac|lpa", q)


def _money(lpa: float, text: str, q: Question, ql: str) -> Answer | None:
    if q.kind in (QuestionKind.NUMBER, QuestionKind.DECIMAL):
        value = str(int(lpa * 100_000)) if _in_rupees(ql) else _fmt(lpa)
    elif q.kind == QuestionKind.CHOICE:
        value = pick_option(_fmt(lpa), q.options)
        if value is None:
            return None
    else:
        value = text
    return Answer(value, "profile")


def _duration_answer(d: Duration, q: Question) -> Answer | None:
    if _is_yes_no(q):
        return _yes_no(d.months > 0, q)
    if q.kind == QuestionKind.NUMBER:
        value = str(d.years)
    elif q.kind == QuestionKind.DECIMAL:
        value = _fmt(d.decimal_years)
    elif q.kind == QuestionKind.CHOICE:
        value = pick_option(_fmt(d.decimal_years), q.options) or pick_option(str(d.years), q.options)
        if value is None:
            return None
    else:
        value = d.as_text()
    return Answer(value, "profile", duration=d)


def find_skill(profile: Profile, text: str) -> str | None:
    """Profile skill named in the text, matching 1-3 word phrases so 'spring boot' and 'google cloud' work."""
    known = {normalize_skill(name): name for name in profile.skills_since}
    words = re.findall(r"[a-z0-9.+#]+", text.lower())
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            key = normalize_skill("".join(words[i : i + size]))
            if key in known:
                return known[key]
    return None


def _job_country(profile: Profile, q: str, job: Job) -> str:
    """Country the question or job is about, out of those in work_authorization; else your home country."""
    text = q + " " + " ".join(job.locations).lower()
    for country in profile.work_authorization:
        if any(a in text for a in location_aliases(country)):
            return country
    return profile.home_country.lower()


def _authorized(profile: Profile, country: str) -> bool:
    return {k.lower(): v for k, v in profile.work_authorization.items()}.get(country, False)


def profile_answer(profile: Profile, q: Question, job: Job,
                   today: date | None = None) -> Answer | _AskUser | _UnknownSkill | None:
    """Answer from profile facts; None when the rules don't recognise the question."""
    ql = q.text.lower()
    expected = profile.expected_ctc_lpa

    if re.search(r"notice period|how soon can you join|joining time|when can you (join|start)", ql):
        if "serving" in ql:
            return None
        days = profile.notice_period_days
        if q.kind == QuestionKind.CHOICE:
            for o in q.options:
                if re.search(rf"\b{days}\b|1 month|one month", o.lower()):
                    return Answer(o, "profile")
            value = pick_option(str(days), q.options)
            return Answer(value, "profile") if value else ASK_USER
        return Answer(str(days) if q.kind == QuestionKind.NUMBER else f"{days} days", "profile")

    if re.search(r"\b(ctc|salary|compensation|package)\b", ql):
        if re.search(r"expect", ql):
            if q.kind in (QuestionKind.NUMBER, QuestionKind.DECIMAL, QuestionKind.CHOICE):
                return _money(expected.single_value, "", q, ql) or ASK_USER
            return Answer(f"{_fmt(expected.min)}-{_fmt(expected.max)} LPA", "profile")
        if re.search(r"current|present|existing|last|drawn", ql) or "ctc" in ql:
            return _money(profile.current_ctc_lpa, f"{_fmt(profile.current_ctc_lpa)} LPA", q, ql) or ASK_USER
        return ASK_USER

    if "relocat" in ql:
        return _yes_no(profile.willing_to_relocate, q)

    if re.search(r"sponsor", ql):
        return _yes_no(not _authorized(profile, _job_country(profile, ql, job)), q)
    if re.search(r"authori[sz]ed to work|eligible to work|right to work|work permit|legally allowed", ql):
        return _yes_no(_authorized(profile, _job_country(profile, ql, job)), q)

    if re.search(r"experience|how many years|how long have you", ql):
        skill = find_skill(profile, ql)
        if skill:
            return _duration_answer(profile.skill_experience(skill, today), q) or ASK_USER
        if _is_yes_no(q):
            return None  # "Do you have experience building REST APIs?" -> LLM
        if set(re.findall(r"[a-z]+", ql)) <= FILLER:
            return _duration_answer(profile.total_experience(today), q) or ASK_USER
        return UNKNOWN_SKILL

    if re.search(r"current (location|city)|where are you (based|located)|which city|preferred location", ql):
        city = profile.current_location.split(",")[0]
        if q.options:
            value = next((v for a in location_aliases(city) if (v := pick_option(a, q.options))), None)
            return Answer(value, "profile") if value else ASK_USER
        return Answer(city, "profile")

    if re.search(r"highest (qualification|education|degree)|graduat|bachelor", ql):
        if _is_yes_no(q):
            return _yes_no(True, q)
        if q.options:
            for guess in profile.education_aliases or [profile.education.split(",")[0]]:
                if value := pick_option(guess, q.options):
                    return Answer(value, "profile")
            return ASK_USER
        return Answer(profile.education, "profile")

    if re.search(r"e-?mail", ql):
        return Answer(profile.email, "profile")
    if re.search(r"phone|mobile|contact number", ql):
        return Answer(profile.phone, "profile")
    if re.search(r"^(your |full )?name\b", ql):
        return Answer(profile.name, "profile")

    return None


class Answerer:
    def __init__(self, profile: Profile, book: AnswerBook, llm: LLMClient, ask_user: AskUser | None):
        self.profile = profile
        self.book = book
        self.llm = llm
        self.ask_user = ask_user

    async def answer(self, q: Question, job: Job) -> Answer | None:
        """None means the question couldn't be answered and the job should be skipped."""
        rule = profile_answer(self.profile, q, job)
        if isinstance(rule, Answer):
            return rule

        saved = self.book.lookup(q.text)
        if saved is not None and (value := self._fit(saved, q)) is not None:
            return Answer(value, "your answers")

        suggestion = None
        if rule is UNKNOWN_SKILL and self.llm.enabled:
            suggestion, confidence, _ = await self._ask_llm(q, job, SKILL_SYSTEM, with_resume=True)
            suggestion = suggestion and self._cap_experience(suggestion, q)
            if suggestion and confidence >= MIN_SKILL_CONFIDENCE:
                return suggestion
        elif rule is not ASK_USER and self.llm.enabled:
            suggestion, confidence, reused = await self._ask_llm(q, job, SYSTEM)
            # sensitive questions only via a reworded answer you gave yourself
            allowed = reused or not SENSITIVE.search(q.text.lower())
            if suggestion and confidence >= MIN_LLM_CONFIDENCE and allowed:
                return suggestion

        if self.ask_user is None:
            return None
        reply = await self.ask_user(q, job, suggestion)
        if reply is None:
            return None
        raw = [p.strip() for p in reply.split(",")] if q.kind == QuestionKind.MULTI_CHOICE else reply.strip()
        value = self._fit(raw, q)
        if value is None:
            return None
        self.book.add(q.text, value, q.options)
        return Answer(value, "user")

    def _fit(self, value: str | list[str], q: Question) -> str | list[str] | None:
        if q.kind == QuestionKind.CHOICE:
            return pick_option(str(value), q.options)
        if q.kind == QuestionKind.MULTI_CHOICE:
            items = value if isinstance(value, list) else [value]
            picked = [p for p in (pick_option(str(v), q.options) for v in items) if p]
            return picked or None
        return value if isinstance(value, str) else ", ".join(value)

    def _cap_experience(self, answer: Answer, q: Question) -> Answer | None:
        """Numeric experience can't exceed total experience; an unparseable number is no answer."""
        if q.kind not in (QuestionKind.NUMBER, QuestionKind.DECIMAL):
            return answer
        n = _number(str(answer.value))
        if n is None:
            return None
        total = self.profile.total_experience()
        if q.kind == QuestionKind.NUMBER:
            answer.value = str(min(int(n), total.years))
        else:
            answer.value = _fmt(min(n, total.decimal_years))
        return answer

    async def _ask_llm(self, q: Question, job: Job, system: str,
                       with_resume: bool = False) -> tuple[Answer | None, float, bool]:
        """(answer, confidence, reused one of your previous answers)"""
        resume = f"RESUME:\n{resume_text(self.profile.resume_pdf)}\n\n" if with_resume else ""
        previous = f"{self.book.as_prompt()}\n\n" if self.book.entries else ""
        user = (
            f"{resume}PROFILE:\n{profile_summary(self.profile)}\n\n{previous}"
            f"JOB: {job.title} at {job.company}\n{job.description[:3000]}\n\n"
            f"QUESTION ({q.kind.value}): {q.text}\n"
            + (f"OPTIONS: {json.dumps(q.options)}\n" if q.options else "")
        )
        try:
            data = await self.llm.complete_json(system, user)
        except Exception as e:  # a flaky LLM shouldn't kill the run; the user gets asked instead
            log.warning(f"        LLM error ({type(e).__name__}): {str(e)[:150]}")
            return None, 0.0, False
        value = self._fit(data.get("answer", ""), q)
        if value is None or value == "":
            return None, 0.0, False
        reused = data.get("from_previous_answer") is True and bool(self.book.entries)
        return Answer(value, "llm"), float(data.get("confidence", 0)), reused
