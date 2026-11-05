# Feature: Search Settings & Pre-filters

**Status:** Done (config + pre-filters) · **Phase:** 1 · **Code:** `config/search.yaml`, `src/sortie/filters.py`

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

## Notes
- Location aliases (`LOCATION_ALIASES` in `filters.py`): Bengaluru ↔ Bangalore, Gurugram ↔ Gurgaon, Mumbai ↔ Bombay, Delhi ↔ New Delhi/NCR, Chennai ↔ Madras, …; countries list their main cities (UAE ↔ Dubai/Abu Dhabi/…, UAE ↔ Dubai/Abu Dhabi). Unknown places match by name.
- Company match ignores suffixes (Pvt, Ltd, Inc, Technologies, India), so "Acme India Pvt Ltd" matches an exclusion of "Acme".
- Salary rule only applies when the salary is in Lacs (unlisted/other formats pass).
- Locations outside India: Indian portals list almost none (Naukri skips them); they matter from phase 2 (LinkedIn, Indeed country sites).
