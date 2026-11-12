# Feature: Candidate Profile & Runtime Durations

**Status:** Done · **Phase:** 1 · **Code:** `config/profile.yaml` (from `config/profile.example.yaml`), `src/sortie/config.py`, `src/sortie/durations.py`

## Purpose
One source of truth for every fact Sortie puts into an application form. Anything time-based is stored as a **start date** and the duration is computed on the day of the run, so answers stay correct without editing the file.

## `config/profile.yaml`
Created by `sortie init` from `config/profile.example.yaml`; personal, so git-ignored.

```yaml
name: Your Name
email: you@example.com
phone: "+910000000000"
current_location: Chennai, Tamil Nadu, India
current_company: Your Company
current_title: Software Engineer
career_start: 2022-07-01          # total experience is computed from this
current_ctc_lpa: 10               # lakhs per annum
expected_ctc_lpa: {min: 14, max: 18, single_value: 16}
notice_period_days: 30
willing_to_relocate: true
home_country: India               # default country for work-authorization questions
work_authorization:               # country -> allowed to work there without sponsorship
  india: true
education: B.Tech, Your University (2022)
education_aliases: [B.Tech, B.E, Bachelor of Technology, Bachelor, Graduate, UG]
skills_since:                     # skill -> date you started using it (null = no experience)
  Java: 2022-07-01
  Spring Boot: 2022-07-01
  Docker: 2023-01-01
  Python: null
resume_pdf: resumes/resume.pdf
gender:                           # optional; empty -> you're asked (never the AI)
languages:                        # optional: read / write / speak, 1-5
  English: {read: 5, write: 4, speak: 4}
```

Longer context (projects, achievements, preferences) goes in `config/about.md`, not here. See [llm-matching-and-answers.md](llm-matching-and-answers.md).

## Duration rules
- Duration = run date − start date, in whole months.
- Integer "years" field → whole years, rounded down (3y 2m → `3`).
- Years + months dropdowns (Naukri style) → `3` years, `2` months.
- Decimal field → one decimal place, rounded down (3y 2m → `3.1`).
- Free-text field → "3 years 5 months".
- `null` → `0`.
- Total experience comes from `career_start`.
- Skill names are matched case-insensitively with aliases (e.g. `Springboot` / `Spring` → Spring Boot, `GCP` ↔ Google Cloud, `JS` → JavaScript, `Golang` → Go, `Rest` / `RESTful` / `web services` → REST APIs, `JPA` → Hibernate, `GitHub` → Git, `data structures` → DSA, `RDBMS` → SQL). Aliases live in `SKILL_ALIASES` in `config.py`.
- **A skill not in `skills_since`**: the LLM infers its experience from the resume and related listed skills (capped at total experience); only if it's not sure at all does the question come to you, and your answer is saved to `config/answers.yaml`. Add a skill with its date when you want an exact, rule-based answer.

## Salary & notice rules
- Current CTC from `current_ctc_lpa`; rupees (`lpa × 100000`) when the question asks for INR.
- Expected CTC: range / text field → `min-max LPA`; single-value field → `single_value`.
- Notice period from `notice_period_days`.

## Location, authorization, education
- Current location: the city part of `current_location`, matched against dropdown options using the location aliases (Bengaluru ↔ Bangalore, Gurugram ↔ Gurgaon, …).
- Work authorization / sponsorship: which country the question is about is decided in this order:
  1. any country the question names. One you haven't listed in `work_authorization` counts as **not authorized**. US visa types (H-1B, TN, O-1, E-3, green card) count as naming the US.
  2. a listed country in the job's location (cities count, e.g. Dubai → UAE).
  3. `home_country`.
  Sponsorship = "Yes" where you're not authorized. "Authorized to work in X without sponsorship?" is answered as an authorization question.
  Fixed 2026-11-12: "authorized to work in the United States?" used to fall back to the home country and answer Yes. It was caught before any form was sent.
- Education dropdowns: first option matching `education_aliases`, in order.
- Phone and email (fixed 2026-11-12): only field labels match ("Phone", "Mobile number", "Contact no.", "Email address"). A sentence that just uses the word ("One mobile product interaction you love, and why") used to get your phone number.
- Gender (added 2026-11-12): from `gender`, matched as a whole word ("Male" never picks "Female"). Left empty, you're asked; the AI never answers it.
- Languages (added 2026-11-12), from `languages`:
  - "Mention the languages you can read, write or speak on a scale of 5" → `English: Read (5/5), Write (4/5), Speak (4/5); Spanish: …` (a "out of 10" question gets doubled numbers);
  - "Languages known" → `English, Spanish`;
  - "Are you fluent in / can you speak Spanish?" (Yes/No) → Yes when that language's `speak` is 3 or more. A language you haven't listed goes to the LLM.
- Missing `resume_pdf`: a warning, and the LLM works from the profile alone.

## Notice status
- `notice_period_days`: your contractual notice.
- `serving_notice: true/false`: whether you've resigned and are serving it now.
- `last_working_day: YYYY-MM-DD`: your last day, when serving.
- At runtime: `days_to_join()` = the days left until your LWD when serving, else `notice_period_days`. `joining_date()` = the day after your LWD, else today + notice. Update these two fields when you resign; every notice / LWD / joining question is then answered from them, with no prompts.

