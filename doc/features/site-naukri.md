# Feature: Naukri Adapter

**Status:** In progress (search, details, apply, success page and all chatbot question types verified live) · **Phase:** 1 · **Code:** `src/sortie/sites/naukri.py`

## Login
- `sortie login naukri` opens `naukri.com/nlogin/login` in the bot's Chrome; log in once, press Enter.
- Logged-in check: open `/mnjuser/homepage`; a redirect to `nlogin` or a visible login form means logged out.

## Search (verified 2026-11-05)
- URL: `/{role-slug}-jobs-in-{city}[-{page}]?k={role}&l={city}&experience={total years}&jobAge=7`.
  - `experience` = your total years, computed from `career_start` (Naukri shows jobs whose range includes it).
  - `jobAge=7` = posted in the last 7 days.
- Cities: any Indian city from `search.yaml`, slugged from its name (`CITY_SLUGS` covers exceptions: Bengaluru → `bangalore`, Gurugram → `gurgaon`). Foreign countries are skipped (Naukri is India only).
- Remote: a remote search URL returned non-remote jobs, so it isn't used; remote/hybrid jobs still pass the location filter when they appear.
- 20 cards per page; a page with fewer than 20 is the last (later pages ignore `jobAge`). Max 5 pages per role × city.
- Same job under several roles/cities is yielded once per run.
- Card fields: `data-job-id`, `a.title` (title + URL), `.comp-name`, `.expwdth`, `.sal` (often missing), `.locWdth` (comma-separated, may start with "Hybrid - ").

## Job page (verified 2026-11-05)
- JD: `[class*='JDC__dang-inner-html']`; key skills appended to the JD for fit scoring.
- Salary: `[class*='jhc__salary__'] span` ("Not Disclosed" → blank).
- Apply state: `#apply-button` (text "Apply"), company-site button (→ `external`), "Applied" (→ skipped).

## Apply
1. Already applied → skipped. Company-site button → `external` (phase 3 queue). No button → failed + debug dump.
2. **Confirm before clicking Apply**: Naukri submits on click (or when the chatbot finishes), so review mode asks here, with the job shown but no answers yet.
3. Click Apply, then loop (max 20 steps, 2.5 s apart):
   - success → `applied` + screenshot. Verified: Naukri navigates to a page titled **"Apply Confirmation"** with `.applied-job-content` ("Applied to …").
   - chatbot question → read the last bot message + radio / checkbox / chip options → answerer → click option or type into the text box → send.
     Options are split on new lines (one element can hold the whole list) and "Load more" is dropped; the option is clicked by its exact text inside the chatbot.
   - unanswerable question → close chatbot, `skipped` with the question as reason, debug dump.
4. No success after 20 steps → `failed` + debug dump. If the chatbot never appeared, the reason is "Apply did nothing"; 2 of those in a row stop Naukri for the run (likely a daily/company limit), and any notice Naukri flashed is included.

**Pre-filled text box:** Naukri fills the chatbot text box from your Naukri profile (e.g. your Naukri profile's city for "What is your current location?"). Left there, it is sent together with the clicked option ("Bengaluru / Bangalore, <profile city>"), and typed answers get appended to it. The bot now clears the box (select all + Backspace) before every answer. The chatbot also says "Your profile will be updated with the details that you provide", so answers can change your Naukri profile.

Field type for chatbot text boxes: CTC / lakh / salary → decimal; "how many / years / days" → number; else text.

**Chatbot markup (verified 2026-11-05):** drawer `.chatbot_DrawerContentWrapper`; question = last `.chatbot_ListItem .botMsg`; single choice = `.ssrc__radio-btn-container` (Yes / No / "Skip this question" — the skip option is dropped); chips `.chatbot_Chip`; text box `div.textArea[contenteditable]`; submit `.sendMsg` ("Save", disabled until answered); close `.chatbot_Nav .crossIcon`. Use exact classes: `[class*='chatbot_Chip']` also matches the `.chatbot_Chips` container and reads every option as one. Multi-select = `.multicheckboxes-container` with `input.mcc__checkbox` + `label.mcc__label`. Save = `.sendMsgbtn_container` (clickable), `.send.disabled` until answered. `DUMP_CHATBOT = True` still saves each question to `data/debug/`; switch it off once a few runs are clean.

### Lessons from live runs (2026-11-05)
| Issue | Fix |
|---|---|
| Success not detected; after applying Naukri navigates to "Apply Confirmation" | Detect that page title / `.applied-job-content` |
| Option list read as one string (`[class*='chatbot_Chip']` also matched the `.chatbot_Chips` container) | Exact class selectors, split options on new lines, click by exact text |
| Radio answer not clicked → Save stayed disabled, job failed | Click the option's own text inside the chatbot; failed jobs are retried once |
| Location sent as the chosen city **plus** Naukri's pre-filled profile city | Clear the text box before every answer |
| 17 of 19 failures: clicking Save timed out ("`.sendMsgbtn_container` intercepts pointer events"); some had already navigated to `/myapply/saveApply` (submitted) | Click the outer `.sendMsgbtn_container`, wait until `.send` loses `disabled`; a click error only counts if the apply didn't go through; `/myapply/saveApply` counts as success |
| 2 failures: multi-select questions (checkboxes) read as text, then waited for a hidden text box | Checkbox options are `.multicheckboxes-container .mcc__label` |
| "Execution context was destroyed" while reading the page: Naukri navigated to the apply confirmation mid-check, so a real success was recorded as failed | Page checks tolerate navigation and look again; `/myapply/saveApply` or the confirmation then counts as applied |
| Headless Chrome gets an Akamai "Access Denied" page on job pages | Run Naukri visible (default); `check_blocked` stops the site when it sees this |
| After ~45–50 applications in a day, clicking Apply did nothing on 5 jobs in a row (no chatbot, no confirmation, no message left on the page); the first job at the same company had just gone through | Likely Naukri's daily cap (~50) or a per-company limit. A notice shown within ~6 s of clicking is now captured into the reason; after 2 no-effect clicks in a row Naukri is stopped for the run (`MAX_NO_EFFECT`) |
| "Locator.click: Timeout 30000ms" and "option 'Yes' not found" (2026-11-08/09): the chatbot's "…" typing bubble is itself a `.botMsg` (`li.chatbot_ListItem.loader`). Sortie read it as the new question before the real question and its options (a new `singleselect-radiobutton-container` layout) had rendered, found no options, and tried the hidden text box | Messages exclude `li.loader`, and Sortie waits while a typing bubble is visible before reading a question |
| The chatbot answered "Something went wrong. Please Try again later", and that was read as the next question | That message ends the job as `failed: Naukri chatbot said: …` with a debug dump |
| Walk-in drives ("Walk-in \|\| …") have **I am interested** instead of Apply, plus a Time and Venue block: "no apply button" | Recorded as `skipped: walk-in drive, in person: <time> · <venue>`. Sortie doesn't register interest in in-person drives; they're left for you |

## Dry run on Naukri
`--dry-run` stops before clicking Apply, so it shows which jobs would be applied to but **can't preview chatbot questions** (they only appear after clicking).

## Extras (later)
- Optional daily profile refresh to stay near the top of recruiter searches.
