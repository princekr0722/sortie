# Feature: Search Settings & Pre-filters

**Status:** Done (config + pre-filters); AI-suggested roles added 2026-11-10 · **Phase:** 1 · **Code:** `config/search.yaml`, `src/sortie/filters.py`, `src/sortie/llm/roles.py`

## Purpose
Decide what to search for and drop unsuitable jobs **before** any LLM call (cheap, deterministic).

## `config/search.yaml`

Created by `sortie init` from `config/search.example.yaml`; personal, so git-ignored.

```yaml
roles:
  - Python Backend Developer
  - Software Engineer Python Django
locations: [Chennai, Gurugram, Noida]
include_remote: true
experience_range: [2, 5]
min_fit_score: 65
exclude_companies: [Your Current Company]
exclude_keywords: [intern, lead, manager]
daily_caps: {naukri: 40, instahyre: 30, cutshort: 30}   # Naukri itself seems to stop accepting applies around ~50/day
headless: false
mode: review                         # review | auto
```

## Pre-filter rules (in order)
1. Already in DB (any status) → skip (done by the runner via `Database.is_seen`).
2. Company matches `exclude_companies` (case-insensitive, ignores suffixes like "Pvt Ltd") → skip.
3. **Title** contains an `exclude_keywords` entry as a whole word → skip (JD not checked: words like "lead" appear in most JDs; too-senior roles are caught by rule 4).
4. Listed experience range doesn't overlap `experience_range` → skip.
5. Location not in `locations` and not remote (when `include_remote`) → skip.
6. Listed max salary below your expected minimum (`expected_ctc_lpa.min`) → skip; unlisted salary passes.

Every skip is stored with its reason so filters can be tuned.

After these rules, an LLM **title screen** drops clearly off-target titles before the job page is opened (see [browser-and-safety.md](browser-and-safety.md#run-loop-runnerpy)).

## Notes
- Location aliases (`LOCATION_ALIASES` in `filters.py`): Bengaluru ↔ Bangalore, Gurugram ↔ Gurgaon, Mumbai ↔ Bombay, Delhi ↔ New Delhi/NCR, Chennai ↔ Madras, …; countries list their main cities (UAE ↔ Dubai/Abu Dhabi/…, UAE ↔ Dubai/Abu Dhabi). Unknown places match by name.
- Company match ignores suffixes (Pvt, Ltd, Inc, Technologies, India), so "Acme India Pvt Ltd" matches an exclusion of "Acme".
- Salary rule only applies when the salary is in Lacs (unlisted/other formats pass).
- Locations outside India: Indian portals list almost none (Naukri skips them); they matter from phase 2 (LinkedIn, Indeed country sites).

## AI-suggested roles (`ai_roles`)
Your `roles` list only finds jobs posted under titles you thought of. Once a day the LLM adds titles you'd fit that recruiters actually use.

- **Input:** resume, profile (total experience, skills with their durations), `about.md`, your `roles`, `experience_range`, `exclude_keywords`.
- **Output:** up to `ai_roles` extra search titles (default 4, `0` = off). They're short, recruiter-style (2–5 words), at your level, and different from your roles. Examples: "Spring Boot Developer", "Java Microservices Engineer".
- **Cleaned in code**, because a small model doesn't reliably follow prompt rules. Dropped:
  - duplicates and **rewordings** of your roles: the same words once engineer/developer/… are ignored, so "Backend Engineer Java" duplicates "Python Backend Developer";
  - bare **tool titles** like "Kafka Engineer" or "Redis Developer" (a two-word title is kept only when its first word is a discipline: backend, full stack, Java, cloud, AI, …);
  - titles with an excluded keyword, or longer than 60 characters / 6 words.

  The LLM is asked for `ai_roles + 2` titles so there's room to filter, then the list is cut to `ai_roles`.
- **Order:** your roles are searched first and the AI's after them, so the daily cap fills from your own titles first.
- **Cache:** `data/ai_roles.json` is reused for the whole day and refreshed on the next day, or as soon as your roles, exclusions or `ai_roles` change. That's one LLM call a day.
- **Search log:** each results page logs "N jobs, N new, N already handled". Jobs handled in an earlier run are skipped silently by the runner, which counts them (`SiteAdapter.handled_before`).
- **Logged** at the start of each run: `🤖 AI-suggested roles: …`.
- **No LLM, or the call fails:** your roles only; the run carries on.
- The title screen and fit score still judge every job, so a loose suggestion costs search pages, not applications.
- Salary pre-filter (2026-11-10): ranges in lakhs are read in Naukri's ("12-18 Lacs PA") and CutShort's ("₹10L - ₹14L / yr") formats. A range whose top is below `expected_ctc_lpa.min` is skipped. Amounts in full rupees (Indeed) aren't judged.
