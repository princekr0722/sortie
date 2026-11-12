# Sortie — Docs Index

| Doc | What it covers |
|---|---|
| [../README.md](../README.md) | What Sortie is, setup for new users, commands |
| [PLAN.md](PLAN.md) | Decisions, sites, architecture, run flow, phases, testing |
| [TRACKER.md](TRACKER.md) | Work tracker: every task with status |
| `config/answers.yaml` (local, git-ignored) | Your answers to screening questions; reused for the same and reworded questions (editable) |

## Feature docs

| Feature | Doc | Phase | Status |
|---|---|---|---|
| Candidate profile & runtime durations | [features/profile-and-durations.md](features/profile-and-durations.md) | 1 | Done |
| Search settings & pre-filters | [features/search-and-filters.md](features/search-and-filters.md) | 1 | Done |
| LLM fit scoring & screening answers | [features/llm-matching-and-answers.md](features/llm-matching-and-answers.md) | 1 | Done |
| Browser session, run modes & safety | [features/browser-and-safety.md](features/browser-and-safety.md) | 1 | Done |
| Application tracking (SQLite) | [features/tracking-db.md](features/tracking-db.md) | 1 | Done |
| Naukri adapter | [features/site-naukri.md](features/site-naukri.md) | 1 | In progress |
| Instahyre adapter | [features/site-instahyre.md](features/site-instahyre.md) | 1 | Done (inbox in progress) |
| CutShort adapter (+ questionnaires, needs-you list) | [features/site-cutshort.md](features/site-cutshort.md) | 1 | Done |
| Wellfound adapter | [features/site-wellfound.md](features/site-wellfound.md) | 2 | ✅ Regular site (2026-11-12) |
| Dashboard (`sortie dashboard`, local web page) | [features/dashboard.md](features/dashboard.md) | 2 | ✅ Done |
| Indeed adapter (+ generic form filler, LLM step navigator) | [features/site-indeed.md](features/site-indeed.md) | 2 | ⏸ Disabled (blockers in tracker 2.2d) |

## Conventions
- New feature → new doc in `features/`, a row here, and tasks in [TRACKER.md](TRACKER.md).
- When a task's status changes, update the tracker and the feature doc's **Status** line.
