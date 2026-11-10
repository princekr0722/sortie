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
sortie run [--sites naukri,instahyre] [--review|--auto|--dry-run] [--headless|--visible] [--limit N] [--pace careful|normal|fast]
sortie login <site>
sortie report [--date YYYY-MM-DD]
```
Run from the project venv: `.venv/bin/sortie …`.

## Run loop (`runner.py`)
Per site: login check → `before_jobs` hook → daily cap check → search → skip seen jobs → pre-filter (card) → **title screen** → skip if known `external` → pause → open details → pre-filter again → skip if `external` → LLM fit score → apply → record → `after_jobs` hook.
- **Title screen** (`matcher.screen_title`): one short LLM call (~0.6 s) on the title alone, before the job page is opened. Skips only when the title clearly shows another job family (QA, mobile-only, sales…) or a platform you don't have (SAP, Salesforce, .NET, Polarion), with confidence ≥ 0.8; generic titles always pass. Cached per title for the run; a failure never blocks the job.
- **Company-site jobs aren't scored**: Instahyre marks them in search results, Naukri on the job page (`job.external`); they're recorded `external` straight away.
- Errors on one job are recorded as `failed` with a screenshot; the run continues.
- `--limit N` caps this run; dry runs count a job once it reaches the submit step.

## Site adapter contract (`sites/base.py`)
- `ensure_logged_in`, `search` (async generator), `get_details`, `apply(job, answerer, confirm)`.
- Adapters call `confirm(job, answers)` right before clicking submit; the runner decides by mode.
- `check_blocked()` first waits up to 20 s for a Cloudflare "Just a moment…" check to clear, then raises `BlockedError` on captcha / "unusual activity" / "Access Denied" / security-verification / rate-limit pages, which stops that site for the run.
- `screenshot(tag)` saves a JPEG (quality `SCREENSHOT_QUALITY` = 60, CSS-pixel scale, so not 2x on retina screens) to `data/screenshots/`: ~35 KB instead of ~170 KB as PNG.

## Logging (`log.py`)
- Console: one line per job, numbered per site, with applications so far / this run's target: `[  4 · 1/10 ✅] ·  SKIPPED  Acme — Java Developer  [fit 55]`, then indented detail lines: the reason, each question asked with its answer and source (`profile`, `your answers`, `llm`, `user`), and the screenshot path for failures. Colour by outcome (green applied, cyan external, red failed, dim skipped/details, yellow warnings) when the terminal supports it; detail lines are cut to terminal width.
- Site header shows today's usage: `=== naukri · 12/40 applied today · up to 10 this run ===`; search progress lines show role · city · page → jobs found.
- Summary at the end with run time and counts per site, plus the path of the full log.
- Full log per run in `data/logs/run-<timestamp>.log`: timestamps, nothing truncated.
- Titles that repeat the company ("Java Developer @ Acme" at Acme) are shown without the repeat. LiteLLM's "Give Feedback / Get Help" banner is suppressed; LLM errors are one-line warnings.

## Safety
- Per-site daily caps from `search.yaml` (counted from the DB).
- **Pace** (`pace:` in `search.yaml` or `--pace`): pause before each job page: `careful` 5–12 s, `normal` 2–6 s (default), `fast` 1–3 s. Search-page pauses scale with it. Faster pace = more jobs per minute, but looks less human.
- `human_type` types with 40–120 ms per key.

## Speed (2026-11-06)
Measured before: ~27–39 s per application plus ~12–14 s per job that ended as company-site or a low fit score (6 min for 3 applications in one run). Changes:
| Change | Saves |
|---|---|
| Pace `normal` 2–6 s (was 3–12 s) before each job page | ~4 s per job opened |
| Title screen before opening off-target jobs | ~12 s per off-target job |
| Company-site jobs not fit-scored | ~2 s LLM call each |
| Naukri: notice watch stops when the chatbot/confirmation appears (was a fixed 6 s) | up to ~6 s per application |
| Naukri: page checked every 0.5 s (was 2.5 s) | ~2 s per chatbot step |

## Windows (2026-11-10)
- Every file read and write passes `encoding="utf-8"`: configs, `about.md`, `answers.yaml`, the AI-roles cache and debug snapshots. Windows otherwise uses the ANSI code page and crashes on ₹, emoji and non-English names. `tests/test_portability.py` fails if a new file access leaves the encoding out.
- On Windows, stdout/stderr are switched to UTF-8 at startup (`cli.main`), so piped or redirected output with ✅ / 🤖 doesn't crash.
- No `readline` on Windows (no arrow-key editing in prompts); the CAPTCHA alert is a terminal beep only (the `osascript` notification is macOS).
- Not yet run end to end on Windows.

