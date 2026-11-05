# Sortie

Sortie searches job portals for roles that fit you, scores each job against your resume, and applies, answering the screening questions from your own profile. It works with Naukri today. Instahyre, CutShort, LinkedIn, Indeed and company career sites are planned.

It applies selectively: every job goes through your filters and an optional LLM fit score. Each question is answered from your profile, then from your saved answers, then by the LLM, and you're asked only when none of those can answer. Salary, visa and legal questions are never made up.

## Requirements
- macOS / Linux, Python 3.11+
- Google Chrome installed (Sortie drives a separate Chrome profile; your normal browser is untouched)
- Optional: an LLM API key (Anthropic, OpenAI, Gemini, NVIDIA, any OpenAI-compatible endpoint, or local Ollama)

## Setup
```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/sortie init            # creates config/profile.yaml, config/search.yaml, config/.env
```
Then:
1. **`config/profile.yaml`**: your details. Experience is stored as **start dates** (`career_start`, `skills_since`) and computed on every run, so it never goes stale.
2. **`config/search.yaml`**: roles, cities, experience range, companies/keywords to exclude, daily caps.
3. **Resume**: put the PDF in `resumes/` and set `resume_pdf`.
4. **LLM (optional, recommended)** in `config/.env`:
   ```
   LLM_MODEL=anthropic/claude-haiku-5-5   # provider/model
   LLM_API_KEY=...
   LLM_API_BASE=                          # custom endpoint, e.g. https://integrate.api.nvidia.com/v1 (use openai/<model>)
   LLM_THINKING=                          # false = disable reasoning on open models (faster)
   ```
   Without an LLM, Sortie still runs on your profile rules and saved answers, and asks you everything else.
5. Log in once in Sortie's browser:
   ```bash
   .venv/bin/sortie login naukri
   ```

## Use
```bash
.venv/bin/sortie run --sites naukri --review --limit 3   # asks before each submit (start here)
.venv/bin/sortie run --sites naukri --auto               # applies on its own, within daily caps
.venv/bin/sortie run --sites naukri --dry-run            # never submits, records nothing
.venv/bin/sortie report                                  # today's counts
```
Add `--headless` to run without a visible window.

- Answers you type are saved to `config/answers.yaml`. They're reused for the same question and, via the LLM, for reworded ones. You can edit the file.
- Jobs that can only be applied to on the company's own site are recorded as `external` for a later phase.
- Logs: `data/logs/`. Screenshots and debug pages: `data/screenshots/`, `data/debug/`. History: `data/applier.db`.

## Privacy
Everything personal stays local and is git-ignored: `config/profile.yaml`, `search.yaml`, `answers.yaml`, `.env`, `resumes/`, `data/`, and `browser-profile/` (your site logins). No passwords are stored.

## Use responsibly
Automating applications may conflict with a site's terms of service and can get accounts restricted. Keep daily caps modest, start in `--review` mode, and only apply to jobs you'd actually take.

## Docs
Design, per-feature details and progress: [doc/INDEX.md](doc/INDEX.md).

## Tests
```bash
.venv/bin/pytest
```
Tests use a fixed test candidate in `tests/fixtures/`, not your config.
