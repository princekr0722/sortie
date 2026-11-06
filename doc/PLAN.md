# Sortie — Plan

Sortie: automated, filtered job applications across Indian job portals and company career pages. Anyone can use it with their own `config/` (see the README).
Feature details live in [features/](features/); progress lives in [TRACKER.md](TRACKER.md).

---

## 1. Decisions

| Topic | Decision |
|---|---|
| Stack | Python 3.12 + Playwright (async), persistent real Chrome profile |
| LLM | Pluggable via LiteLLM — provider chosen by `LLM_MODEL` in `.env` (Claude / OpenAI / Ollama / Gemini) |
| Submit mode | `review` (pause before every submit) → switch to `auto` once trusted |
| Browser | Visible by default; `--headless` flag / `headless:` setting to toggle |
| Logins | You log in manually once per site; session stays in `browser-profile/`. No passwords stored |
| Durations | Stored as **start dates**; years/months computed at runtime on every run (never hard-coded) |
| Phase 1 sites | Naukri, Instahyre, CutShort |
| Exclusions | `exclude_companies` in `search.yaml` (e.g. your current employer) — never applied to |
| Personal data | `config/profile.yaml`, `search.yaml`, `answers.yaml`, `.env`, `resumes/`, `data/`, `browser-profile/` are git-ignored; the repo ships `*.example` files and `sortie init` |

## 2. Sites

| Site | How applying works | Difficulty | Phase |
|---|---|---|---|
| Naukri | One-click "Apply" from profile; sometimes a chatbot drawer; some redirect to company site | Easy | 1 |
| Instahyre | Mostly one-click "Interested"/"Apply" on matched opportunities | Easy | 1 |
| CutShort | One-click apply from profile, optional screening questions / cover note | Easy | 1 |
| LinkedIn | Easy Apply multi-step modal; aggressive bot detection (ban risk) | Medium | 2 |
| Indeed | "Easily apply" multi-step; Cloudflare heavy | Medium–hard | 2 |
| Company career pages | Greenhouse / Lever / Ashby (predictable) + Workday / custom (AI browser agent) | Hard | 3 |

## 3. Architecture

```
job-applier/                  # outer folder (not in git)
└── sortie/                   # git repo
    ├── README.md
    ├── config/
    │   ├── profile.example.yaml  → profile.yaml (yours, git-ignored)   → features/profile-and-durations.md
    │   ├── search.example.yaml   → search.yaml  (yours, git-ignored)   → features/search-and-filters.md
    │   ├── about.example.md      → about.md     (yours, git-ignored)   → features/llm-matching-and-answers.md
    │   ├── .env.example          → .env         (yours, git-ignored)
    │   └── answers.yaml          # your saved answers (git-ignored)
    ├── resumes/              # your resume PDF (git-ignored)
    ├── src/sortie/
    │   ├── cli.py            # `sortie init | run | login | report`
    │   ├── config.py         # load + validate YAML, skill aliases
    │   ├── durations.py      # date → years/months at runtime
    │   ├── browser.py        # persistent Chrome, human-like delays
    │   ├── log.py            # console + per-run log file
    │   ├── models.py         # Job, Question, Answer, ApplyResult
    │   ├── db.py             # SQLite tracking
    │   ├── filters.py        # rule-based pre-filter, location aliases
    │   ├── runner.py         # the run loop
    │   ├── terminal.py       # prompts
    │   ├── llm/              # client (LiteLLM), matcher, answerer, answer_book
    │   └── sites/            # base.py + one adapter per site (naukri.py, …)
    ├── tests/                # fixtures/ = fixed test candidate + saved pages
    ├── doc/                  # INDEX, PLAN, TRACKER, features/
    ├── data/                 # applier.db, logs, screenshots, debug (git-ignored)
    └── browser-profile/      # Chrome user data / site logins (git-ignored)
```

## 4. Run flow

1. Launch Chrome with the persistent profile; if a site isn't logged in, pause for manual login.
2. Per site: **search** → drop jobs already in the DB, excluded companies and keywords.
3. **Fetch full JD** → rule-based **pre-filter**.
4. **LLM fit score** (0–100 + reason); below threshold → skipped and logged.
5. **Apply**: profile answers first, LLM fallback, pause on anything uncertain; company-site redirects go to the external queue.
6. **Record**: screenshot, DB row, end-of-run summary.
7. **Safety**: daily caps, random delays, stop on captcha.

## 5. Phases

- **Phase 1:** shared core + Naukri (incl. optional daily profile refresh), Instahyre, CutShort.
- **Phase 2:** LinkedIn Easy Apply (≈25/day cap), Indeed Apply (may need manual help with Cloudflare).
- **Phase 3:** company career pages — Greenhouse / Lever / Ashby adapters, browser-use fallback for Workday / custom; processes the external queue.
- **Extras:** JobSpy discovery, per-job resume tailoring, daily schedule via macOS launchd, small dashboard over SQLite.

## 6. Testing

- `--dry-run`: full flow without clicking submit.
- Unit tests: durations, answerer rules, filters, DB; adapter parsing against saved HTML fixtures.
- First live run in `review` mode with a cap of 3 per site.
