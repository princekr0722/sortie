# Work Tracker

Status: ⬜ todo · 🟨 in progress · ✅ done · ⛔ blocked

_Last updated: 2026-11-13_

## Phase 1 — Core + Naukri, Instahyre, CutShort

| # | Task | Feature doc | Status | Notes |
|---|---|---|---|---|
| 1.1 | Plan + docs (index, tracker, features) | [PLAN](PLAN.md) | ✅ | |
| 1.2 | Project skeleton (pyproject, deps, .gitignore, .env example) | — | ✅ | |
| 1.3 | Profile & search config + loader | [profile](features/profile-and-durations.md), [search](features/search-and-filters.md) | ✅ | |
| 1.4 | Runtime durations (dates → years/months) + tests | [profile](features/profile-and-durations.md) | ✅ | Skill aliases (Golang→Go, k8s, JS…) |
| 1.5 | SQLite tracking + tests | [tracking](features/tracking-db.md) | ✅ | |
| 1.6 | Rule-based pre-filters + tests | [search](features/search-and-filters.md) | ✅ | Keywords checked in title only |
| 1.7 | LLM client (LiteLLM), matcher, answerer + tests | [llm](features/llm-matching-and-answers.md) | ✅ | Unknown skills & money/legal questions never go to the LLM |
| 1.8 | Browser session, run modes, CLI | [browser](features/browser-and-safety.md) | ✅ | Chrome launch verified; tested with a fake site |
| 1.9 | Naukri adapter | [naukri](features/site-naukri.md) | 🟨 | Fixed the 2 causes of all 19 failures (Save click, checkbox questions). Confirm on next run; failed jobs retry once. Headless is blocked by Naukri |
| 1.10 | Instahyre adapter | [instahyre](features/site-instahyre.md) | ✅ | Apply verified live |
| 1.10a | Instahyre: recommended opportunities before search | [instahyre](features/site-instahyre.md) | ✅ | |
| 1.10b | Instahyre inbox: last 10 conversations awaiting your reply → answer questionnaires at the end, log other messages | [instahyre](features/site-instahyre.md) | 🟨 | Built + dry-run on a real questionnaire; needs a live `--review` run |
| 1.17 | `config/about.md` notes (template + `sortie init`), used only for open questions and skill inference | [llm](features/llm-matching-and-answers.md) | ✅ | |
| 1.18 | Speed: pace setting, title screen, no scoring for company-site jobs, faster Naukri polling | [browser](features/browser-and-safety.md) | ✅ | Confirm on next run |
| 1.11 | CutShort adapter | [cutshort](features/site-cutshort.md) | ✅ | Done 2026-11-10; regular site |
| 1.11a | Search from `__NEXT_DATA__` (role → slug, paging, full description from the listing) | [cutshort](features/site-cutshort.md) | ✅ | 50 newest per keyword (paging is a known limit) |
| 1.11b | Apply form: submission message (LLM, job-specific), screening questions via the form filler, completeness check before Submit, success check | [cutshort](features/site-cutshort.md) | ✅ Verified live | 5 applications 2026-11-10 |
| 1.11c | Voice / video / file steps detected; never submitted incomplete; recorded on the needs-you list | [cutshort](features/site-cutshort.md) | ✅ Built | Tested on sample pages; not seen live yet |
| 1.11d | Conversation follow-ups: answer [Questionnaire] forms (JSON read, form filled in page, confirmed via `screeningSubmitted`), other messages → needs-you | [cutshort](features/site-cutshort.md) | ✅ Verified live | 8 questionnaires sent 2026-11-10 |
| 1.21 | Follow-up tracking: every application on Instahyre/CutShort tracked until its questionnaire is handled, checked before and after applying; backfill of the last 14 days | [tracking](features/tracking-db.md) | ✅ | Live dry run: 31 missed CutShort questionnaires found |
| 1.22 | Free-text answers always written by the LLM ("what interests you about this company?", why this role, tell us about yourself, notes): writer prompt tying your background to the job and company; long plain-text replies kept whole | [llm](features/llm-matching-and-answers.md) | ✅ | 2026-11-12, after a recruiter's question reached the user |
| 1.23 | Work-authorization answers: a country the question names that isn't in `work_authorization` → not authorized (US visa types name the US); authorization wording checked before sponsorship | [profile](features/profile-and-durations.md) | ✅ | 2026-11-12, caught before any form was sent |
| 1.24 | Form filler: react-select dropdowns (read options, choose, close by blur), loose radio groups, disabled groups skipped, rejected phone retyped with the country code (an optional one left empty if no format is accepted) | [llm](features/llm-matching-and-answers.md), [wellfound](features/site-wellfound.md) | ✅ | 2026-11-12 |
| 1.25 | Search log separates jobs handled in earlier runs: "33 jobs, 2 new, 31 already handled" (Wellfound, CutShort, Indeed). Before, "new" counted jobs new to the run, so a run whose results were all handled before looked stuck | [search](features/search-and-filters.md) | ✅ | 2026-11-12 |
| 1.26 | Profile `gender` and `languages` (read/write/speak, 1-5): gender choice/text questions, language proficiency lists (scale of 5 or 10), "languages known", "fluent in X?" | [profile](features/profile-and-durations.md) | ✅ | 2026-11-12 |
| 1.27 | Phone and email rules match field labels only ("Phone", "Mobile number", "Email address"): "One mobile product interaction you love, and why" got the phone number | [profile](features/profile-and-durations.md) | ✅ | 2026-11-12 |
| 1.28 | `a` at the question prompt lets the AI answer any question (never saved); behavioural questions ("tell me about a time…", "walk me through…") written by the AI automatically | [llm](features/llm-matching-and-answers.md) | ✅ | 2026-11-12 |
| 1.29 | Dashboard: `sortie dashboard`, a local read-only web page with overview, applications (details, answers, screenshots), needs-you, follow-ups, answers and run logs. Read-only, 127.0.0.1 only, no new dependencies | [dashboard](features/dashboard.md) | ✅ | 2026-11-12 |
| 1.30 | Dashboard outcomes: mark applied jobs Heard back / Interview / Offer / Rejected / Withdrawn with notes and a timeline; funnel and response rates by site and fit score | [dashboard](features/dashboard.md) | ✅ | 2026-11-12 |
| 1.31 | Wellfound searches every place in `locations`, countries included (cities the location table lists as spellings of a country were treated as that country and skipped as "cities only"); a place Wellfound doesn't know redirects to the worldwide page and is now skipped | [wellfound](features/site-wellfound.md) | ✅ | 2026-11-13 |
| 1.20 | Needs-you list (all sites): what Sortie left for you, with links, in `sortie report` | [cutshort](features/site-cutshort.md) | ✅ | Instahyre inbox messages included |
| 1.12 | First live run (review mode, cap 3/site) | — | ✅ | Naukri, Instahyre, CutShort all live |
| 1.13 | Answer book: `config/answers.yaml`, reused for reworded questions via LLM | [llm](features/llm-matching-and-answers.md) | ✅ | |
| 1.14 | Better run logging: numbered per-job lines, reasons + Q&A with sources, summary, log file per run | [browser](features/browser-and-safety.md) | ✅ | `data/logs/` |
| 1.15 | Rename to Sortie; app moved into `sortie/` (git repo root) | — | ✅ | |
| 1.16 | Generic for any user: example configs + `sortie init`, personal config git-ignored, no hard-coded personal values, test fixtures, README | [PLAN](PLAN.md) | ✅ | |
| 1.19 | AI-suggested search roles (`ai_roles`, daily cache) | [search](features/search-and-filters.md) | ✅ | in use since 2026-11-10 |

