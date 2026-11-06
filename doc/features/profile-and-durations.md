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
- Work authorization / sponsorship: the country is taken from the question or the job's location (any country listed in `work_authorization`, cities count, e.g. Dubai → UAE); otherwise `home_country`. Sponsorship = "Yes" where you're not authorized.
- Education dropdowns: first option matching `education_aliases`, in order.
- Missing `resume_pdf`: a warning, and the LLM works from the profile alone.
