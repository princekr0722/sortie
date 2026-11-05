# Feature: LLM Fit Scoring & Screening Answers

**Status:** Done · **Phase:** 1 · **Code:** `src/sortie/llm/` (`client.py`, `matcher.py`, `answerer.py`)

## Provider
- LiteLLM wrapper, configured in `config/.env` with three settings for every provider:
  ```
  LLM_MODEL=anthropic/claude-haiku-5-5   # provider/model
  LLM_API_KEY=...                        # key for that provider (empty for local Ollama)
  LLM_API_BASE=                          # optional custom endpoint; empty = provider default
  ```
- The prefix of `LLM_MODEL` picks the provider and its default URL (`anthropic/` → api.anthropic.com, `openai/` → api.openai.com, `ollama/` → localhost:11434, `openrouter/` → openrouter.ai). `LLM_API_BASE` overrides it, e.g. a proxy, LM Studio, a remote Ollama, or any OpenAI-compatible server (use `openai/<model>` with it).
- Text-only: no vision needed.
- `LLM_THINKING=false` (optional) turns off reasoning on open models behind OpenAI-compatible APIs (sends `chat_template_kwargs.enable_thinking=false`). With Nemotron 3.5 Lightning a fit score took 17 s / ~1,500 tokens with thinking (and >60 s under load) vs 2 s / ~60 tokens without, same score. Leave it unset for Anthropic / OpenAI models.
- Current setup: `LLM_MODEL=openai/nvidia/nemotron-3.5-lightning-30b-a3b`, `LLM_API_BASE=https://integrate.api.nvidia.com/v1`, `LLM_THINKING=false`.
- Provider not chosen yet. With no model configured, fit scoring is skipped and questions are answered from profile rules, saved answers, or by you.

## Fit scoring (`matcher.py`)
- Input: resume text (extracted from the PDF) + profile summary with computed durations + full JD.
- Output (JSON): `score` 0–100, `reason`, `missing_must_haves[]`.
- Scoring bands in the prompt: 80–100 core stack + seniority match; 65–79 mostly matching, at most one new secondary requirement; 40–64 role built around a tool/product/domain you haven't used (Polarion, Axiom, SAP, Salesforce…) even if it mentions Java; 0–39 different job family / wrong seniority. A missing core requirement caps the score at 60.
- Calibration on 2026-11-05 (Nemotron 3.5 Lightning, thinking off): niche-platform roles 55 (skipped); roles in your core stack 75 (pass). The model sometimes lists skills you do have as missing (REST APIs, TypeScript), which lowers scores of good matches; watch for good jobs landing just under 65.
- Below `min_fit_score` (65) → skipped, reason stored.
- Each LLM request times out after 60 s with no retries. If it fails (missing/bad key, provider down, model stalled), the job continues unscored / the question goes to you, and the error is printed.
- `LLM_MODEL` must start with a provider LiteLLM knows. For OpenAI-compatible services such as NVIDIA (`LLM_API_BASE=https://integrate.api.nvidia.com/v1`) prefix with `openai/`, e.g. `openai/nvidia/nemotron-3.5-lightning-30b-a3b`.
- NVIDIA check on 2026-11-05: `nemotron-3.5-lightning-30b-a3b` answered in ~8 s; `deepseek-v4.1-flash` and `gpt-oss-20b` stalled (no reply in 60–120 s); `kimi-k2.6` and `nemotron-3-super` returned 404 for this key.

## Screening answers (`answerer.py`)
Resolution order for each question:
1. **Profile rules** (regex on the question): notice period, current / expected CTC, relocation, visa sponsorship, work authorization, experience (total and per skill), current location, education, email, phone, name.
2. **Your answers** (`config/answers.yaml`): every answer you type is appended there (question, answer, options, date). Same wording (ignoring case/punctuation) → reused directly. The whole file is also sent to the LLM with every question, so **reworded questions get your answer** too (LLM sets `from_previous_answer`). The file is yours to edit: fix or delete an entry, or pre-fill answers; the bot only appends, so comments survive. LLM answers are **not** saved there, so a wrong guess never sticks.
3. **LLM**: profile + JD + question + options → `{answer, confidence}`. Used only when confidence ≥ 0.7.
4. **You**: the terminal shows the question, numbered options and the LLM's guess (Enter accepts it, `s` skips the job). Your answer is appended to `config/answers.yaml`.

### Rule details
- **Experience:** the skill is found by matching 1–3 word phrases against the profile with aliases (`spring boot`, `google cloud`, `node.js`, `golang`). Answers by field type: number → whole years; decimal → one decimal; choice → matching range (`3-5`, `3+`); text → "3 years 5 months"; Yes/No → Yes if any experience.
- "Total / IT / overall experience" → total from `career_start`.
- **Unknown skill** (not in `skills_since`) in an experience question → **the LLM answers first** (since 2026-11-05):
  - it gets your resume + listed skills and infers from related experience (JUnit / CI/CD via Java backend + Docker/Kubernetes work → 3; Angular / SAP ABAP with no evidence → 0);
  - accepted at confidence ≥ 0.3 (`MIN_SKILL_CONFIDENCE`); only "not sure at all" goes to you, with the LLM's guess as the suggestion;
  - numeric answers are capped at your total experience (an LLM "5" becomes 3).
- Yes/No experience questions with no known skill ("experience building REST APIs?") → LLM.
- **Money:** lakhs by default (e.g. `9.5`), rupees when the question says INR / ₹ / per annum without "lakh" (e.g. `1120000`). Expected CTC: text field → "min-max LPA", number/choice → `single_value`.
- **Notice:** choice → option with "30" or "1 month"; number → `30`; text → "30 days". "Are you serving notice?" → asks you (once; then `answers.yaml`).
- **Offers in hand** ("share details about any offer …") → asks you; never the CTC rule.
- An LLM reply that isn't JSON is offered to you as a suggestion, never auto-accepted.
- **Country** for visa / authorization questions: any country in `work_authorization` named in the question or job location (cities count), else `home_country`.

## Guardrails
- Questions about salary, CTC, visa, sponsorship, authorization, criminal record, disability, gender, caste, religion, bonds or legal matters are never answered by the LLM. If no rule covers them, you're asked.
- Experience in a skill **in** the profile always comes from its date, never from the LLM.
- Sensitive questions are answered by the LLM **only** when it reuses one of your own answers from `config/answers.yaml` (e.g. "Any criminal record?" → your earlier "No").
- LLM answers are typed into forms verbatim, so the prompt demands short, form-ready answers with no explanations; conditional questions that don't apply get "Not applicable".
- Choice answers must map to one of the offered options, otherwise the question goes to you.
- With no terminal attached (no `ask_user`), an unanswerable question skips the job.