## Phase 2 — LinkedIn, Indeed
| # | Task | Status |
|---|---|---|
| 2.1 | LinkedIn Easy Apply adapter | ⬜ |
| 2.3 | Wellfound adapter: search (Apollo data, role/city + remote pages, paging), apply with a job-specific note | ✅ Regular site since 2026-11-12: verified live (20 applications over 5 runs); external jobs, the Applied list and unknown role names handled; location-blocked jobs (both wordings) skipped |
| 2.2 | Indeed adapter: search (embedded JSON, per-country sites), job page, external detection | ⏸ **Disabled** 2026-11-09 (built, merged; see 2.2d) |
| 2.2a | Generic form filler (`sites/forms.py`): text, number, date, select, radio, checkbox, consent; choices verified to stick | ✅ Built, merged (used by Indeed; reusable for phase 3) |
| 2.2b | Live Indeed runs: map the smartapply steps (contact, resume, questions, consent, review, intervention) | ✅ Done 2026-11-07, about 10 applications sent |
| 2.2c | LLM step navigator: fallback for unfamiliar form steps (click / check consent / skip / wait), with code-enforced guardrails | ✅ Built, merged; used live |
| 2.2d | **Re-enable Indeed.** Blockers: (1) Yes/No radio clicks look selected but Indeed's React form doesn't register them ("Choose an option to continue"); needs a live check of which click method registers. (2) A reCAPTCHA before almost every Submit after a day of runs; needs a lower cap (about 10/day), `careful` pace and a Google sign-in in the bot browser. To re-enable: move `IndeedAdapter` from `DISABLED` back into `ADAPTERS` in `sites/__init__.py` | ⬜ Blocked |

## Phase 3 — Company career pages
| # | Task | Status |
|---|---|---|
| 3.1 | Greenhouse / Lever / Ashby adapters | ⬜ |
| 3.2 | browser-use fallback (Workday / custom) | ⬜ |
| 3.3 | External queue processing | ⬜ |

## Extras
| # | Task | Status |
|---|---|---|
| X.1 | JobSpy discovery | ⬜ |
| X.2 | Per-job resume tailoring | ⬜ |
| X.3 | Daily schedule (launchd) | ⬜ |
| X.4 | SQLite dashboard | ⬜ |

## Setup checklist (each user)
- [ ] `sortie init`, then fill in `config/profile.yaml` and `config/search.yaml` (optional: `config/about.md`).
- [ ] Put your resume PDF in `resumes/` and set `resume_pdf`.
- [ ] Optional: `LLM_MODEL` / `LLM_API_KEY` (/ `LLM_API_BASE`) in `config/.env`.
- [ ] `sortie login naukri` and `sortie login instahyre` once.
- [ ] Make sure your Naukri profile's current city is right (Naukri pre-fills it into the apply chatbot).
