# Feature: Wellfound Adapter

**Status:** ✅ Regular site since 2026-11-12 (runs with the other sites; daily cap `daily_caps.wellfound`, default 20). Search and apply verified live: 20 applications over 5 runs · **Phase:** 2 · **Code:** `src/sortie/sites/wellfound.py`

Wellfound (formerly AngelList Talent) lists startup jobs. Applying sends a note to the hiring team, much like CutShort.

## Search (verified 2026-11-12, logged out)
- Role pages exist for any role phrase:
  - `https://wellfound.com/role/l/<role-slug>/<city-slug>` for a city (e.g. `role/l/python-backend-developer/bangalore`, 9 jobs; `role/l/backend-engineer/bangalore`, 143);
  - `https://wellfound.com/role/r/<role-slug>` for remote jobs (used when `include_remote` is on).
- **Paging:** `?page=N`, about 30 jobs per page; page 2 had 26 new of 30. Sortie stops at a page with no new jobs, or after 5 pages.
- **Unknown role names** (verified 2026-11-12): Wellfound redirects a role slug it doesn't know (`role/l/swe-backend/bangalore`) to the general city page `/location/bangalore`. Sortie notices the redirect (the URL has no `/role/`), logs it once and skips that role on Wellfound for the rest of the run. Common titles (Backend Engineer, Java Developer, Full Stack Engineer) have role pages.
- **City slugs:** `bangalore` (for Bengaluru), `kolkata`, `jaipur`. Countries aren't searched.
- **Data:** the page embeds Apollo state in `__NEXT_DATA__` → `props.pageProps.apolloState.data`:
  - `JobListingSearchResult:<id>`: `title`, `slug`, `description` (full, markdown), `locationNames`, `remote`, `compensation` (often USD + equity, e.g. "$40k – $100k • 0.05% – 0.1%"), `yearsExperienceMin` / `Max`, `jobType`, `liveStartAt`, and `atsSource` (e.g. `AtsIntegration::Greenhouse::Listing`, or none).
  - `StartupResult:<id>`: `name`, `slug`, and `highlightedJobListings` (refs to its jobs), which is how a job gets its company.
- **Job page:** `https://wellfound.com/jobs/<id>-<slug>`.
- The full description is in the listing, so scoring needs no page visit.

## Filters
- The usual ones (experience from `yearsExperienceMin`/`Max`, location, title, fit).
- Non-full-time jobs (`jobType` other than `full-time`) are skipped.
- **Remote jobs closed to your country** are dropped at search time, before scoring or any page visit (verified 2026-11-12). The listing's `acceptedRemoteLocationNames` lists where remote hires may live (e.g. `["United States"]`, `["India", "Egypt"]`; empty means no limit). A job is dropped when that list names none of your `home_country` / search cities and none of its `locationNames` is one of your cities either. Wellfound refuses those applications anyway. The search log shows how many: "…, 12 not hiring from India".
- **`atsSource` (Greenhouse / Ashby / Dover):** the job was imported from the company's own hiring system. Whether Wellfound's Apply still works for these, or sends you to the company's site, is **to verify logged in**. Until then they're tried like the rest; a redirect away from Wellfound counts as `external`.

## Apply (verified live 2026-11-12)
1. Open the job page and wait for **Apply**. Already applied ("Applied") → `skipped`.
2. Apply opens a dialog. Sortie only ever fills **inside the dialog**; without one, nothing is filled.
3. The dialog must mention the job's title or company.
4. **The note to the hiring team** (a textarea): written per job by `Answerer.compose` from your resume, `about.md` and the description, the same as CutShort's message.
5. Other questions go through the form filler (profile → answers.yaml → LLM → you). Voice/video/file → needs-you. Unfamiliar steps → the LLM navigator, which can't press Apply or Submit.
6. Before sending: required fields must be filled, then `confirm()`, then the dialog's Send / Apply / Submit.
7. Success (verified 2026-11-12): the dialog switches to **"APPLICATION SCHEDULED"** and offers an optional AI video interview, which Sortie never starts. Sortie closes the dialog. A note box isn't always there; without one, Apply sends straight away.

## Safety
- Daily cap `daily_caps.wellfound` (default 20).
- Bot protection (Wellfound uses DataDome): the visible browser, the normal pace, `check_blocked`.

## Lessons from live runs
| Issue | Fix |
|---|---|
| First review run (2026-11-12): "apply dialog doesn't mention this job". The popup was still a spinner when checked, and the Help Scout chat widget is also `role=dialog`. Nothing was filled | The dialog must be modal (`aria-modal=true`, not Help Scout). Sortie waits up to 15 s for it to show a field or button before checking that it mentions the job. Company names are trimmed (a trailing space broke the check) |
| Second run: "send did not complete", but the application went through. The dialog switched to **"APPLICATION SCHEDULED"** (Wellfound's word for submitted) and offered an optional AI video interview. This dialog had no note box | "application scheduled" counts as success, and the dialog is then closed. Sortie never starts the AI interview (camera/voice) |
| "no apply button" on 3 jobs that the previous run had already sent (logged failed before "scheduled" was recognised): an applied job's page has no Apply button | Sortie reads your **Applied** list (`/jobs/applications`; rows are company, title, status) once per run and skips jobs on it before opening them. That includes jobs you applied to yourself |
| One job's popup said "Applications to this job are managed outside of Wellfound… Apply on website" | That text → `external` |
| One job's form: 3 required questions (GitHub URL, two long answers) were never read, and Send failed with "This question is required". Their `<label>` wraps the field but has `for="form-input--undefined"`, so the browser doesn't link it | Generic form filler: a field without a linked label takes the question text of the `<label>` around it; a trailing `*` marks it required |
| "What interests you about working for this company?" came to you with no suggestion. The model had replied in plain text, which was cut at 200 characters and dropped | Motivation questions go to `Answerer.compose`, which uses a writer prompt and accepts any usable reply, plain text in full. See `llm-matching-and-answers.md` |
| 7 jobs failed with "Locator.click: Timeout 30000ms": the popup said "<Company> is not accepting applications from your current location due to timezone or relocation constraints", and Send stayed disabled (e.g. a Jaipur job with relocation not allowed) | That text → `skipped` straight away, before the note is written. A disabled Send fails at once instead of after a 30 s timeout. The check uses the location in your Wellfound profile, so keep it current |
| "send did not complete". The required Yes/No question "authorized to work in the United States…" was never read: its radios sit in a `<label>`-wrapped `div`, not a `fieldset` or `role=radiogroup` | Generic form filler: radios outside any group are grouped by `name`. The question is the wrapping label's text, or the text just before the group. It's then answered from your profile (`work_authorization`) |
| Full remote run (2026-11-12): 46 of 213 jobs failed, almost all "Locator.click: Timeout" on US-only remote jobs. Each one also cost a fit score and a page visit | Search-time filter on `acceptedRemoteLocationNames` (above). A page whose jobs were all dropped still counts as a new page, so paging continues |
