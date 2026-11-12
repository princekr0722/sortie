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
- The scorer gets the resume, profile **and `about.md` notes**; every skill in any of them counts as one you have.
- It judges the **core** of the role (primary language, main framework, domain, seniority). Secondary tools are learnable and don't lower the score: CI/CD, scripting, a specific cloud, build/monitoring/testing tools, extra databases or queues. Only a missing core requirement caps the score at 60.
- Bands: 80–100 core + seniority match; 65–79 one core element adjacent or a small seniority stretch; 40–64 role built around a tool/product/domain you haven't used (Salesforce, SAP, Polarion…); 0–39 different job family / wrong seniority.
- Re-scored 2026-11-06 after this change: two Java/Spring Hooli roles and a Java + GenAI full-stack role went 55 → 75; a Salesforce/Apex role stayed low (10).
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
   - A reply that matches no option (a typo, or stray arrow-key codes like `^[[D`) makes Sortie **ask again**; only `s` skips the job. The prompt has line editing (`readline`), so arrow keys move the cursor.

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
- **Country** for visa / authorization questions: any country named in the question (an unlisted one, e.g. the United States or a US visa type, means not authorized), else a `work_authorization` country in the job location (cities count), else `home_country`. See [profile](profile-and-durations.md).

## Your notes (`config/about.md`)
- Free-form Markdown about you: projects, achievements, why you're looking, what you want. Created by `sortie init` from `config/about.example.md`; git-ignored.
- Sent to the LLM (with the resume) **only** for open text questions ("Describe a project…", "Why are you looking…") and for inferring experience in a skill missing from `skills_since`. Never for choice, number, CTC or notice questions, keeping those prompts small.
- Factual open questions ("describe your experience with X") get 2–4 first-person sentences built only from your notes and resume; if they don't cover it, low confidence → asked to you.
- **Free writing is always done by the LLM** (since 2026-11-12). This covers motivation questions ("What interests you about working for this company?", "Why this role?", "Why are you a good fit?", "Tell us about yourself", a cover letter) and the note to the hiring team (`Answerer.compose`).
  - It uses a writer prompt (`WRITE_SYSTEM`) that connects your resume and notes to this job and company, taking what the company does from the job description, with no invented facts.
  - Every reply is used whatever its confidence, including a long plain-text reply. Another try follows if a reply is unusable.
  - You're asked only if there's no LLM or both tries fail.
  - These answers skip answers.yaml in both directions: a good one names this company, so it's neither reused nor saved.
  - `is_open_ended()` decides which questions count. A text question matching words like why / interest / motivat / tell us / describe / good fit counts. Logistics wordings don't count (relocation, shifts, office, notice, joining dates, links/URLs), nor yes/no forms ("Are you interested in relocating?"), nor sensitive topics such as salary.
- Plain-text replies: the client keeps the whole reply (`NotJSON.reply`). It used to be cut at 200 characters, which silently dropped long answers and sent the question to you. JSON with a broken escape is handled the same way, so the parser's error text is never typed into a form.
- `<!-- comments -->` are dropped, the text is capped at 8,000 characters, and an **unedited copy of the template is ignored** (its sample projects aren't yours).

## Guardrails
- Questions about salary, CTC, visa, sponsorship, authorization, criminal record, disability, gender, caste, religion, bonds or legal matters are never answered by the LLM. If no rule covers them, you're asked.
- Experience in a skill **in** the profile always comes from its date, never from the LLM.
- Sensitive questions are answered by the LLM **only** when it reuses one of your own answers from `config/answers.yaml` (e.g. "Any criminal record?" → your earlier "No").
- LLM answers are typed into forms verbatim, so the prompt demands short, form-ready answers with no explanations; conditional questions that don't apply get "Not applicable".
- Choice answers must map to one of the offered options, otherwise the question goes to you.
- With no terminal attached (no `ask_user`), an unanswerable question skips the job.

## Lessons (2026-11-07, Indeed run)
| Issue | Fix |
|---|---|
| A plain-text LLM reply ("3 years 5 months, confidence: 0.9, from_previous_answer: false") became the suggestion as-is. Enter accepted it, and it was typed into the form and saved to answers.yaml | `plain_answer()` keeps only the answer part (an `answer:` field, or the text before `confidence` / `from_previous_answer` / `based_on`) |
| "total exp" and "Exp In java?" went to the LLM ("total exp → 4 to 7" was wrong) | The experience rule also matches the abbreviation `exp`, so they're computed from the profile |
| A date field (placeholder DD/MM/YYYY) got "30 days from offer acceptance" and the form refused it | New question kind `date`: a field is a date if it's `type=date` or its placeholder is a D/M/Y pattern; the format comes from the placeholder. "When can you start / join / be available" dates = today + `notice_period_days` (profile rule). Any other date answer must parse in that format, otherwise you're asked |
| "If Yes, please provide further details" → "no offer, answer yourself?": the LLM didn't know the previous question had been answered No | Questions carry the answers already given on the same application (`Question.context`). The LLM prompt lists them under EARLIER ANSWERS ON THIS APPLICATION, and conditional questions after a No get "Not applicable" |
| "What is your notice period? please mention your LWD If serving" was sent to you: any question mentioning "serving" asked you | The profile now records it: `serving_notice` (true/false) and `last_working_day` (YYYY-MM-DD). "Are you serving…?" → Yes/No from the profile. Notice-period questions: not serving → "30 days"; serving → "Serving notice, last working day 15 Dec 2026" (number questions get the days left). "Last working day?" → that date, or "Not applicable". Start/join dates → the day after your LWD when serving, else today + notice. "…if serving" is only a condition |
