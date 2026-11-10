"""Answer screening questions: profile rules, then saved answers, then the LLM, then the user."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta

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
    "industry software development field role domain experience exp hands handson as an in yoe full time fulltime "
    "exclude excluding including internship internships".split()
)

SYSTEM = """You fill in job application screening questions for the candidate described below.
Use only facts from the profile. Never invent experience, salary or legal facts; if the profile does not
support an answer, give your best guess with low confidence.
The answer is typed into the form as-is: keep it short and form-ready, never explain or mention the profile.
For a conditional question that doesn't apply (e.g. "if you worked at X, share your ID", or "If yes, give details"
after an earlier answer on this application was No), answer "Not applicable".
For a date question, answer with a date in the given DATE FORMAT only.
If the candidate's previous answers include the same or a reworded question, reuse that answer with
high confidence and set "from_previous_answer": true.
For an open question (describe a project, why you're looking, tell us about your experience with X), write
2-4 first-person sentences from the candidate's notes and resume only; if they don't cover it, use low confidence.
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


# a non-JSON reply that still carries the JSON's fields: 'answer: 3 years, confidence: 0.9, from_previous_answer: false'
_ANSWER_FIELD = re.compile(r"""(?<![\w])["']?answer["']?\s*[:=]\s*["']?(.*?)["']?\s*(?:,\s*["']?(?:confidence|from_previous_answer|based_on)\b|[}]|$)""",
                           re.I | re.S)
_META_TAIL = re.compile(r"""[,;]?\s*["']?(confidence|from_previous_answer|based_on)["']?\s*[:=].*$""", re.I | re.S)


def plain_answer(text: str) -> str:
    """The answer part of a plain-text LLM reply, without confidence / from_previous_answer fields."""
    text = text.strip().strip("'\"` ")
    if match := _ANSWER_FIELD.search(text):
        text = match.group(1)
    return _META_TAIL.sub("", text).strip().strip("'\"` ")


def date_format(placeholder: str) -> str | None:
    """strftime format from a field placeholder like "DD/MM/YYYY" or "mm-dd-yyyy"; None if it isn't a date."""
    text = placeholder.strip()
    if not re.fullmatch(r"(?i)(dd|mm|yyyy|yy)([/.\- ])(dd|mm)\2(dd|mm|yyyy|yy)", text):
        return None
    out = text.upper()
    for token, code in (("YYYY", "%Y"), ("YY", "%y"), ("MM", "%m"), ("DD", "%d")):
        out = out.replace(token, code)
    return out


def _date_value(value: str, fmt: str) -> str | None:
    """`value` if it's a valid date in `fmt`, else None (so a phrase never goes into a date field)."""
    try:
        datetime.strptime(value.strip(), fmt)
        return value.strip()
    except ValueError:
        return None


def question_kind(text: str, options: list[str], multi: bool) -> QuestionKind:
    """Field type for a question whose form field doesn't say (free-text boxes on Naukri / Instahyre)."""
    if options:
        return QuestionKind.MULTI_CHOICE if multi else QuestionKind.CHOICE
    t = text.lower()
    if re.search(r"\boffers?\b|details|share|describe|explain|reason", t):
        return QuestionKind.TEXT  # e.g. "share details about any offer - CTC, location, ..."
    if re.search(r"ctc|lakh|lpa|salary", t):
        return QuestionKind.DECIMAL
    if re.search(r"how many|number of|\byears?\b|\byoe\b|notice period in days|how many days", t):
        return QuestionKind.NUMBER
    return QuestionKind.TEXT


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


NEGATIVE_OPTION = re.compile(r"\b(no|not|never|unwilling|don'?t|do not|cannot|can'?t)\b", re.I)


def _yes_no(flag: bool, q: Question) -> Answer:
    """Yes/No from a profile fact. Descriptive options ("Can Relocate All Over India" / "Not Willing to
    Relocate") get the first option that does / doesn't read as a refusal."""
    value = "Yes" if flag else "No"
    if picked := pick_option(value, q.options):
        return Answer(picked, "profile")
    for option in q.options:
        if bool(NEGATIVE_OPTION.search(option)) != flag:
            return Answer(option, "profile")
    return Answer(value, "profile")


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

    lwd = profile.last_working_day if profile.serving_notice else None
    lwd_question = re.search(r"\blwd\b|last working (day|date)|relieving date", ql)
    if lwd_question and "notice" not in ql:  # "What is your last working day?"
        if q.kind == QuestionKind.DATE:
            return Answer(lwd.strftime(q.date_format), "profile") if lwd else None
        return Answer(f"{lwd:%d %b %Y}" if lwd else "Not applicable", "profile")

    if q.kind == QuestionKind.DATE and re.search(
            r"start|join|available|availability|notice", ql):  # "when would you be able to start employment?"
        return Answer(profile.joining_date(today).strftime(q.date_format), "profile")

    # "Are you (currently) serving your notice period?" ("...if serving" is only a condition)
    if "serving" in ql and not re.search(r"\bif (you are |you're |currently )?serving\b", ql) and _is_yes_no(q):
        return _yes_no(profile.serving_notice, q)

    if re.search(r"notice period|how soon can you join|joining time|when can you (join|start)", ql):
        days = profile.days_to_join(today)
        if lwd and q.kind == QuestionKind.TEXT:  # "notice period? mention LWD if serving"
            return Answer(f"Serving notice, last working day {lwd:%d %b %Y}", "profile")
        if q.kind == QuestionKind.CHOICE:
            for o in q.options:
                if re.search(rf"\b{days}\b|1 month|one month", o.lower()):
                    return Answer(o, "profile")
            value = pick_option(str(days), q.options)
            return Answer(value, "profile") if value else ASK_USER
        return Answer(str(days) if q.kind == QuestionKind.NUMBER else f"{days} days", "profile")

    # CutShort's standard screening questions (2026-11-10)
    if m := re.search(r"location of this job (?:will be|is) (.+?)[.?]?\s+(?:are you|is this|is that) (?:okay|ok|fine)", ql):
        city = profile.current_location.split(",")[0]
        here = any(a in m.group(1) for a in location_aliases(city))
        if q.options:
            want = (r"currently in" if here else r"relocate") if (here or profile.willing_to_relocate) else None
            for option in q.options:
                if want and re.search(want, option, re.I) and not re.search(r"\bnot okay\b", option, re.I):
                    return Answer(option, "profile")
        return _yes_no(here or profile.willing_to_relocate, q)
    if m := re.search(r"offers? (?:around|about|up ?to)?\s*₹?\s*([\d.]+)\s*(?:l|lpa|lakhs?)\b\s*(?:-|–|to)\s*₹?\s*([\d.]+)\s*(?:l|lpa|lakhs?)\b", ql):
        return _yes_no(float(m.group(2)) >= expected.min, q)  # "This job offers around ₹7L - ₹15L / yr. Does this work?"

    if re.search(r"\boffers?\b", ql):
        return ASK_USER  # offers in hand / their CTC: only you know

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

    if re.search(r"experience|how many years|how long have you|\byoe\b|\bexp\b", ql):
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
    def __init__(self, profile: Profile, book: AnswerBook, llm: LLMClient, ask_user: AskUser | None,
                 about: str = ""):
        self.profile = profile
        self.book = book
        self.about = about  # config/about.md; only sent for open questions and skill inference
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

    async def compose(self, q: Question, job: Job) -> Answer | None:
        """Free writing for this job (a note to the hiring team): LLM from the resume, notes and job description;
        if it isn't confident, you, with its draft as the suggestion. Profile rules don't apply (they'd answer
        "...your experience..." with a duration), and the text isn't saved to answers.yaml (it's job-specific)."""
        suggestion = None
        if self.llm.enabled:
            suggestion, confidence, _ = await self._ask_llm(q, job, SYSTEM)
            if suggestion and confidence >= MIN_LLM_CONFIDENCE:
                return suggestion
        if self.ask_user is None:
            return None
        reply = await self.ask_user(q, job, suggestion)
        return Answer(reply.strip(), "user") if reply and reply.strip() else None

    def _fit(self, value: str | list[str], q: Question) -> str | list[str] | None:
        if q.kind == QuestionKind.DATE:
            return _date_value(str(value), q.date_format)
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
        open_question = q.kind == QuestionKind.TEXT
        resume = f"RESUME:\n{resume_text(self.profile.resume_pdf)}\n\n" if with_resume or open_question else ""
        notes = f"CANDIDATE'S OWN NOTES:\n{self.about}\n\n" if self.about and (with_resume or open_question) else ""
        previous = f"{self.book.as_prompt()}\n\n" if self.book.entries else ""
        user = (
            f"{resume}{notes}PROFILE:\n{profile_summary(self.profile)}\n\n{previous}"
            f"JOB: {job.title} at {job.company}\n{job.description[:3000]}\n\n"
            + (("EARLIER ANSWERS ON THIS APPLICATION:\n"
                + "\n".join(f"- {k} → {v}" for k, v in list(q.context.items())[-12:]) + "\n\n") if q.context else "")
            + f"QUESTION ({q.kind.value}): {q.text}\n"
            + (f"OPTIONS: {json.dumps(q.options)}\n" if q.options else "")
            + (f"DATE FORMAT: {q.date_format} (today is {date.today():%Y-%m-%d})\n" if q.date_format else "")
        )
        try:
            data = await self.llm.complete_json(system, user)
        except ValueError as e:  # replied in plain text: offer it as a suggestion, never auto-accept it
            plain = plain_answer(str(e).split("reply: ", 1)[-1])
            value = self._fit(plain, q) if plain and len(plain) < 200 else None
            return (Answer(value, "llm"), 0.0, False) if value else (None, 0.0, False)
        except Exception as e:  # a flaky LLM shouldn't kill the run; the user gets asked instead
            log.warning(f"        LLM error ({type(e).__name__}): {str(e)[:150]}")
            return None, 0.0, False
        value = self._fit(data.get("answer", ""), q)
        if value is None or value == "":
            return None, 0.0, False
        reused = data.get("from_previous_answer") is True and bool(self.book.entries)
        return Answer(value, "llm"), float(data.get("confidence", 0)), reused
