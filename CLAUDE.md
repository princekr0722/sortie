# Sortie: guidelines for Claude

Sortie finds jobs on Indian job portals, scores them against the user's resume, and applies, answering screening questions from the user's profile. Python 3.12, async Playwright (installed Chrome, persistent profile), LiteLLM, SQLite. Overview and setup: `README.md`. Design and progress: `doc/INDEX.md`.

## Commands
```bash
.venv/bin/pytest -q                                      # tests (must pass before finishing any change)
.venv/bin/sortie init | login <site> | report
.venv/bin/sortie run --sites naukri --review --limit 3   # --auto, --dry-run, --headless
.venv/bin/pip install -e ".[dev]"                        # after changing pyproject / moving the package
```

## Working style
- **Plan before building.** Write or update the feature doc and tracker rows first, then code.
- **Keep the docs current with every change.** That means `doc/INDEX.md` (feature list and status), `doc/TRACKER.md` (task status and notes), `doc/features/<feature>.md` (behaviour and decisions, with the `**Status:**` line), and `doc/PLAN.md` when the architecture changes. A new feature gets a new feature doc, an INDEX row and tracker tasks.
- When the user just asks a question, give a short, direct answer. Don't start changing things.
- Recommend one option rather than listing many.

## Keep it generic: no personal data in the repo
- Sortie is for anyone. Code, tests and docs must not contain the user's name, contact details, CTC, DOB, employer, home city, or the companies they applied to.
- Personal facts live only in git-ignored files: `config/profile.yaml`, `config/search.yaml`, `config/about.md`, `config/answers.yaml`, `config/.env`, `resumes/`, `data/`, `browser-profile/`. Ship `*.example` files instead (`sortie init` copies them).
- Behaviour comes from config (`education_aliases`, `home_country`, `work_authorization`, `skills_since`, …), never from hard-coded personal values.
- Tests use `tests/fixtures/` (the fake "Test Candidate", employer "Initech"), never `config/`.
- Docs use placeholders (Acme, "Your Name", `you@example.com`).

## Git
- The repo root is this `sortie/` folder; the outer `job-applier/` folder isn't in git.
- **Never add Claude as co-author or committer**: no `Co-Authored-By: Claude` and no "Generated with Claude Code" lines in commits or PRs.
- `doc/**/*.md` and `README.md` are part of the repo and are committed with related changes. Other `.md` files follow the global rule: ask first.
- Before committing, check with `git add -n .` that nothing personal or secret is staged: no API keys (`nvapi-`, `sk-…`), no personal emails or phones, no `/Users/...` paths.
- Commit or push only when the user asks.

## Answering rules (don't weaken these)
- Resolution order: profile rules → `config/answers.yaml` (exact match) → LLM (with the answer book in its prompt, so reworded questions reuse the user's answers) → ask the user.
- Experience is computed at runtime from dates (`career_start`, `skills_since`), never stored as numbers.
  - A skill **in** the profile → from its date.
  - A skill **not** in the profile → the LLM infers it from the resume and related skills, accepted at confidence ≥ 0.3 and capped at total experience.
- Salary, CTC, visa, authorization, criminal, disability, gender, caste, religion and legal questions are never answered by the LLM, unless it's reusing an answer the user gave themselves.
- LLM answers are typed into forms as-is: short and form-ready, never explanations. Conditional questions that don't apply get "Not applicable". LLM answers are never saved to `answers.yaml`; only the user's are.

## Site adapters
- One file per site in `src/sortie/sites/`, implementing `SiteAdapter` (`ensure_logged_in`, `search`, `get_details`, `apply`), registered in `sites/__init__.py`. A site that's built but not usable goes in `DISABLED` with the reason (Indeed, since 2026-11-09), and its blockers go in the tracker.
- Keep all selectors in the module's `SELECTORS` dict. Verify them against the live site and note "verified <date>". Use exact classes: substring selectors like `[class*='chatbot_Chip']` also match containers.
- Call `confirm(job, answers)` right before the irreversible click. On unknown page states, call `debug_dump()` (saves HTML and a screenshot to `data/debug/`) and fix from the dump.
- Naukri pre-fills chatbot text boxes from the user's Naukri profile, so clear them before answering.
- Naukri (Akamai "Access Denied"), Instahyre and Indeed (Cloudflare "Just a moment…") block headless Chrome: run them visible, and don't rapid-fire page visits to verify things.
- Use a site's own "easy apply" search filter when it has one (Indeed `iafilter=1`; LinkedIn's Easy Apply filter when that adapter is built). Company-site applying isn't supported yet, so external jobs are only noise. Keep the per-job external check as a fallback.
- Prefer a site's own JSON API for search when one exists (Instahyre: `/api/v1/job_search`; Indeed: the job-card JSON embedded in the results page); it's sturdier than page scraping.
- Explore public pages in a separate temporary browser profile. Never test the apply step on a real job without the user: clicking Apply submits an application.

## LLM
- `config/.env`: `LLM_MODEL` (LiteLLM `provider/model`; OpenAI-compatible services such as NVIDIA use `openai/<model>`), `LLM_API_KEY`, `LLM_API_BASE`, `LLM_THINKING=false` to disable reasoning on open models.
- Text only, no vision. Each request times out after 60 s with no retries. A failure must never abort a run: carry on unscored, or ask the user.

## Safety and operations
- Before moving or touching `browser-profile/` or `data/`, check that no Sortie run or bot Chrome is open (`pgrep -fl "browser-profile|sortie run"`).
- Never print or commit `config/.env` values; mask keys when inspecting.
- Respect the daily caps. Start new sites and flows in `--review` mode with `--limit 1-3`.
- Match the surrounding code: its comment density, naming and idioms. Use small modules and type hints, and keep comments to the "why".
