# Feature: Browser Session, Run Modes & Safety

**Status:** Done · **Phase:** 1 · **Code:** `src/sortie/browser.py`, `runner.py`, `terminal.py`, `cli.py`, `sites/base.py`

## Browser
- Playwright with a persistent profile in `browser-profile/` using the installed Chrome (`channel="chrome"`), with the automation flag disabled.
- You log in manually once per site (`sortie login <site>`, or when a run finds you logged out); no passwords stored.
- Visible by default; `headless:` in `search.yaml`, overridden by `--headless` / `--visible`.

## Run modes
| Mode | Behaviour |
|---|---|
| `--dry-run` | Full flow, shows answers, never submits, **writes nothing to the DB** (the real run sees the same jobs) |
| `--review` (default from `search.yaml`) | Shows job + all answers, asks `Submit? [y/N]`; "no" records the job as skipped |
| `--auto` | Submits without asking; still asks you for questions it can't answer safely |

## CLI
```
sortie run [--sites naukri,instahyre,cutshort] [--review|--auto|--dry-run] [--headless|--visible] [--limit N]
sortie login <site>
sortie report [--date YYYY-MM-DD]
```
Run from the project venv: `.venv/bin/sortie …`.

## Run loop (`runner.py`)
Per site: login check → daily cap check → search → skip seen jobs → pre-filter → open details → pre-filter again → LLM fit score → apply → record → pause.
- Errors on one job are recorded as `failed` with a screenshot; the run continues.
- `--limit N` caps this run; dry runs count a job once it reaches the submit step.

## Site adapter contract (`sites/base.py`)
- `ensure_logged_in`, `search` (async generator), `get_details`, `apply(job, answerer, confirm)`.
- Adapters call `confirm(job, answers)` right before clicking submit; the runner decides by mode.
- `check_blocked()` first waits up to 20 s for a Cloudflare "Just a moment…" check to clear, then raises `BlockedError` on captcha / "unusual activity" / "Access Denied" / security-verification / rate-limit pages, which stops that site for the run.
- `screenshot(tag)` saves to `data/screenshots/`.

## Logging (`log.py`)
- Console: one line per job, numbered per site: `[  4] ·  SKIPPED  Acme — Java Developer  [fit 55]`, then indented detail lines: the reason, each question asked with its answer and source (`profile`, `your answers`, `llm`, `user`), and the screenshot path for failures. Colour by outcome (green applied, cyan external, red failed, dim skipped/details, yellow warnings) when the terminal supports it; detail lines are cut to terminal width.
- Site header shows today's usage: `=== naukri · 12/40 applied today · up to 10 this run ===`; search progress lines show role · city · page → jobs found.
- Summary at the end with run time and counts per site, plus the path of the full log.
- Full log per run in `data/logs/run-<timestamp>.log`: timestamps, nothing truncated.
- Titles that repeat the company ("Java Developer @ Acme" at Acme) are shown without the repeat. LiteLLM's "Give Feedback / Get Help" banner is suppressed; LLM errors are one-line warnings.

## Safety
- Per-site daily caps from `search.yaml` (counted from the DB).
- Random 3–12 s pauses between jobs; `human_type` types with 40–120 ms per key.
